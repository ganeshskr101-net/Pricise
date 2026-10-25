"""Connects the stub backend to backend/orchestrator.py when AGENT_ORCH=on. No verdict logic here: the cards come from
stub_server's existing builders (demo_card, real_card, _live_card), and the agents are the stub's own findings plus the
real Deal Agent when AGENT_DEAL=real and the real Trust Agent when AGENT_TRUST=real. With Trust real, its findings take
the place of the scripted trust rows and trust.safe_verdict() sets the card's "Shop safe?" section, as stub_server's
_real_trust/_with_trust do without the orchestrator. No rechecker yet, so the Skeptic applies only its evidence rule.
"""
from __future__ import annotations

import json
import time
from typing import AsyncIterator

from backend import orchestrator
from backend.contracts import FinalCard, Finding, PageFacts


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
    trust_real = s._trust_mode() == "real"
    scripted = s.demo_findings(cid) if chk["demo"] else s.real_findings(cid, page)
    delay = s.DEMO_DELAY if chk["demo"] else 0.18
    replaced = {"deal"} if real else set()
    if trust_real:
        replaced.add("trust")

    async def page_agent(_chk: dict) -> AsyncIterator[Finding]:
        async for f in s._paced([f for f in scripted if f.agent not in replaced], delay):
            f.ts = time.time()
            yield f

    async def trust_agent(_chk: dict) -> AsyncIterator[Finding]:
        from backend.agents import trust

        tv = await trust.assess_store(chk["url"], cid, page)
        chk["trust_verdict"] = tv  # for the card's "Shop safe?" section
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

    return ([_named(page_agent, "page")] + ([_named(deal_agent, "deal")] if real else [])
            + ([_named(trust_agent, "trust")] if trust_real else []))


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
        return card

    return build


async def sse_stream(chk: dict) -> AsyncIterator[str]:
    """check_events' text/event-stream body, from the orchestrator."""
    done: set[str] = set()
    await orchestrator.clean_page(chk)  # the stub agents quote page text: clean it before they are created
    async for ev in orchestrator.run_check(chk, stub_agents(chk, done), None, build_card=card_builder(chk, done),
                                           agent_timeout_s=_deal_timeout_s()):
        body = ({"kind": "finding", "finding": ev["finding"].model_dump()} if ev["kind"] == "finding"
                else {"kind": "final_card", "card": ev["card"].model_dump()})
        yield f"data: {json.dumps(body)}\n\n"
