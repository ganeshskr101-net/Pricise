"""Pricise stub backend: a fake version of the real orchestrator, so the extension UI works before the agents exist.

Run:  python -m uvicorn backend.stub_server:app --port 8787 --reload   (from the repo root)

- Store Lab hero page (/storelab/audiodealz/): streams the scripted demo (3 tricks, real total $191, Store C $229).
- Any other page (real stores): turns the signals the extension read from the page into careful, evidence-backed findings.
  Trust (domain age etc.) and Price (other stores) are marked "not connected yet" until Raj's and Josh's agents land.
Nothing here calls a model. The real backend replaces these functions with agents that go through Meterless Nightshift.

AGENT_DEAL=real streams the real Deal Agent (backend/agents/deal.py) in place of the scripted/heuristic deal findings;
AGENT_DEAL=stub (default) keeps the scripted demo exactly as it was.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.contracts import (
    BestPrice,
    CheckRequest,
    Evidence,
    FinalCard,
    Finding,
    PageFacts,
    Reason,
    Verdict,
    WishlistItem,
)

from backend.env import load_dotenv

load_dotenv()  # before anything reads settings; shell variables still win, no .env is fine

from backend.agents import chat as chat_agent  # noqa: E402
from backend.agents.chat import ChatRequest, ChatResponse  # noqa: E402  (proposed for backend/contracts.py)

ROOT = Path(__file__).resolve().parent.parent
@asynccontextmanager
async def lifespan(_app):
    """With the real Deal Agent, launch the hidden browser once at startup so no check pays a cold start."""
    from backend.tools import browser
    if _deal_mode() == "real":
        try:
            await browser.warm()
        except Exception as err:  # no Playwright/Chromium: the Deal Agent reports "couldn't check" per tool instead
            logging.getLogger("pricise").warning("hidden browser not started: %s", err)
    yield
    await browser.shutdown()


app = FastAPI(title="Pricise stub backend", version="0.1", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/storelab", StaticFiles(directory=ROOT / "storelab", html=True), name="storelab")
(ROOT / "traces").mkdir(exist_ok=True)
app.mount("/traces", StaticFiles(directory=ROOT / "traces"), name="traces")  # Deal Agent screenshots (timer test, run-ahead)
app.mount("/bench", StaticFiles(directory=ROOT / "bench", html=True), name="bench")  # labelled bench pages (made-up stores)

# Timing logs from the agents ("pricise.*" loggers) show up in the uvicorn console.
_log = logging.getLogger("pricise")
if not _log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(name)s: %(message)s", "%H:%M:%S"))
    _log.addHandler(_h)
    _log.setLevel(logging.INFO)


CHECKS: dict[str, dict[str, Any]] = {}
DEMO_DELAY = 0.55  # seconds between streamed findings on the demo page

# ----------------------------------------------------------------------------- helpers


def _f(check_id: str, agent: str, type_: str, status: str, summary: str, evidence=None, **kw) -> Finding:
    return Finding(
        check_id=check_id, agent=agent, type=type_, status=status, summary=summary,
        evidence=[Evidence(**e) for e in (evidence or [])], ts=time.time(),
        model=kw.pop("model", "stub (no model call)"), **kw,
    )


def _is_demo(url: str) -> bool:
    return "/storelab/audiodealz" in url


def _deal_mode() -> str:
    return "real" if os.environ.get("AGENT_DEAL", "stub").lower() == "real" else "stub"


# ----------------------------------------------------------------------------- demo script (AudioDealz)


def demo_findings(cid: str) -> list[Finding]:
    return [
        _f(cid, "page", "product", "confirmed", "Read the page: Sony WH-1000XM5, listed at $149 (\"was $249\").",
           [{"kind": "selector", "ref": "#product-title"}], confidence=0.99),
        _f(cid, "deal", "fake_discount", "proposed",
           "\"40% off\" is not real: the price was $149 for 81 of the last 90 days. $249 appeared for 2 days only.",
           [{"kind": "selector", "ref": "#deal-badge", "label": "Fake 40% off"}, {"kind": "price_row", "ref": "history:audiodealz"}],
           confidence=0.93, model="nemotron fast lane (stub)"),
        _f(cid, "deal", "resetting_timer", "confirmed",
           "The \"deal ends\" timer restarts at 09:59 on every visit (seen on 3 visits).",
           [{"kind": "selector", "ref": "#timer", "label": "Timer resets"}, {"kind": "screenshot", "ref": "replay:3-visits"}],
           confidence=0.97, model="nemotron fast lane (stub)"),
        _f(cid, "deal", "prechecked_addon", "confirmed", "A $4.99 protection plan is ticked for you.",
           [{"kind": "selector", "ref": "#addon-row", "label": "Pre-ticked $4.99"}], confidence=0.99),
        _f(cid, "trust", "domain_age", "confirmed", "The domain was registered 6 days ago.",
           [{"kind": "log", "ref": "whois:audiodealz"}], confidence=0.95),
        _f(cid, "trust", "no_company", "confirmed", "No address or company record found for this store.",
           [{"kind": "log", "ref": "registry:audiodealz"}], confidence=0.85),
        _f(cid, "trust", "copied_photos", "proposed", "Product photos are copied from another site.",
           [{"kind": "url", "ref": "reverse-image:audiodealz"}], confidence=0.8),
        _f(cid, "deal", "drip_fees", "confirmed",
           "$37.01 in fees appear only at checkout step 4 (service $22.00 + processing $15.01). Real total: $191.00.",
           [{"kind": "screenshot", "ref": "runahead:step4"}, {"kind": "log", "ref": "fees:audiodealz"}],
           confidence=0.98, model="hidden browser run-ahead (stub)"),
        _f(cid, "price", "candidate", "rejected", "Store D, $189: blocked by Trust Agent (store looks like a scam).",
           [{"kind": "url", "ref": "storelab:store-d"}], challenged_by="trust", rounds=1),
        _f(cid, "price", "candidate", "rejected", "Store B, $199: refurbished, not the same item. Rejected by Skeptic.",
           [{"kind": "url", "ref": "storelab:store-b"}], challenged_by="skeptic", rounds=2, model="nemotron deep lane (stub)"),
        _f(cid, "skeptic", "best_price", "confirmed",
           "Store C, $229: exact match (new, same model), $255 minus a $26 card offer. Survived 2 challenges.",
           [{"kind": "url", "ref": "storelab:store-c"}, {"kind": "price_row", "ref": "offers:store-c"}],
           rounds=2, model="nemotron deep lane (stub)", confidence=0.94),
    ]


def demo_card(cid: str, elapsed: float) -> FinalCard:
    return FinalCard(
        check_id=cid, title="Don't buy here", subtitle="Risky store · 3 tricks · real total $191, not $149", tone="bad",
        tricks=3, elapsed_s=round(elapsed, 1),
        alt={"store": "Store C", "total": 229, "url": "http://localhost:8787/storelab/store-c/"},
        safe=Verdict(status="bad", headline="Risky", reasons=[
            Reason(text="Domain registered 6 days ago", tone="bad", proof="whois"),
            Reason(text="No address or company record", tone="bad", proof="whois"),
            Reason(text="Product photos copied from another site", tone="caution", proof="whois"),
        ]),
        honest=Verdict(status="bad", headline="3 tricks", reasons=[
            Reason(text="Fake 40% off: it was $149 for 81 of 90 days", tone="bad", proof="chart"),
            Reason(text="Timer resets on every visit", tone="bad", proof="replay"),
            Reason(text="$37 in fees appear at checkout step 4", tone="bad", proof="fees"),
        ]),
        best_price=BestPrice(status="good", headline="Store C · $229", store="Store C", total=229,
                             url="http://localhost:8787/storelab/store-c/", reasons=[
            Reason(text="Exact match: same model, new, 12-month warranty", tone="good", proof="stores"),
            Reason(text="$255 minus a $26 card offer, free shipping", tone="good", proof="stores"),
            Reason(text="Skipped Store B (refurbished) and Store D (scam)", tone="caution", proof="stores"),
        ]),
    )


def demo_evidence() -> dict:
    history = [149.0] * 90
    history[40] = history[41] = 249.0
    for i in range(80, 90):
        history[i] = 149.0
    return {
        "price_history": {"store": "AudioDealz", "label": "Synthetic history (Store Lab)", "points": history,
                          "claimed_was": 249.0, "days_at_claim": 2, "days_at_price": 81},
        "timer_frames": [{"visit": 1, "time": "Mon 09:12", "shown": "09:59"},
                         {"visit": 2, "time": "Mon 14:40", "shown": "09:59"},
                         {"visit": 3, "time": "Tue 08:05", "shown": "09:59"}],
        "fees": [{"label": "Item", "amount": 149.00, "step": 1},
                 {"label": "Protection plan (pre-ticked)", "amount": 4.99, "step": 1},
                 {"label": "Service fee", "amount": 22.00, "step": 4},
                 {"label": "Processing fee", "amount": 15.01, "step": 4}],
        "total": 191.00,
        "stores": [{"store": "Store C", "total": 229, "note": "$255 − $26 card offer · new · free shipping", "status": "chosen"},
                   {"store": "Store A", "total": 252, "note": "$239 + $13 shipping · new", "status": "ok"},
                   {"store": "Store B", "total": 199, "note": "Refurbished: not the same item", "status": "rejected"},
                   {"store": "Store D", "total": 189, "note": "Scam signals: blocked by Trust Agent", "status": "rejected"},
                   {"store": "Store E", "total": None, "note": "Site down during check", "status": "skipped"}],
    }


# ----------------------------------------------------------------------------- real pages (heuristics only)

TRICK_TYPES = {"prechecked_addon", "resetting_timer", "drip_fees"}


def real_findings(cid: str, page: PageFacts) -> list[Finding]:
    out: list[Finding] = []
    p = page.product
    if p.name or p.price:
        price = f"${p.price:,.2f}" if p.price is not None else "price not found"
        model = f" · model {p.model}" if p.model else ""
        out.append(_f(cid, "page", "product", "confirmed", f"Read the page: {p.name or 'Unnamed product'}, {price}{model}.",
                      [{"kind": "text", "ref": p.source or "page"}], confidence=0.9))
    seen_sel: set[str] = set()
    for s in page.signals:
        if s.selector and s.selector in seen_sel:
            continue
        if s.selector:
            seen_sel.add(s.selector)
        sel = [{"kind": "selector", "ref": s.selector, "label": _label(s)}] if s.selector else []
        if s.type == "claimed_discount":
            out.append(_f(cid, "deal", "claimed_discount", "unverified",
                          f"The page claims a reduced price ({s.text.strip()[:80]}). Price history not checked yet, so this is unverified.",
                          sel, confidence=0.4))
        elif s.type == "countdown":
            out.append(_f(cid, "deal", "countdown", "proposed",
                          f"Countdown timer on the page ({s.text.strip()[:40]}). Pricise will check if it resets on your next visit.",
                          sel, confidence=0.5))
        elif s.type == "resetting_timer":
            out.append(_f(cid, "deal", "resetting_timer", "confirmed",
                          "This countdown restarted since your last visit, so the deadline is not real.", sel, confidence=0.85))
        elif s.type == "prechecked_addon":
            amt = f" (${s.amount:,.2f})" if s.amount else ""
            out.append(_f(cid, "deal", "prechecked_addon", "confirmed",
                          f"An add-on is ticked for you{amt}: \"{s.text.strip()[:70]}\".", sel, confidence=0.8))
        elif s.type == "scarcity":
            out.append(_f(cid, "deal", "scarcity", "proposed",
                          f"Pressure message: \"{s.text.strip()[:70]}\". Stock claims can't be verified from the page.", sel, confidence=0.5))
        elif s.type == "drip_fees":
            continue  # grouped into one finding below
    fees = [s for s in page.signals if s.type == "drip_fees" and s.amount]
    if fees:
        total_fees = round(sum(s.amount for s in fees), 2)
        names = ", ".join(_fee_name(s.text) for s in fees)
        ev = [{"kind": "selector", "ref": s.selector, "label": "Extra fees" if i == 0 else "", "placement": "right"}
              for i, s in enumerate(fees) if s.selector]
        base = f" on top of ${p.price:,.2f}" if p.price is not None else ""
        out.append(_f(cid, "deal", "drip_fees", "confirmed", f"${total_fees:,.2f} in extra fees{base} ({names}).", ev, confidence=0.8))
    if not page.https:
        out.append(_f(cid, "trust", "no_https", "confirmed", "This page is not using a secure connection (no HTTPS).", confidence=0.99))
    out.append(_f(cid, "trust", "not_connected", "unverified",
                  f"Store checks for {page.host or 'this site'} (domain age, company record) need the Trust Agent, not connected yet."))
    out.append(_f(cid, "price", "not_connected", "unverified",
                  "Other-store price search needs the Price Agent, not connected yet."))
    return out


def _fee_name(text: str) -> str:
    m = re.search(r"([A-Za-z ]*?fee)", text, re.I)
    return (m.group(1).strip().lower() if m else "fee")


def _label(s) -> str:
    return {"claimed_discount": "Discount claim", "countdown": "Countdown", "resetting_timer": "Timer resets",
            "prechecked_addon": "Pre-ticked add-on", "scarcity": "Pressure message", "drip_fees": "Extra fee"}.get(s.type, s.type)


def real_card(cid: str, page: PageFacts, findings: list[Finding], elapsed: float) -> FinalCard:
    tricks = [f for f in findings if f.type in TRICK_TYPES and f.status != "unverified"]  # "couldn't check" is not a trick
    soft, seen_types = [], set()
    for f in findings:  # one line per kind of claim, so the card stays short
        if f.type in {"claimed_discount", "countdown", "scarcity"} and f.type not in seen_types:
            seen_types.add(f.type)
            soft.append(f)
    honest_reasons = [Reason(text=f.summary, tone="bad" if f.type in TRICK_TYPES else "caution",
                             proof="fees" if f.type == "drip_fees" else None) for f in tricks + soft][:4]
    if not honest_reasons:
        honest_reasons = [Reason(text="No timers, pre-ticked add-ons or extra fees found on this page", tone="good")]
    n = len(tricks)
    honest = Verdict(status="bad" if n else ("caution" if soft else "good"),
                     headline=f"{n} trick{'s' if n != 1 else ''}" if n else ("Check these" if soft else "Looks honest"),
                     reasons=honest_reasons)
    safe_reasons = [Reason(text="Secure connection (HTTPS)" if page.https else "No secure connection (HTTPS)",
                           tone="good" if page.https else "bad"),
                    Reason(text="Domain age and company record: Trust Agent not connected yet", tone="unknown")]
    safe = Verdict(status="unknown" if page.https else "bad", headline="Not fully checked" if page.https else "Risky",
                   reasons=safe_reasons)
    p = page.product
    bp_reasons = []
    if p.name:
        bp_reasons.append(Reason(text=f"Product: {p.name[:70]}", tone="unknown"))
    if p.price is not None:
        bp_reasons.append(Reason(text=f"Price on this page: ${p.price:,.2f}", tone="unknown"))
    bp_reasons.append(Reason(text="Other-store search: Price Agent not connected yet", tone="unknown"))
    best = BestPrice(status="unknown", headline="Search not connected", reasons=bp_reasons)
    if n:
        title, tone = "Be careful here", "caution"
    elif soft:
        title, tone = "Check before you buy", "caution"
    else:
        title, tone = "No tricks spotted", "good"
    sub = f"{n} trick{'s' if n != 1 else ''} · {len(soft)} claim{'s' if len(soft) != 1 else ''} to check"
    extras = sum(s.amount or 0 for s in page.signals if s.type in {"drip_fees", "prechecked_addon"})
    if p.price is not None and extras:
        sub = f"{n} trick{'s' if n != 1 else ''} · real total ${p.price + extras:,.2f}, not ${p.price:,.2f}"
    return FinalCard(check_id=cid, title=title, subtitle=sub, tone=tone, safe=safe, honest=honest, best_price=best,
                     tricks=n, elapsed_s=round(elapsed, 1))


def real_evidence(page: PageFacts) -> dict:
    fees = [{"label": s.text[:60], "amount": s.amount, "step": None} for s in page.signals if s.type == "drip_fees" and s.amount]
    if page.product.price is not None:
        fees.insert(0, {"label": "Item", "amount": page.product.price, "step": None})
    return {"price_history": None, "timer_frames": [], "fees": fees,
            "total": round(sum(f["amount"] for f in fees), 2) if fees else None, "stores": []}


# ----------------------------------------------------------------------------- API


@app.get("/health")
def health() -> dict:
    return {"ok": True, "mode": "stub", "agent_deal": _deal_mode(), "checks": len(CHECKS)}


@app.get("/")
def root():
    return RedirectResponse("/storelab/audiodealz/")


@app.post("/check")
def start_check(req: CheckRequest, fresh: bool = False) -> dict:
    """fresh=1 (query string, optional): re-run every check instead of reusing recent results (the panel's refresh button)."""
    cid = uuid.uuid4().hex[:12]
    page = req.page or PageFacts(url=req.url)
    demo = _is_demo(req.url)
    CHECKS[cid] = {"check_id": cid, "url": req.url, "demo": demo, "page": page.model_dump(), "started": time.time(),
                   "findings": [], "final_card": None, "fresh": fresh,
                   "evidence_data": demo_evidence() if demo else real_evidence(page)}
    return {"check_id": cid}


@app.get("/check/{cid}")
def get_check(cid: str) -> dict:
    if cid not in CHECKS:
        raise HTTPException(404, "unknown check")
    return CHECKS[cid]


@app.get("/check/{cid}/events")
async def check_events(cid: str):
    if cid not in CHECKS:
        raise HTTPException(404, "unknown check")
    chk = CHECKS[cid]
    if os.environ.get("AGENT_ORCH", "off").lower() == "on":  # opt-in: backend/orchestrator.py runs the check
        from backend.orchestrator_stub import sse_stream
        return StreamingResponse(sse_stream(chk), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def stream():
        page = PageFacts(**chk["page"])
        findings = demo_findings(cid) if chk["demo"] else real_findings(cid, page)
        delay = DEMO_DELAY if chk["demo"] else 0.18
        chk["findings"] = []
        if _deal_mode() == "real":
            async for kind, item in _with_real_deal(cid, chk, page, findings, delay):
                if kind == "finding":
                    item.ts = time.time()
                    chk["findings"].append(item.model_dump())
                    yield f"data: {json.dumps({'kind': 'finding', 'finding': item.model_dump()})}\n\n"
                else:
                    chk["final_card"] = item.model_dump()
                    yield f"data: {json.dumps({'kind': 'final_card', 'card': item.model_dump()})}\n\n"
            return
        streamed = []
        async for f in _paced(findings, delay):
            f.ts = time.time()
            streamed.append(f)
            chk["findings"].append(f.model_dump())
            yield f"data: {json.dumps({'kind': 'finding', 'finding': f.model_dump()})}\n\n"
        findings = streamed
        await asyncio.sleep(delay)
        elapsed = time.time() - chk["started"]
        card = demo_card(cid, elapsed) if chk["demo"] else real_card(cid, page, findings, elapsed)
        chk["final_card"] = card.model_dump()
        yield f"data: {json.dumps({'kind': 'final_card', 'card': card.model_dump()})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _paced(findings: list[Finding], delay: float):
    for f in findings:
        await asyncio.sleep(delay)
        yield f


async def _with_real_deal(cid: str, chk: dict, page: PageFacts, findings: list[Finding], delay: float):
    """Scripted non-deal findings (Trust, Price, Skeptic, page) paced as before, merged with the real Deal Agent's live findings.
    Yields ("finding", Finding) and ("card", FinalCard). A verdict goes out as soon as the fast Deal tools finish (~0.1 s) and
    again each time a slow one (run-ahead, timer test) lands, so it firms up as proof arrives. The last card is the final one."""
    from backend.agents import deal

    queue: asyncio.Queue = asyncio.Queue()
    done: set[str] = set()

    async def scripted():
        async for f in _paced([f for f in findings if f.agent != "deal"], delay):
            await queue.put(("finding", f))

    async def agent():
        async for f in deal.iter_deal(cid, chk["url"], page, fresh=chk.get("fresh", False),
                                      on_tool_done=lambda name, _secs: queue.put_nowait(("tool", name))):
            await queue.put(("finding", f))

    async def both():
        await asyncio.gather(scripted(), agent(), return_exceptions=True)
        await queue.put(None)  # end of stream

    runner = asyncio.create_task(both())
    seen: list[Finding] = []
    sent_card = False
    try:
        while (item := await queue.get()) is not None:
            kind, value = item
            if kind == "finding":
                seen.append(value)
                yield "finding", value
                continue
            done.add(value)
            fast_done = set(deal.FAST_TOOLS) <= done
            if fast_done and (not sent_card or value in deal.SLOW_TOOLS):
                sent_card = True
                yield "card", _live_card(cid, chk, page, seen, done, deal)
        yield "card", _live_card(cid, chk, page, seen, done, deal)  # the final verdict, with the total elapsed time
    finally:
        runner.cancel()
        deal.EVIDENCE.pop(cid, None)


def _live_card(cid: str, chk: dict, page: PageFacts, seen: list[Finding], done: set[str], deal) -> FinalCard:
    """The verdict so far. Placeholders for slow checks that are still running show as "Checking…" reasons."""
    chk["evidence_data"].update(deal.EVIDENCE.get(cid, {}))  # real price history, timer frames, fee table, timings
    pending = [f for f in seen if f.model == deal.PENDING and deal.TYPE_TOOL.get(f.type) not in done]
    shown = [f for f in seen if f.model != deal.PENDING]
    # Finished placeholders leave the stored check, so GET /check and the chat never see a stale "still checking" row.
    chk["findings"] = [d for d in chk["findings"]
                       if not (d.get("model") == deal.PENDING and deal.TYPE_TOOL.get(d.get("type")) in done)]
    elapsed = time.time() - chk["started"]
    if chk["demo"]:
        return _demo_card_live(cid, elapsed, shown, pending, chk["evidence_data"])
    card = real_card(cid, page, shown, elapsed)
    if pending:
        kept = [r for r in card.honest.reasons if not r.text.startswith("No timers")]
        card.honest.reasons = (kept + [Reason(text=f.summary, tone="unknown") for f in pending])[:5]
    return card


def _money_short(v: float) -> str:
    return f"${v:,.0f}" if float(v).is_integer() else f"${v:,.2f}"


def _demo_card_live(cid: str, elapsed: float, deal_findings: list[Finding], pending: list[Finding], ev: dict) -> FinalCard:
    """The AudioDealz demo card, with "Honest deal?" built from the real Deal Agent's findings instead of the script."""
    card = demo_card(cid, elapsed)
    by = {f.type: f for f in deal_findings}  # the latest finding of each type
    fake, timer, fees = by.get("fake_discount"), by.get("resetting_timer"), by.get("drip_fees")
    reasons = []
    for f, text, proof in ((fake, "Fake 40% off: it was $149 for 81 of 90 days", "chart"),
                           (timer, "Timer resets on every visit", "replay"),
                           (fees, None, "fees")):
        if not f:
            continue
        if f.status != "confirmed":
            reasons.append(Reason(text=f.summary, tone="unknown"))
            continue
        if text is None:  # "$37.01 in fees appear only at checkout step 4 (...)" -> "$37 in fees appear at checkout step 4"
            m = re.match(r"\$([\d,.]+) in fees appear only at checkout step (\d+)", f.summary)
            text = f"${float(m.group(1).replace(',', '')):,.0f} in fees appear at checkout step {m.group(2)}" if m else f.summary
        reasons.append(Reason(text=text, tone="bad", proof=proof))
    reasons += [Reason(text=p.summary, tone="unknown") for p in pending]
    n = sum(1 for f in (fake, timer, fees) if f and f.status == "confirmed")
    tricks = f"{n} trick{'s' if n != 1 else ''}"
    total = ev.get("total") if fees and fees.status == "confirmed" else None
    card.tricks = n
    card.honest = Verdict(status="bad" if n else "unknown", headline=tricks + (" so far" if pending else ""), reasons=reasons)
    card.subtitle = (f"Risky store · {tricks} · real total {_money_short(total)}, not $149" if total
                     else f"Risky store · {tricks} so far")
    return card


@app.post("/chat")
async def chat(req: ChatRequest) -> ChatResponse:
    """Questions about one finished check. Answers only from what the check stored; never loads the page or any site."""
    chk = CHECKS.get(req.check_id)
    if not chk:
        raise HTTPException(404, "unknown check")
    return await asyncio.to_thread(chat_agent.answer, chk, req.question, req.history)


class UntickReq(BaseModel):
    check_id: Optional[str] = None
    selector: Optional[str] = None
    amount: Optional[float] = None


@app.post("/action/untick")
def untick(req: UntickReq) -> dict:
    chk = CHECKS.get(req.check_id or "")
    new_total = None
    if chk and chk["demo"]:
        new_total = 186.01
        chk["evidence_data"]["fees"] = [f for f in chk["evidence_data"]["fees"] if "Protection" not in f["label"]]
        chk["evidence_data"]["total"] = new_total
    return {"ok": True, "new_total": new_total}


# ----------------------------------------------------------------------------- wishlist + demo clock

WISHLIST: list[WishlistItem] = [
    WishlistItem(id="sony", name="Sony WH-1000XM5", rule="Under $215 · new only", target=215, best_price=229,
                 best_store="Store C", status="Watching", tone="unknown"),
    WishlistItem(id="kindle", name="Kindle Paperwhite", rule="Under $120", target=120, best_price=139,
                 best_store="Store A", status="Watching", tone="unknown"),
    WishlistItem(id="shoes", name="Running shoes, size 10", rule="Under $90 · trusted only", target=90, best_price=84,
                 best_store="Store F", status="Held: fake discount", tone="bad"),
    WishlistItem(id="fryer", name="Air fryer, 6 qt", rule="Under $80", target=80, best_price=92,
                 best_store="Store B", status="Wait: sale likely", tone="caution"),
]
CLOCK = {"day": 1, "alerted": set()}


def _stats(day: int) -> dict:
    s = max(1, min(day, 9)) / 9
    return {"price_changes": round(340 * s), "alerts": 2 if day >= 9 else (1 if day >= 5 else 0),
            "tricks": round(11 * s), "stores": 10}


@app.get("/wishlist")
def get_wishlist() -> dict:
    return {"day": CLOCK["day"], "items": [i.model_dump() for i in WISHLIST], "stats": _stats(CLOCK["day"])}


@app.post("/wishlist")
def add_wishlist(item: WishlistItem) -> dict:
    for existing in WISHLIST:
        if existing.name.lower() == item.name.lower():
            return {"ok": True, "item": existing.model_dump(), "duplicate": True}
    item.id = item.id or uuid.uuid4().hex[:8]
    WISHLIST.append(item)
    return {"ok": True, "item": item.model_dump()}


class ClockReq(BaseModel):
    day: int


@app.post("/demo/clock")
def demo_clock(req: ClockReq) -> dict:
    CLOCK["day"] = req.day
    alerts = []
    sony = next((i for i in WISHLIST if i.id == "sony"), None)
    if sony:
        if req.day >= 9:
            sony.best_price, sony.status, sony.tone = 209, "Alert sent", "good"
            if "sony" not in CLOCK["alerted"]:
                CLOCK["alerted"].add("sony")
                alerts.append({"id": "sony", "title": "Sony WH-1000XM5 · $209 at Store C",
                               "message": "Lowest true price in 90 days · same model, new · trusted store · Skeptic approved"})
        else:
            sony.best_price, sony.status, sony.tone = 229, "Watching", "unknown"
            CLOCK["alerted"].discard("sony")
    return {"day": req.day, "alerts": alerts, "stats": _stats(req.day)}
