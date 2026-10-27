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
import logging
import os
import time
from typing import Literal, NotRequired, Optional, TypedDict
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
    checked: bool             # False = the store checks did not run (store didn't answer, or no registry answer for a real site)
    elapsed_ms: int
    passed: NotRequired[list[str]]  # checks that ran and found nothing wrong (real sites list them; shown green)


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
    prompt = (meterless.trusted_prefix("Rewrite each line as one short sentence a shopper understands. Keep the same facts "
                                       "and the same number of lines, one per line, no bullets, nothing new.\n")
              + meterless.untrusted("\n".join(reasons)))  # reasons can quote page text (store name, reviews)
    try:
        text = await asyncio.wait_for(asyncio.to_thread(meterless.complete, prompt, "fast", 160), SUMMARY_TIMEOUT_S)
    except Exception:  # noqa: BLE001 - cosmetic only
        return reasons
    lines = [ln.strip(" -•\t") for ln in (text or "").splitlines() if ln.strip()]
    return lines if len(lines) == len(reasons) else reasons


async def _real_site(store_url: str, page: Optional[PageFacts], client: httpx.AsyncClient) -> tuple[list[Signal], bool]:
    """A real store page: READ ONLY, nothing is fetched from the store. HTTPS from the extension; domain age from public
    registration data (RDAP, backend/tools/rdap.py); returns and contact links from the page facts (the extension's
    "store_info" signal). Company record and reviews have no data source for real stores, so they stay unverified.
    checked = the registry answered (otherwise nothing independent was verified)."""
    from backend.tools import rdap

    host = (page.host if page and page.host else urlsplit(store_url).hostname) or "this site"
    signals = []
    https = page.https if page is not None else store_url.startswith("https://")
    signals.append(Signal("no_https", "strong", not https, True,
                          "Secure connection (HTTPS)." if https else "This page is not using a secure connection (no HTTPS).",
                          [{"kind": "url", "ref": store_url}], 0.99))
    domain = rdap.registrable(host)
    created = await rdap.registered_on(client, domain)
    if created:
        signals.append(check_domain_age({"domain_created": created.isoformat()}, rdap.rdap_url(domain)))
    else:
        signals.append(Signal("domain_age", "strong", False, False, f"Couldn't look up when {domain} was registered.",
                              [{"kind": "url", "ref": rdap.rdap_url(domain)}], 0.3))
    info = next((s for s in (page.signals if page else []) if s.type == "store_info"), None)
    if info is not None:
        d = info.detail or {}
        ev = [{"kind": "text", "ref": f"page links: returns={bool(d.get('returns'))} contact={bool(d.get('contact'))}"}]
        signals.append(Signal("bad_returns", "weak", not d.get("returns"), True,
                              "Returns policy link on the page." if d.get("returns")
                              else "No returns or refund policy link found on this page.", ev, 0.6))
        signals.append(Signal("no_contact", "weak", not d.get("contact"), True,
                              "Contact or customer service link on the page." if d.get("contact")
                              else "No contact or customer service link found on this page.", ev, 0.6))
    else:
        signals.append(Signal("store_links", "weak", False, False, "Returns and contact links weren't read from this page.",
                              [], 0.2))
    signals.append(Signal("store_records", "weak", False, False,
                          f"Company record and reviews for {host} aren't checked (no data source for real stores yet).", [], 0.2))
    return signals, created is not None


def real_reasons(signals: list[Signal]) -> list[str]:
    """Real sites: warning signs first, then what was checked and fine, then what couldn't be checked."""
    flagged = sorted((s for s in signals if s.flagged), key=lambda s: s.strength != "strong")
    passed = [s for s in signals if s.verified and not s.flagged]
    unchecked = [s for s in signals if not s.verified and not s.flagged]
    return list(dict.fromkeys(s.reason for s in [*flagged, *passed, *unchecked]))


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
        own = client is None
        client = client or make_client()
        try:
            signals, checked = await _real_site(store_url, page, client)
        finally:
            if own:
                await client.aclose()

    verdict = decide(signals)
    if not checked and verdict == "safe":
        verdict = "caution"  # nothing verified is never "safe"
    # Only warning signs and checks that couldn't run become findings: the panel shows every trust row as a warning,
    # so a passing check ("Business address listed") would look alarming. Passing checks still feed decide() and the card.
    findings = [to_finding(check_id, s) for s in signals if s.flagged or not s.verified]
    real = not is_storelab(store_url)
    reasons = real_reasons(signals) if real else reasons_for(signals)
    result = TrustVerdict(verdict=verdict, reasons=await _reword(reasons), findings=findings, store=name,
                          store_url=base, checked=checked, elapsed_ms=int((time.perf_counter() - t0) * 1000))
    if real:
        result["passed"] = [s.reason for s in signals if s.verified and not s.flagged]
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
        return Verdict(status="bad" if bad else "unknown", headline="Risky" if bad else "Unknown", reasons=reasons)
    strong = {"domain_age", "no_company", "copied_photos", "no_https"}
    by_summary = {f.summary: f for f in tv["findings"]}
    reasons = []
    passed = set(tv.get("passed") or ())
    for text in tv["reasons"][:5]:
        f = by_summary.get(text)
        if text in passed:  # a real-site check that ran and found nothing wrong
            tone = "good"
        elif tv["verdict"] == "safe" and (f is None or f.status != "unverified"):
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
