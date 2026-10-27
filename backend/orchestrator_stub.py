"""Connects the stub backend to backend/orchestrator.py when AGENT_ORCH=on. No verdict logic here: the cards come from
stub_server's existing builders (demo_card, real_card, _live_card), and the agents are the stub's own findings plus the
real Deal Agent when AGENT_DEAL=real and the real Trust Agent when AGENT_TRUST=real. With Trust real, its findings take
the place of the scripted trust rows and trust.safe_verdict() sets the card's "Shop safe?" section, as stub_server's
_real_trust/_with_trust do without the orchestrator.

AGENT_PRICE=real: the Price Agent (backend/agents/price.py) runs as one more agent in place of the scripted price rows,
its best_price finding gets the exact-match ref (backend/rechecker.py adapt), and the Skeptic re-checks every best_price
with backend/rechecker.py. The card's best price comes only from Skeptic-confirmed best_price findings; after the run
the findings are stored like stub_server does (DATA_SOURCE=zetaris only; a failure is a log warning). Otherwise there
is no rechecker and the Skeptic applies only its evidence rule.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator, Optional

from backend import orchestrator
from backend.contracts import FinalCard, Finding, PageFacts

log = logging.getLogger("pricise.orchestrator")


def _deal_timeout_s() -> float:
    from backend.tools.timer_test import timeout_s

    return max(orchestrator.AGENT_TIMEOUT_S, timeout_s() + 10)


def _named(fn, name: str):
    fn.agent_name = name
    return fn


def stub_agents(chk: dict, done: set[str]) -> list[orchestrator.Agent]:
    """The scripted/heuristic findings, paced as today, plus the real Deal Agent when AGENT_DEAL=real and the real Trust
    Agent when AGENT_TRUST=real. done collects the Deal tools that have finished, for _live_card."""
    from backend import stub_server as s

    cid, page, real = chk["check_id"], PageFacts(**chk["page"]), s._deal_mode() == "real"
    trust_real, price_real = s._trust_mode() == "real", s._price_mode() == "real"
    scripted = s.demo_findings(cid) if chk["demo"] else s.real_findings(cid, page)
    delay = s.DEMO_DELAY if chk["demo"] else 0.18
    replaced = {"deal"} if real else set()
    if trust_real:
        replaced.add("trust")
    kept = [f for f in scripted if f.agent not in replaced and not (price_real and s._scripted_price(f))]

    async def page_agent(_chk: dict) -> AsyncIterator[Finding]:
        async for f in s._paced(kept, delay):
            f.ts = time.time()
            yield f

    async def trust_agent(_chk: dict) -> AsyncIterator[Finding]:
        from backend.agents import trust

        tv = await trust.assess_store(chk["url"], cid, page)
        chk["trust_verdict"] = tv  # for the card's "Shop safe?" section
        chk["evidence_data"]["store_checks"] = tv.get("checks")  # the evidence page's "Store checks" table
        for f in tv["findings"]:
            yield f

    async def deal_agent(_chk: dict) -> AsyncIterator[Finding]:
        from backend.agents import deal

        try:
            async for f in deal.iter_deal(cid, chk["url"], page, fresh=chk.get("fresh", False),
                                          on_tool_done=lambda name, _secs: done.add(name)):
                yield f
        finally:  # keep the evidence page's data, then free it, as _with_real_deal does
            chk["evidence_data"].update(deal.EVIDENCE.pop(cid, {}))

    async def price_agent(_chk: dict) -> AsyncIterator[Finding]:
        from backend import rechecker
        from backend.agents import price

        # propose_lookalikes: cheaper listings of another model or condition become best_price proposals, so the
        # Skeptic (not the Price Agent) decides "same item?" and rejects them with its rule.
        res = await price.assess_async(cid, chk["url"], page, s.CLOCK["day"], use_trust=trust_real,
                                       propose_lookalikes=True)
        s.PRICE[cid] = res  # for _apply_price, as stub_server's _with_price does
        chk["evidence_data"]["stores"], chk["evidence_data"]["stores_sql"] = res.stores, res.sql
        chk["evidence_data"]["online"] = res.online
        for f in await asyncio.to_thread(rechecker.adapt, res, page, chk["url"]):
            yield f

    return ([_named(page_agent, "page")] + ([_named(deal_agent, "deal")] if real else [])
            + ([_named(trust_agent, "trust")] if trust_real else []) + ([_named(price_agent, "price")] if price_real else []))


def rechecker_for(chk: dict) -> Optional[orchestrator.Rechecker]:
    """The Skeptic's rechecker: best_price re-checks with AGENT_PRICE=real, none otherwise (evidence rule only)."""
    from backend import stub_server as s

    if s._price_mode() != "real":
        return None
    from backend.rechecker import Rechecker

    return Rechecker(chk)


def card_builder(chk: dict, done: set[str] | None = None) -> orchestrator.CardBuilder:
    """The card check_events would build today, from the latest version of each finding."""
    from backend import stub_server as s

    done = set() if done is None else done
    page = PageFacts(**chk["page"])

    def build(chk: dict, findings: list[Finding]) -> FinalCard:
        cid = chk["check_id"]
        if s._deal_mode() == "real":
            from backend.agents import deal

            card = s._live_card(cid, chk, page, findings, done, deal)
        else:
            elapsed = time.time() - chk["started"]
            card = s.demo_card(cid, elapsed) if chk["demo"] else s.real_card(cid, page, findings, elapsed)
        if chk.get("trust_verdict") is not None:  # set by trust_agent (AGENT_TRUST=real) once the store is vetted
            from backend.agents import trust

            card.safe = trust.safe_verdict(chk["trust_verdict"])
        card = s._safe_headline(card)  # the headline follows the final "Shop safe?" section
        if s._price_mode() == "real":
            from backend.rechecker import best_price_on_card

            card = s._apply_price(cid, card)  # the Price Agent's verdict, once it has answered
            card.best_price, card.alt = best_price_on_card(chk, findings, card.best_price, card.alt)
        return card

    return build


async def store_findings(chk: dict) -> None:
    """Once per check, after the run: stub_server._store_findings (DATA_SOURCE=zetaris only). Never raises."""
    from backend import stub_server as s

    if chk.get("findings_store_tried"):
        return
    chk["findings_store_tried"] = True
    try:
        await s._store_findings(chk)  # catches PgError / ValueError itself and logs a warning
    except Exception as err:
        log.warning("orchestrator check=%s findings not stored: %s", chk["check_id"], type(err).__name__)


async def sse_stream(chk: dict) -> AsyncIterator[str]:
    """check_events' text/event-stream body, from the orchestrator."""
    done: set[str] = set()
    await orchestrator.clean_page(chk)  # the stub agents quote page text: clean it before they are created
    async for ev in orchestrator.run_check(chk, stub_agents(chk, done), rechecker_for(chk),
                                           build_card=card_builder(chk, done), agent_timeout_s=_deal_timeout_s()):
        body = ({"kind": "finding", "finding": ev["finding"].model_dump()} if ev["kind"] == "finding"
                else {"kind": "final_card", "card": ev["card"].model_dump()})
        yield f"data: {json.dumps(body)}\n\n"
    await store_findings(chk)
