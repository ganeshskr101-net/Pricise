"""Pricise stub backend: a fake version of the real orchestrator, so the extension UI works before the agents exist.

Run:  python -m uvicorn backend.stub_server:app --port 8787 --reload   (from the repo root)

- Store Lab hero page (/storelab/audiodealz/): streams the scripted demo (3 tricks, real total $191, Store C $229).
- Any other page (real stores): turns the signals the extension read from the page into careful, evidence-backed findings.
  Trust (domain age etc.) and Price (other stores) are marked "not connected yet" until Raj's and Josh's agents land.
Nothing here calls a model. The real backend replaces these functions with agents that go through Meterless Nightshift.

AGENT_DEAL=real streams the real Deal Agent (backend/agents/deal.py) in place of the scripted/heuristic deal findings;
AGENT_DEAL=stub (default) keeps the scripted demo exactly as it was.
AGENT_TRUST=real does the same for "Shop safe?": the real Trust Agent (backend/agents/trust.py) replaces the scripted trust
findings and the card's safe section. POST /internal/trust/vet (for the Price Agent) is always available.

AGENT_PRICE=real runs the Price Agent (backend/agents/price.py) in place of the scripted Store B/D/C findings, so "Best safe
price" comes from Zetaris (or the catalog fixture) and follows the demo clock (Day 9: Store C $209).

DATA_SOURCE=zetaris reads the wishlist through Zetaris and stores each finished check's findings in the Pricise Postgres (the
database Zetaris reads); any failure falls back to the in-memory wishlist, so the demo never breaks. Default: fixture (memory).
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
from urllib.parse import urlsplit
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
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

from backend.env import apply_sample_mode, load_dotenv, sample_mode

load_dotenv()  # before anything reads settings; shell variables still win, no .env is fine
apply_sample_mode()  # SAMPLE_MODE=true: every agent stub, fixture data, no Zetaris / Nightshift / SerpApi / RDAP calls

from backend.agents import chat as chat_agent  # noqa: E402
from backend.agents import price as price_agent  # noqa: E402
from backend import agent_trace  # noqa: E402
from backend.data import repo, zetaris  # noqa: E402
from backend.data.pg import PgError  # noqa: E402
from backend.data.zetaris import ZetarisError  # noqa: E402
from backend.agents.chat import ChatRequest, ChatResponse  # noqa: E402  (proposed for backend/contracts.py)
from backend.agents.trust_api import router as trust_router  # noqa: E402

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
app.add_middleware(CORSMiddleware,  # the extension (any unpacked id) and this laptop only; web pages can't read replies
                   allow_origin_regex=r"^(chrome-extension://[a-p]{32}|http://(localhost|127\.0\.0\.1)(:\d+)?)$",
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(trust_router)  # POST /internal/trust/vet
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
        model=kw.pop("model", "stub (no model call)" if sample_mode() else "extension (no model call)"), **kw,
    )


def _is_demo(url: str) -> bool:
    return "/storelab/audiodealz" in url


def _price_mode() -> str:
    return "real" if os.environ.get("AGENT_PRICE", "stub").lower() == "real" else "stub"


def _deal_mode() -> str:
    return "real" if os.environ.get("AGENT_DEAL", "stub").lower() == "real" else "stub"


def _trust_mode() -> str:
    return "real" if os.environ.get("AGENT_TRUST", "stub").lower() == "real" else "stub"


async def _real_trust(cid: str, chk: dict, page: PageFacts, findings: list[Finding]):
    """AGENT_TRUST=real: the Trust Agent's findings take the place of the scripted trust rows (same spot in the stream)."""
    from backend.agents import trust
    tv = await trust.assess_store(chk["url"], cid, page)
    chk["evidence_data"]["store_checks"] = tv.get("checks")  # the evidence page's "Store checks" table
    at = next((i for i, f in enumerate(findings) if f.agent == "trust"), len(findings))
    rest = [f for f in findings if f.agent != "trust"]
    at = min(at, len(rest))
    return rest[:at] + tv["findings"] + rest[at:], tv


def _with_trust(card: FinalCard, tv) -> FinalCard:
    if tv is not None:
        from backend.agents import trust
        card.safe = trust.safe_verdict(tv)
    return _safe_headline(card)


def _safe_headline(card: FinalCard) -> FinalCard:
    """A risky or doubtful store raises the card's tone and title ("Shop safe?" bad or caution); never lowers them.
    Call it once card.safe is final (after the Trust verdict replaced real_card's provisional section)."""
    if card.safe.status == "bad" and card.tone != "bad":
        card.tone, card.title = "bad", "Don't buy here"
    elif card.safe.status == "caution" and card.tone in ("good", "unknown"):
        card.tone, card.title = "caution", "Be careful here"
    return card


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

TRICK_TYPES = {"fake_discount", "prechecked_addon", "resetting_timer", "drip_fees"}


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
            from backend.tools.discount import CARD_OFFER_RE
            if CARD_OFFER_RE.search(s.text or ""):
                continue  # a store card's signup offer: not this item's price or a discount
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
    # Stub mode only: with AGENT_TRUST / AGENT_PRICE=real those agents report for themselves, never "not connected".
    if _trust_mode() != "real":
        out.append(_f(cid, "trust", "not_connected", "unverified",
                      f"Store checks for {page.host or 'this site'} (domain age, company record) need the Trust Agent, not connected yet."))
    if _price_mode() != "real":
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
    tricks = [f for f in findings if f.type in TRICK_TYPES and f.status == "confirmed"]  # proposed: "check these" below
    soft, good, seen_types = [], [], set()
    for f in findings:  # one line per kind of claim, so the card stays short
        if f.type == "claimed_discount" and f.status == "confirmed":
            good.append(f)  # the discount checks out (price history, or the list price is the launch price)
        elif f.type in TRICK_TYPES and f.status == "proposed":
            soft.append(f)
        elif f.type in {"claimed_discount", "inflated_discount", "countdown", "scarcity"} and f.type not in seen_types:
            seen_types.add(f.type)
            soft.append(f)
    honest_reasons = [Reason(text=f.summary, tone="bad" if f in tricks else "caution",
                             proof="fees" if f.type == "drip_fees" else None) for f in tricks + soft][:4]
    honest_reasons += [Reason(text=f.summary, tone="good") for f in good][:max(0, 4 - len(honest_reasons))]
    if not honest_reasons:
        honest_reasons = [Reason(text="No timers, pre-ticked add-ons or extra fees found on this page", tone="good")]
    n = len(tricks)
    honest = Verdict(status="bad" if n else ("caution" if soft else "good"),
                     headline=f"{n} trick{'s' if n != 1 else ''}" if n else ("Check these" if soft else "Looks honest"),
                     reasons=honest_reasons)
    trust_real, price_real = _trust_mode() == "real", _price_mode() == "real"
    safe_reasons = [Reason(text="Secure connection (HTTPS)" if page.https else "No secure connection (HTTPS)",
                           tone="good" if page.https else "bad"),
                    Reason(text="Store checks (domain age, links): no answer from the Trust Agent yet" if trust_real
                           else "Domain age and company record: Trust Agent not connected yet", tone="unknown")]
    # With AGENT_TRUST=real this is only a placeholder until trust.safe_verdict() replaces it.
    safe = Verdict(status="unknown" if page.https else "bad",
                   headline=("Unknown" if trust_real else "Not fully checked") if page.https else "Risky", reasons=safe_reasons)
    p = page.product
    bp_reasons = []
    if p.name:
        bp_reasons.append(Reason(text=f"Product: {p.name[:70]}", tone="unknown"))
    if p.price is not None:
        bp_reasons.append(Reason(text=f"Price on this page: ${p.price:,.2f}", tone="unknown"))
    if price_real:  # placeholder until the Price Agent answers (stub_server._apply_price)
        bp_reasons.append(Reason(text="Looking for prices you've seen on other stores…", tone="unknown"))
        best = BestPrice(status="unknown", headline="Compare", reasons=bp_reasons)
    else:
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
    # The headline follows "Shop safe?" in _with_trust / the orchestrator's card builder, once the final section is known.
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
    from backend.llm import meterless
    from backend.tools import rdap

    return {"ok": True, "mode": "sample" if sample_mode() else "full", "agent_deal": _deal_mode(), "agent_trust": _trust_mode(),
            "agent_price": _price_mode(), "agent_orch": "on" if os.environ.get("AGENT_ORCH", "off").lower() == "on" else "off",
            "data_source": "zetaris" if zetaris.use_zetaris() else "fixture", "trust_rdap": "on" if rdap.enabled() else "off",
            "nemotron": {"fast": meterless.configured("fast"), "deep": meterless.configured("deep")}, "checks": len(CHECKS)}


@app.get("/")
def root() -> dict:
    return {"name": "Pricise backend", "health": "/health", "mode": "sample" if sample_mode() else "full"}


@app.get("/checks/recent")
def recent_checks() -> dict:
    """The newest checks first (for scripts/record_run.py): id, url, whether it finished and is a real page."""
    from backend.tools.common import is_storelab

    rows = sorted(CHECKS.values(), key=lambda c: -c["started"])[:20]
    return {"checks": [{"check_id": c["check_id"], "url": c["url"], "finished": c.get("final_card") is not None,
                        "real": not is_storelab(c["url"]) and not c.get("replay"), "started": c["started"]} for c in rows]}


@app.post("/check")
def start_check(req: CheckRequest, fresh: bool = False) -> dict:
    """fresh=1 (query string, optional): re-run every check instead of reusing recent results (the panel's refresh button)."""
    cid = uuid.uuid4().hex[:12]
    page = req.page or PageFacts(url=req.url)
    demo = _is_demo(req.url)
    skip = req.page is not None and not demo and not_a_product_page(req.url, page)
    CHECKS[cid] = {"check_id": cid, "url": req.url, "demo": demo, "page": page.model_dump(), "started": time.time(),
                   "findings": [], "final_card": None, "fresh": fresh, "not_product": skip,
                   "evidence_data": demo_evidence() if demo else real_evidence(page)}
    if sample_mode() and not demo:  # a recorded real run of this page replaces the scripted stub
        from backend import recordings

        rec = recordings.match(req.url)
        if rec:
            CHECKS[cid].update(replay=rec["file"], page=rec["page"], evidence_data=rec.get("evidence_data") or {},
                               not_product=False)
    return {"check_id": cid}


async def _replay(chk: dict):
    """SAMPLE_MODE=true and a recording of this page: its findings and card as they streamed in the real run, re-stamped
    with this check's id, and the card says it was recorded. No agent, search or model call."""
    from backend import recordings

    rec = next((r for r in recordings.load_all() if r["file"] == chk["replay"]), None)
    cid, chk["findings"] = chk["check_id"], []
    for ev in (rec or {}).get("events", []):
        if ev["kind"] == "finding":
            f = Finding(**{**ev["finding"], "check_id": cid, "ts": time.time()})
            chk["findings"].append(f.model_dump())
            agent_trace.finding(chk, f)
            yield f"data: {json.dumps({'kind': 'finding', 'finding': f.model_dump()})}\n\n"
            await asyncio.sleep(0.12)
        else:
            card = FinalCard(**{**ev["card"], "check_id": cid})
            card.subtitle = f"{card.subtitle} · {recordings.label(rec)}"
            chk["final_card"] = card.model_dump()
            agent_trace.verdict(chk, chk["final_card"])
            yield f"data: {json.dumps({'kind': 'final_card', 'card': card.model_dump()})}\n\n"


PRODUCT_SIGNALS = {"claimed_discount", "countdown", "resetting_timer", "prechecked_addon", "drip_fees", "price_history"}


def not_a_product_page(url: str, page: PageFacts) -> bool:
    """A real page with nothing to check: no price, and its name is only the site's name ("Amazon.com" on a block or
    interstitial page), or nothing about it is a product (no SKU, model, brand or product signal). Store Lab pages never."""
    from backend.tools.common import is_storelab

    p = page.product
    if is_storelab(url) or p.price is not None:
        return False
    if p.sku or p.model or p.brand or any(s.type in PRODUCT_SIGNALS for s in page.signals):
        return False  # something about it is a product (a pre-ticked add-on, a fee, a discount claim...)
    return True  # no price, no product identity, no product signal: a title alone ("Amazon.com", an article) isn't one


def not_product_card(cid: str) -> FinalCard:
    """The only result for a page that isn't a product: no agents ran, no online search, no model call."""
    none = Verdict(status="unknown", headline="Not checked", reasons=[])
    return FinalCard(check_id=cid, title="Not a product page", subtitle="Pricise checks product pages; there is nothing to "
                     "check here", tone="unknown", safe=none, honest=none,
                     best_price=BestPrice(status="unknown", headline="Not checked"), tricks=0, alt={"kind": "not_product"})


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
    if chk.get("replay"):
        return StreamingResponse(_replay(chk), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
    if chk.get("not_product"):  # before any agent, online search or model call (both paths)
        card = not_product_card(cid)
        chk["final_card"] = card.model_dump()
        agent_trace.verdict(chk, chk["final_card"])
        body = f"data: {json.dumps({'kind': 'final_card', 'card': card.model_dump()})}\n\n"

        async def once():
            yield body

        return StreamingResponse(once(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
    if os.environ.get("AGENT_ORCH", "off").lower() == "on":  # opt-in: backend/orchestrator.py runs the check
        from backend.orchestrator_stub import sse_stream
        return StreamingResponse(sse_stream(chk), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def stream():
        page = PageFacts(**chk["page"])
        findings = demo_findings(cid) if chk["demo"] else real_findings(cid, page)
        delay = DEMO_DELAY if chk["demo"] else 0.18
        chk["findings"] = []
        tv = None
        if _trust_mode() == "real":
            findings, tv = await _real_trust(cid, chk, page, findings)
        if _deal_mode() == "real":
            async for kind, item in _with_real_deal(cid, chk, page, findings, delay):
                if kind == "finding":
                    item.ts = time.time()
                    chk["findings"].append(item.model_dump())
                    agent_trace.finding(chk, item)
                    yield f"data: {json.dumps({'kind': 'finding', 'finding': item.model_dump()})}\n\n"
                else:
                    item = _with_trust(item, tv)
                    chk["final_card"] = item.model_dump()
                    yield f"data: {json.dumps({'kind': 'final_card', 'card': item.model_dump()})}\n\n"
            await _store_findings(chk)
            return
        streamed = []
        async for f in _with_price(cid, chk, page, findings, delay):
            f.ts = time.time()
            streamed.append(f)
            chk["findings"].append(f.model_dump())
            agent_trace.finding(chk, f)
            yield f"data: {json.dumps({'kind': 'finding', 'finding': f.model_dump()})}\n\n"
        findings = streamed
        await asyncio.sleep(delay)
        elapsed = time.time() - chk["started"]
        card = demo_card(cid, elapsed) if chk["demo"] else real_card(cid, page, findings, elapsed)
        card = _apply_price(cid, _with_trust(card, tv))
        chk["final_card"] = card.model_dump()
        yield f"data: {json.dumps({'kind': 'final_card', 'card': card.model_dump()})}\n\n"
        await _store_findings(chk)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def observation(chk: dict) -> Optional[dict]:
    """The price the shopper saw on a REAL store page (never Store Lab or bench), for repo.save_observation. None when the
    page has no price or nothing to match on, or when the finished check found the store unsafe."""
    from backend.data import catalog
    from backend.tools.common import is_storelab

    page = PageFacts(**chk["page"])
    p, safe = page.product, ((chk.get("final_card") or {}).get("safe") or {}).get("status")
    if is_storelab(chk["url"]) or "/bench/" in chk["url"] or p.price is None or safe == "bad":
        return None
    host = (page.host or urlsplit(chk["url"]).netloc).lower().removeprefix("www.")
    return {"host": host, "brand": p.brand, "model": p.model, "model_key": catalog.key(p.model or ""), "sku": p.sku,
            "name": (p.name or "")[:160] or None, "price": round(float(p.price), 2), "currency": p.currency,
            "condition": repo.condition_of(p.name), "safe": safe, "url": repo.clean_url(chk["url"]), "seen_at": time.time()}


async def _save_observation(chk: dict) -> None:
    """Best effort, once per check: a failure is a log line, never a broken check."""
    try:
        obs = observation(chk)
        if obs and await asyncio.to_thread(repo.save_observation, obs):
            chk["observation_saved"] = True
    except (PgError, ValueError, KeyError) as err:
        _log.warning("price observation not stored: %s", err)


async def _store_findings(chk: dict) -> None:
    """DATA_SOURCE=zetaris: keep the finished check's findings in the Pricise Postgres. Best effort: never breaks a check.
    Every mode: a real store page's price becomes a price observation (Postgres with zetaris, memory with fixture).
    Also the trace's last step (logs/<check_id>.jsonl): the verdict the shopper sees."""
    agent_trace.verdict(chk, chk.get("final_card"))
    await _save_observation(chk)
    if not zetaris.use_zetaris():
        return
    try:
        found = [Finding(**d) for d in chk["findings"]]
        n = await asyncio.to_thread(repo.save_findings, chk["check_id"], chk["url"], found)
        chk["stored_findings"] = n
    except (PgError, ValueError) as err:
        _log.warning("findings not stored: %s", err)


async def _paced(findings: list[Finding], delay: float):
    for f in findings:
        await asyncio.sleep(delay)
        yield f


PRICE: dict[str, price_agent.PriceResult] = {}  # check_id -> the Price Agent's result (AGENT_PRICE=real)


def _scripted_price(f: Finding) -> bool:
    """The scripted Store B / Store D / Store C rows (and the stub Skeptic's "$229" verdict) the real Price Agent replaces."""
    return f.agent == "price" or (f.agent == "skeptic" and f.type == "best_price")


async def _with_price(cid: str, chk: dict, page: PageFacts, findings: list[Finding], delay: float):
    """The scripted findings, paced. With AGENT_PRICE=real the Price Agent runs alongside them (one Zetaris query, plus a
    Trust Agent vet of each candidate store when AGENT_TRUST=real) and its findings follow; the result also fills the evidence
    page's store comparison."""
    if _price_mode() != "real":
        async for f in _paced(findings, delay):
            yield f
        return
    task = asyncio.create_task(price_agent.assess_async(cid, chk["url"], page, CLOCK["day"],
                                                        use_trust=_trust_mode() == "real"))
    async for f in _paced([f for f in findings if not _scripted_price(f)], delay):
        yield f
    res = await task
    PRICE[cid] = res
    chk["evidence_data"]["stores"] = res.stores
    chk["evidence_data"]["online"] = res.online
    chk["evidence_data"]["stores_sql"] = res.sql
    async for f in _paced(res.findings, delay):
        yield f


def _apply_price(cid: str, card: FinalCard) -> FinalCard:
    """Put the Price Agent's "Best safe price" verdict and safer choice on the card, once it has answered."""
    res = PRICE.get(cid)
    if res:
        card.best_price, card.alt = res.best_price, res.alt
    return card


async def _with_real_deal(cid: str, chk: dict, page: PageFacts, findings: list[Finding], delay: float):
    """Scripted non-deal findings (Trust, Price, Skeptic, page) paced as before, merged with the real Deal Agent's live findings.
    Yields ("finding", Finding) and ("card", FinalCard). A verdict goes out as soon as the fast Deal tools finish (~0.1 s) and
    again each time a slow one (run-ahead, timer test) lands, so it firms up as proof arrives. The last card is the final one."""
    from backend.agents import deal

    queue: asyncio.Queue = asyncio.Queue()
    done: set[str] = set()

    async def scripted():
        async for f in _with_price(cid, chk, page, [f for f in findings if f.agent != "deal"], delay):
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
        return _apply_price(cid, _demo_card_live(cid, elapsed, shown, pending, chk["evidence_data"]))
    card = real_card(cid, page, shown, elapsed)
    if pending:
        kept = [r for r in card.honest.reasons if not r.text.startswith("No timers")]
        card.honest.reasons = (kept + [Reason(text=f.summary, tone="unknown") for f in pending])[:5]
    return _apply_price(cid, card)


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


def _wishlist_items() -> tuple[list[WishlistItem], str]:
    """(items, source): Zetaris when DATA_SOURCE=zetaris and it answers, else the in-memory list."""
    if zetaris.use_zetaris():
        try:
            return repo.get_wishlist(), "zetaris"
        except (ZetarisError, ValueError) as err:
            _log.warning("wishlist from Zetaris failed, using memory: %s", err)
    return WISHLIST, "memory"


SEEDED_WISHLIST = {"sony", "kindle", "shoes", "fryer"}  # the Store Lab demo's watched items (made-up stores)


def storelab_demo() -> bool:
    """STORELAB_DEMO=on shows the Store Lab wishlist demo (seeded items, demo clock, its stats). Off by default: the
    product demo uses real retailer pages only; Store Lab stays for tests."""
    return os.environ.get("STORELAB_DEMO", "off").strip().lower() == "on"


@app.get("/wishlist")
def get_wishlist() -> dict:
    items, source = _wishlist_items()
    demo = storelab_demo()
    if not demo:
        items = [i for i in items if i.id not in SEEDED_WISHLIST]
    return {"day": CLOCK["day"], "items": [i.model_dump() for i in items], "stats": _stats(CLOCK["day"]) if demo else None,
            "source": source, "demo_clock": demo}


@app.post("/wishlist")
def add_wishlist(item: WishlistItem) -> dict:
    items, source = _wishlist_items()
    for existing in items:
        if existing.name.lower() == item.name.lower():
            return {"ok": True, "item": existing.model_dump(), "duplicate": True}
    item.id = item.id or uuid.uuid4().hex[:8]
    if source == "zetaris":
        try:
            repo.add_wishlist(item)
            return {"ok": True, "item": item.model_dump()}
        except PgError as err:
            _log.warning("wishlist write failed, keeping it in memory: %s", err)
    WISHLIST.append(item)
    return {"ok": True, "item": item.model_dump()}


class ClockReq(BaseModel):
    day: int


@app.post("/demo/clock")
def demo_clock(req: ClockReq, request: Request) -> dict:
    CLOCK["day"] = req.day
    alerts = []
    sony = next((i for i in WISHLIST if i.id == "sony"), None)
    if sony:
        _clock_sony(sony, req.day, alerts, str(request.base_url))
    return {"day": req.day, "alerts": alerts, "stats": _stats(req.day)}


def _clock_sony(sony: WishlistItem, day: int, alerts: list, origin: str) -> None:
    """The wishlist week: on Day 9 Store C drops to $209 (below the $215 target) and an alert goes out once.
    With AGENT_PRICE=real the price comes from the Price Agent for that demo day; otherwise it is scripted."""
    store, total = ("Store C", 209) if day >= 9 else ("Store C", 229)
    if _price_mode() == "real":
        best = price_agent.best_for("WH-1000XM5", "new", day, origin, use_trust=_trust_mode() == "real")
        if best:
            store, total = best["store"], best["true_total"]
    sony.best_store = store
    if total <= (sony.target or 0):
        sony.best_price, sony.status, sony.tone = total, "Alert sent", "good"
        if "sony" not in CLOCK["alerted"]:
            CLOCK["alerted"].add("sony")
            alerts.append({"id": "sony", "title": f"Sony WH-1000XM5 · {_money_short(total)} at {store}",
                           "message": "Lowest true price in 90 days · same model, new · trusted store · Skeptic approved"})
    else:
        sony.best_price, sony.status, sony.tone = total, "Watching", "unknown"
        CLOCK["alerted"].discard("sony")
    if zetaris.use_zetaris():
        try:
            repo.update_wishlist("sony", best_price=sony.best_price, best_store=sony.best_store, status=sony.status,
                                 tone=sony.tone)
        except PgError as err:
            _log.warning("wishlist update not stored: %s", err)
