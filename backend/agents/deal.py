"""Deal Agent: answers "Honest deal?" by catching four tricks, each with proof.

Tricks: fake discount (price history), resetting timer (3 fresh visits), pre-ticked add-on (page signals), drip fees (checkout run-ahead).
Two modes:
- Real store pages: READ ONLY. Uses only the PageFacts the extension sent. No hidden browser, no clicks, nothing loaded from here.
- Store Lab pages (localhost + /storelab/ or /bench/): the full agent, including the hidden-browser run-ahead and the timer test.
Tools run in parallel, each with a timeout (8 s; the timer test gets its gaps + 15 s). A tool that fails returns an "unverified"
finding saying what couldn't be checked; the agent never raises. Decisions follow explicit rules, never the model alone.

Streaming: the fast tools (page signals, add-ons, price history) finish in well under a second. On Store Lab pages the slow
ones (run-ahead, timer test) first stream a "still checking" placeholder row (model == PENDING), then the real finding when
they finish. The timer-test result for a Store Lab URL is reused for 10 minutes unless a fresh check is asked for.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import AsyncIterator, Awaitable, Callable, Optional

from backend.contracts import Finding, PageFacts
from backend.tools.addons import check_addons
from backend.tools.common import ToolResult, finding, is_storelab
from backend.tools.discount import check_discount
from backend.tools.runahead import check_runahead
from backend.tools.timer_test import check_timer, timeout_s

log = logging.getLogger("pricise.deal")
TOOL_TIMEOUT_S = 8.0
FAST_TOOLS = ("addons", "discount", "scarcity")
SLOW_TOOLS = ("runahead", "timer")
PENDING = "pending"  # Finding.model of a "still checking" placeholder row; the tool's real result replaces it
PLACEHOLDER = {"timer": ("countdown", "Checking if the timer resets…"), "runahead": ("drip_fees", "Checking checkout for late fees…")}
TIMER_CACHE_S = 600

# What each tool checks, for "couldn't check" findings and for the Skeptic's rerun.
TOOL_TYPE = {"addons": "prechecked_addon", "discount": "claimed_discount", "timer": "resetting_timer",
             "runahead": "drip_fees", "scarcity": "scarcity"}
TOOL_WHAT = {"addons": "pre-ticked add-ons", "discount": "the discount against price history", "timer": "whether the countdown resets",
             "runahead": "late checkout fees", "scarcity": "pressure messages"}
TYPE_TOOL = {"prechecked_addon": "addons", "fake_discount": "discount", "claimed_discount": "discount",
             "inflated_discount": "discount",
             "resetting_timer": "timer", "countdown": "timer", "drip_fees": "runahead", "scarcity": "scarcity"}

RUNS: dict[str, dict] = {}      # check_id -> {"url", "page", "rounds"} so the Skeptic can ask for a rerun
EVIDENCE: dict[str, dict] = {}  # check_id -> data for the evidence page (price_history, timer_frames, fees, total)
TIMER_CACHE: dict[str, tuple[float, ToolResult]] = {}  # Store Lab URL -> (when, timer-test result)


def check_scarcity(check_id: str, page: PageFacts) -> ToolResult:
    """Stock / viewers claims can't be verified from a page, so they stay "proposed"."""
    out, seen = ToolResult(), set()
    for s in page.signals:
        if s.type != "scarcity" or (s.selector or s.text) in seen:
            continue
        seen.add(s.selector or s.text)
        ev = [{"kind": "selector", "ref": s.selector, "label": "Pressure message"}] if s.selector else \
            [{"kind": "log", "ref": "page-signal:scarcity", "label": " ".join(s.text.split())[:70]}]
        out.findings.append(finding(check_id, "scarcity", "proposed",
                                    f"Pressure message: \"{' '.join(s.text.split())[:70]}\". Stock claims can't be verified from the page.",
                                    ev, confidence=0.5))
    return out


def _timer_cached(url: str) -> Optional[tuple[float, ToolResult]]:
    hit = TIMER_CACHE.get(url.split("#")[0])
    return hit if hit and time.time() - hit[0] < TIMER_CACHE_S else None


async def _timer(check_id: str, url: str, page: PageFacts, fresh: bool) -> ToolResult:
    """The timer test, reusing a Store Lab URL's result from the last 10 minutes unless fresh (the panel's refresh button)."""
    hit = None if fresh else _timer_cached(url)
    if hit:
        when, res = hit
        age = round(time.time() - when)
        ago = f"{age // 60} min ago" if age >= 60 else f"{age} s ago"
        out = ToolResult(evidence_data=dict(res.evidence_data))
        for f in res.findings:  # same proof, re-stamped for this check
            out.findings.append(f.model_copy(update={"check_id": check_id, "ts": time.time(),
                                                     "summary": f"{f.summary} (Timer test from {ago}.)"}))
        return out
    res = await check_timer(check_id, url, page)
    if is_storelab(url):
        TIMER_CACHE[url.split("#")[0]] = (time.time(), res)
    return res


def _tools(check_id: str, url: str, page: PageFacts, fresh: bool = False) -> dict[str, tuple[Callable[[], Awaitable[ToolResult]], float]]:
    async def sync(fn, *a):
        return fn(*a)

    lab = is_storelab(url)
    return {
        "addons": (lambda: sync(check_addons, check_id, page), TOOL_TIMEOUT_S),
        "discount": (lambda: asyncio.to_thread(check_discount, check_id, url, page), discount_timeout()),  # may search (cached)
        "scarcity": (lambda: sync(check_scarcity, check_id, page), TOOL_TIMEOUT_S),
        "timer": (lambda: _timer(check_id, url, page, fresh), timeout_s() if lab else TOOL_TIMEOUT_S),
        "runahead": (lambda: check_runahead(check_id, url, page), TOOL_TIMEOUT_S),
    }


def discount_timeout() -> float:
    """The discount check reads the same safe_price as the Price Agent (repo.usl_offers: one shared read, up to its 15 s),
    so with the USL on it gets that limit plus 2 s to compare; otherwise the usual 8 s."""
    from backend.data import repo, zetaris

    usl = os.environ.get("PRICISE_USL", "on").strip().lower() != "off" and zetaris.use_zetaris()
    return repo.USL_TIMEOUT_S + 2.0 if usl else TOOL_TIMEOUT_S


def _why(err: BaseException, timeout: float) -> str:
    if isinstance(err, asyncio.TimeoutError):
        return f"it took longer than {timeout:g} s"
    if isinstance(err, ImportError) or "Executable doesn't exist" in str(err):
        return "the hidden browser (Playwright + Chromium) isn't installed"
    return f"the check failed ({type(err).__name__})"


async def _run(name: str, check_id: str, make: Callable[[], Awaitable[ToolResult]], timeout: float) -> ToolResult:
    t0 = time.perf_counter()
    try:
        res = await asyncio.wait_for(make(), timeout)
    except Exception as err:  # never raise: say what we couldn't check
        res = ToolResult([finding(check_id, TOOL_TYPE[name], "unverified",
                                  f"Couldn't check {TOOL_WHAT[name]}: {_why(err, timeout)}.", confidence=0.0)])
    secs = round(time.perf_counter() - t0, 2)
    res.evidence_data.setdefault("timings", {})[name] = secs
    log.info("deal check=%s tool=%s took %.2fs findings=%d", check_id, name, secs, len(res.findings))
    return res


def _merge(check_id: str, data: dict) -> None:
    ev = EVIDENCE.setdefault(check_id, {})
    for k, v in data.items():
        if k == "timings":
            ev.setdefault("timings", {}).update(v)
        elif v is not None:
            ev[k] = v


def placeholders(check_id: str, url: str, page: PageFacts, fresh: bool = False) -> list[Finding]:
    """Rows that say a slow Store Lab check is still running, so the panel and card can show the verdict straight away."""
    if not is_storelab(url):
        return []
    out = [finding(check_id, PLACEHOLDER["runahead"][0], "proposed", PLACEHOLDER["runahead"][1], model=PENDING, confidence=0.0)]
    has_timer = any(s.type in ("countdown", "resetting_timer") for s in page.signals)
    if has_timer and (fresh or not _timer_cached(url)):
        out.append(finding(check_id, PLACEHOLDER["timer"][0], "proposed", PLACEHOLDER["timer"][1], model=PENDING, confidence=0.0))
    return out


async def iter_deal(check_id: str, url: str, page: PageFacts, *, fresh: bool = False,
                    on_tool_done: Optional[Callable[[str, float], None]] = None) -> AsyncIterator[Finding]:
    """Yields placeholders for the slow Store Lab checks first, then findings as each tool finishes (fast ones first).
    on_tool_done(name, seconds) is called after each tool's findings have been yielded."""
    RUNS[check_id] = {"url": url, "page": page, "rounds": {}}
    for f in placeholders(check_id, url, page, fresh):
        yield f

    async def named(name, make, timeout):
        return name, await _run(name, check_id, make, timeout)

    tasks = [asyncio.create_task(named(name, make, timeout))
             for name, (make, timeout) in _tools(check_id, url, page, fresh).items()]
    try:
        for next_done in asyncio.as_completed(tasks):
            name, res = await next_done
            _merge(check_id, res.evidence_data)
            for f in res.findings:
                yield f
            if on_tool_done:
                on_tool_done(name, res.evidence_data["timings"][name])
    finally:
        for t in tasks:
            t.cancel()


async def assess_deal(check_id: str, url: str, page: PageFacts, *, fresh: bool = False) -> list[Finding]:
    """All of the agent's findings for a page (placeholders left out: they only matter while streaming)."""
    return [f async for f in iter_deal(check_id, url, page, fresh=fresh) if f.model != PENDING]


async def rerun(check_id: str, finding_type: str) -> Finding:
    """Skeptic hook, e.g. rerun(cid, "resetting_timer") reruns the timer test. Returns the fresh finding for that trick."""
    run = RUNS.get(check_id)
    name = TYPE_TOOL.get(finding_type)
    if not run or not name:
        why = "no earlier check with this id" if not run else f"unknown finding type {finding_type!r}"
        return finding(check_id, finding_type, "unverified", f"Couldn't rerun: {why}.", confidence=0.0)
    rounds = run["rounds"][finding_type] = run["rounds"].get(finding_type, 0) + 1
    make, timeout = _tools(check_id, run["url"], run["page"], fresh=True)[name]  # a rerun never uses the timer cache
    res = await _run(name, check_id, make, timeout)
    _merge(check_id, res.evidence_data)
    fresh = next((f for f in res.findings if TYPE_TOOL.get(f.type) == name), None)
    if fresh is None:
        fresh = finding(check_id, finding_type, "rejected", f"Rerun found no sign of this: {TOOL_WHAT[name]} looked fine.",
                        confidence=0.7)
    fresh.rounds = rounds
    fresh.challenged_by = "skeptic"
    return fresh
