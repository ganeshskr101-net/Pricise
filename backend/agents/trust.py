"""Trust Agent (Raj): answers "Shop safe?" for the page the user is on and for every store the Price Agent proposes.

Two modes, like the Deal Agent:
- Store Lab stores (localhost + /storelab/ or /bench/): five checks, each a tool in backend/tools/ with a timeout:
  domain age, company record, copied photos, review patterns, returns policy.
- Real store pages: READ ONLY. Nothing is fetched from the real site. Only the HTTPS fact the extension sent is used; the
  store checks are reported as "unverified" so the panel never claims what wasn't checked.

The verdict comes from explicit rules (decide(), documented in docs/trust_agent.md), never from the model. The Nemotron fast lane
(via backend/llm/meterless.py) may only reword the reasons, when TRUST_LLM_SUMMARY=true; if it is slow or missing the rule text stays.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import time
from typing import Literal, Optional, TypedDict
from urllib.parse import urlsplit

import httpx

from backend.contracts import Evidence, Finding, PageFacts, Reason, Verdict
from backend.tools.common import RULES, is_storelab
from backend.tools.domain import check_domain_age
from backend.tools.photo_match import check_copied_photos
from backend.tools.photo_match import clear_cache as clear_photo_cache
from backend.tools.registry import check_registry
from backend.tools.returns import check_returns
from backend.tools.reviews import check_reviews
from backend.tools.trust_common import (REVIEWS_PATHS, RETURNS_PATHS, Signal, find_store_base, first_json, first_text,
                                        make_client, sibling_stores)

try:  # Abel's World Model when it lands; the in-memory stub until then
    from backend.engines.world_model import write_finding  # type: ignore[attr-defined]
except ImportError:
    from backend.engines.world_model_stub import write_finding

log = logging.getLogger("pricise.trust")
TrustLevel = Literal["safe", "caution", "unsafe"]
CACHE_S = 300
SUMMARY_TIMEOUT_S = 2.0


class TrustVerdict(TypedDict):
    verdict: TrustLevel
    reasons: list[str]
    findings: list[Finding]
    store: Optional[str]      # store name from about.json
    store_url: str            # the store root that was checked
    checked: bool             # False = real store page or store didn't answer: the store checks did not run
    elapsed_ms: int


_cache: dict[str, tuple[float, TrustVerdict]] = {}


def decide(signals: list[Signal]) -> TrustLevel:
    """The trust rules.
    unsafe : 2+ strong warning signs, or 1 strong + 2 weak
    caution: 1 strong warning sign, or 2+ weak ones
    safe   : otherwise (a single weak sign is shown as a note, not a warning)"""
    strong = sum(1 for s in signals if s.flagged and s.strength == "strong")
    weak = sum(1 for s in signals if s.flagged and s.strength == "weak")
    if strong >= 2 or (strong == 1 and weak >= 2):
        return "unsafe"
    if strong == 1 or weak >= 2:
        return "caution"
    return "safe"


def to_finding(check_id: str, s: Signal) -> Finding:
    return Finding(check_id=check_id, agent="trust", type=s.name, status="proposed" if s.verified else "unverified",
                   summary=s.reason, evidence=[Evidence(**e) for e in s.evidence], confidence=round(s.confidence, 2),
                   model=RULES, ts=time.time())


def reasons_for(signals: list[Signal]) -> list[str]:
    """Warning signs first (strong before weak), then what couldn't be checked."""
    flagged = sorted((s for s in signals if s.flagged), key=lambda s: s.strength != "strong")
    out = [s.reason for s in flagged]
    if not out and any(s.verified for s in signals):  # never reassure about checks that didn't run
        out = ["No warning signs found."]
    out += [s.reason for s in signals if not s.verified and not s.flagged]
    return list(dict.fromkeys(out))


async def _reword(reasons: list[str]) -> list[str]:
    """Optional plain-English rewording on the Nemotron fast lane. Never changes the verdict; same line count or ignored."""
    if os.getenv("TRUST_LLM_SUMMARY", "false").lower() != "true":
        return reasons
    from backend.llm import meterless
    if not meterless.configured("fast"):
        return reasons
    prompt = ("Rewrite each line as one short sentence a shopper understands. Keep the same facts and the same number of "
              "lines, one per line, no bullets, nothing new.\n" + "\n".join(reasons))
    try:
        text = await asyncio.wait_for(asyncio.to_thread(meterless.complete, prompt, "fast", 160), SUMMARY_TIMEOUT_S)
    except Exception:  # noqa: BLE001 - cosmetic only
        return reasons
    lines = [ln.strip(" -•\t") for ln in (text or "").splitlines() if ln.strip()]
    return lines if len(lines) == len(reasons) else reasons


async def _write_all(findings: list[Finding]) -> None:
    for f in findings:
        res = write_finding(f)
        if inspect.isawaitable(res):
            await res


def _real_page(store_url: str, check_id: str, page: Optional[PageFacts]) -> list[Signal]:
    host = (page.host if page and page.host else urlsplit(store_url).hostname) or "this site"
    signals = []
    https = page.https if page is not None else store_url.startswith("https://")
    if not https:
        signals.append(Signal("no_https", "strong", True, True, "This page is not using a secure connection (no HTTPS).",
                              [{"kind": "url", "ref": store_url}], 0.99))
    signals.append(Signal("store_checks", "strong", False, False,
                          f"Domain age, company record, photos and reviews for {host} were not checked "
                          "(only Store Lab stores are checked for now).", [{"kind": "url", "ref": store_url}], 0.2))
    return signals


async def _storelab(store_url: str, client: httpx.AsyncClient, peer_bases: Optional[list[str]]
                    ) -> tuple[list[Signal], Optional[str], str, bool]:
    found = await find_store_base(client, store_url)
    if found is None:
        return ([Signal("store_unreachable", "strong", False, False, "Couldn't check this store: it did not respond.",
                        [{"kind": "url", "ref": store_url}], 0.2)], None, store_url, False)
    base, about = found
    peers = sibling_stores(base) if peer_bases is None else peer_bases
    (reviews, reviews_url), (returns_html, returns_url), photos = await asyncio.gather(
        first_json(client, base, REVIEWS_PATHS), first_text(client, base, RETURNS_PATHS),
        check_copied_photos(client, base, peers))
    about_url = base + "/about.json"
    signals = [check_domain_age(about, about_url), check_registry(about, about_url), photos,
               *check_reviews(reviews, reviews_url), check_returns(returns_html, returns_url)]
    return signals, about.get("name"), base, True


async def assess_store(store_url: str, check_id: str, page: Optional[PageFacts] = None, *,
                       peer_bases: Optional[list[str]] = None, client: Optional[httpx.AsyncClient] = None,
                       use_cache: bool = True) -> TrustVerdict:
    """Vet one store. `store_url` may be any page of the store. Never raises; results are cached for CACHE_S per URL."""
    t0 = time.perf_counter()
    hit = _cache.get(store_url) if use_cache else None
    if hit and time.monotonic() - hit[0] < CACHE_S:
        old = hit[1]
        findings = [f.model_copy(update={"check_id": check_id, "ts": time.time()}) for f in old["findings"]]
        await _write_all(findings)
        return {**old, "findings": findings, "elapsed_ms": int((time.perf_counter() - t0) * 1000)}

    name, base, checked = None, store_url, False
    if is_storelab(store_url):
        own = client is None
        client = client or make_client()
        try:
            signals, name, base, checked = await _storelab(store_url, client, peer_bases)
        finally:
            if own:
                await client.aclose()
    else:
        signals = _real_page(store_url, check_id, page)

    verdict = decide(signals)
    if not checked and verdict == "safe":
        verdict = "caution"  # nothing verified is never "safe"
    # Only warning signs and checks that couldn't run become findings: the panel shows every trust row as a warning,
    # so a passing check ("Business address listed") would look alarming. Passing checks still feed decide() and the card.
    findings = [to_finding(check_id, s) for s in signals if s.flagged or not s.verified]
    await _write_all(findings)
    result = TrustVerdict(verdict=verdict, reasons=await _reword(reasons_for(signals)), findings=findings, store=name,
                          store_url=base, checked=checked, elapsed_ms=int((time.perf_counter() - t0) * 1000))
    log.info("trust %s -> %s in %d ms", base, verdict, result["elapsed_ms"])
    if checked:
        _cache[store_url] = (time.monotonic(), result)
    return result


def safe_verdict(tv: TrustVerdict) -> Verdict:
    """The panel's "Shop safe?" section (contracts.Verdict) from a TrustVerdict."""
    if not tv["checked"]:
        bad = [f for f in tv["findings"] if f.status != "unverified"]
        reasons = ([Reason(text=f.summary, tone="bad") for f in bad] +
                   [Reason(text=f.summary, tone="unknown") for f in tv["findings"] if f.status == "unverified"])
        return Verdict(status="bad" if bad else "unknown", headline="Risky" if bad else "Not fully checked", reasons=reasons)
    strong = {"domain_age", "no_company", "copied_photos", "no_https"}
    by_summary = {f.summary: f for f in tv["findings"]}
    reasons = []
    for text in tv["reasons"][:4]:
        f = by_summary.get(text)
        if tv["verdict"] == "safe" and (f is None or f.status != "unverified"):
            tone = "good" if text == "No warning signs found." else "caution"
        elif f is not None and f.status == "unverified":
            tone = "unknown"
        else:
            tone = "bad" if f is not None and f.type in strong else "caution"
        reasons.append(Reason(text=text.rstrip("."), tone=tone, proof="whois"))
    status = {"safe": "good", "caution": "caution", "unsafe": "bad"}[tv["verdict"]]
    headline = {"safe": "Looks safe", "caution": "Be careful", "unsafe": "Risky"}[tv["verdict"]]
    return Verdict(status=status, headline=headline, reasons=reasons)


def clear_cache() -> None:
    _cache.clear()
    clear_photo_cache()
