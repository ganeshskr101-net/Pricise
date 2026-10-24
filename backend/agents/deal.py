"""Deal Agent: answers "Honest deal?" by catching four tricks, each with proof.

Tricks: fake discount (price history), resetting timer (3 fresh visits), pre-ticked add-on (page signals), drip fees (checkout run-ahead).
Two modes:
- Real store pages: READ ONLY. Uses only the PageFacts the extension sent. No hidden browser, no clicks, nothing loaded from here.
- Store Lab pages (localhost + /storelab/ or /bench/): the full agent, including the hidden-browser run-ahead and the timer test.
Tools run in parallel, each with a timeout (8 s; the timer test gets its gaps + 15 s). A tool that fails returns an "unverified"
finding saying what couldn't be checked; the agent never raises. Decisions follow explicit rules, never the model alone.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Awaitable, Callable

from backend.contracts import Finding, PageFacts
from backend.tools.addons import check_addons
from backend.tools.common import ToolResult, finding, is_storelab
from backend.tools.discount import check_discount
from backend.tools.runahead import check_runahead
from backend.tools.timer_test import check_timer, timeout_s

TOOL_TIMEOUT_S = 8.0

# What each tool checks, for "couldn't check" findings and for the Skeptic's rerun.
TOOL_TYPE = {"addons": "prechecked_addon", "discount": "claimed_discount", "timer": "resetting_timer",
             "runahead": "drip_fees", "scarcity": "scarcity"}
TOOL_WHAT = {"addons": "pre-ticked add-ons", "discount": "the discount against price history", "timer": "whether the countdown resets",
             "runahead": "late checkout fees", "scarcity": "pressure messages"}
TYPE_TOOL = {"prechecked_addon": "addons", "fake_discount": "discount", "claimed_discount": "discount",
             "resetting_timer": "timer", "countdown": "timer", "drip_fees": "runahead", "scarcity": "scarcity"}

RUNS: dict[str, dict] = {}      # check_id -> {"url", "page", "rounds"} so the Skeptic can ask for a rerun
EVIDENCE: dict[str, dict] = {}  # check_id -> data for the evidence page (price_history, timer_frames, fees, total)


def check_scarcity(check_id: str, page: PageFacts) -> ToolResult:
    """Stock / viewers claims can't be verified from a page, so they stay "proposed"."""
    out, seen = ToolResult(), set()
    for s in page.signals:
        if s.type != "scarcity" or (s.selector or s.text) in seen:
            continue
        seen.add(s.selector or s.text)
        ev = [{"kind": "selector", "ref": s.selector, "label": "Pressure message"}] if s.selector else []
        out.findings.append(finding(check_id, "scarcity", "proposed",
                                    f"Pressure message: \"{' '.join(s.text.split())[:70]}\". Stock claims can't be verified from the page.",
                                    ev, confidence=0.5))
    return out


def _tools(check_id: str, url: str, page: PageFacts) -> dict[str, tuple[Callable[[], Awaitable[ToolResult]], float]]:
    async def sync(fn, *a):
        return fn(*a)

    lab = is_storelab(url)
    return {
        "addons": (lambda: sync(check_addons, check_id, page), TOOL_TIMEOUT_S),
        "discount": (lambda: sync(check_discount, check_id, url, page), TOOL_TIMEOUT_S),
        "scarcity": (lambda: sync(check_scarcity, check_id, page), TOOL_TIMEOUT_S),
        "timer": (lambda: check_timer(check_id, url, page), timeout_s() if lab else TOOL_TIMEOUT_S),
        "runahead": (lambda: check_runahead(check_id, url, page), TOOL_TIMEOUT_S),
    }


def _why(err: BaseException, timeout: float) -> str:
    if isinstance(err, asyncio.TimeoutError):
        return f"it took longer than {timeout:g} s"
    if isinstance(err, ImportError) or "Executable doesn't exist" in str(err):
        return "the hidden browser (Playwright + Chromium) isn't installed"
    return f"the check failed ({type(err).__name__})"


async def _run(name: str, check_id: str, make: Callable[[], Awaitable[ToolResult]], timeout: float) -> ToolResult:
    try:
        return await asyncio.wait_for(make(), timeout)
    except Exception as err:  # never raise: say what we couldn't check
        return ToolResult([finding(check_id, TOOL_TYPE[name], "unverified",
                                   f"Couldn't check {TOOL_WHAT[name]}: {_why(err, timeout)}.", confidence=0.0)])


def _merge(check_id: str, data: dict) -> None:
    EVIDENCE.setdefault(check_id, {}).update({k: v for k, v in data.items() if v is not None})


async def iter_deal(check_id: str, url: str, page: PageFacts) -> AsyncIterator[Finding]:
    """Yields findings as each tool finishes (fast ones first; the timer test last)."""
    RUNS[check_id] = {"url": url, "page": page, "rounds": {}}
    tasks = [asyncio.create_task(_run(name, check_id, make, timeout))
             for name, (make, timeout) in _tools(check_id, url, page).items()]
    try:
        for next_done in asyncio.as_completed(tasks):
            res = await next_done
            _merge(check_id, res.evidence_data)
            for f in res.findings:
                yield f
    finally:
        for t in tasks:
            t.cancel()


async def assess_deal(check_id: str, url: str, page: PageFacts) -> list[Finding]:
    return [f async for f in iter_deal(check_id, url, page)]


async def rerun(check_id: str, finding_type: str) -> Finding:
    """Skeptic hook, e.g. rerun(cid, "resetting_timer") reruns the timer test. Returns the fresh finding for that trick."""
    run = RUNS.get(check_id)
    name = TYPE_TOOL.get(finding_type)
    if not run or not name:
        why = "no earlier check with this id" if not run else f"unknown finding type {finding_type!r}"
        return finding(check_id, finding_type, "unverified", f"Couldn't rerun: {why}.", confidence=0.0)
    rounds = run["rounds"][finding_type] = run["rounds"].get(finding_type, 0) + 1
    make, timeout = _tools(check_id, run["url"], run["page"])[name]
    res = await _run(name, check_id, make, timeout)
    _merge(check_id, res.evidence_data)
    fresh = next((f for f in res.findings if TYPE_TOOL.get(f.type) == name), None)
    if fresh is None:
        fresh = finding(check_id, finding_type, "rejected", f"Rerun found no sign of this: {TOOL_WHAT[name]} looked fine.",
                        confidence=0.7)
    fresh.rounds = rounds
    fresh.challenged_by = "skeptic"
    return fresh
