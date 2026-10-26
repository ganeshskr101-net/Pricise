"""Resetting countdown test.

Store Lab: load the page in 3 fresh browser contexts TIMER_GAP_S apart (default 3 s in SAMPLE_MODE, 120 s otherwise), read the
countdown and screenshot it each time. A real deadline keeps counting down between visits; a fake one restarts.
Real pages: never load the site from here; pass through what the extension saw (countdown / resetting_timer signals).
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Optional

from backend.contracts import PageFacts, PageSignal
from backend.tools.browser import chromium
from backend.tools.common import ToolResult, finding, is_storelab
from backend.tools.runahead import TRACES

VISITS = 3
TIMER_RE = re.compile(r"\b(\d{1,2}):([0-5]\d)(?::([0-5]\d))?\b(?!\s?(am|pm))", re.I)
FIND_TIMER_JS = """(src) => {
  const re = new RegExp(src, 'i');
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = w.nextNode())) {
    const p = n.parentElement;
    if (p && !['SCRIPT', 'STYLE'].includes(p.tagName) && re.test(n.nodeValue) && p.innerText.length < 60) return p.innerText;
  }
  return null;
}"""


def timer_gap_s() -> float:
    if os.environ.get("TIMER_GAP_S"):
        return float(os.environ["TIMER_GAP_S"])
    # 3 s is enough to prove a reset: a real countdown drops ~3 s between visits, a fake one shows the same start again.
    return 3.0 if os.environ.get("SAMPLE_MODE", "true").lower() in {"1", "true", "yes"} else 120.0


def timeout_s(gap: Optional[float] = None) -> float:
    """Timeout for the whole test: the gaps plus time to load and read the page each visit."""
    return (VISITS - 1) * (timer_gap_s() if gap is None else gap) + 15


def to_seconds(text: Optional[str]) -> Optional[int]:
    m = TIMER_RE.search(text or "")
    if not m:
        return None
    a, b, c = int(m.group(1)), int(m.group(2)), m.group(3)
    return a * 3600 + b * 60 + int(c) if c is not None else a * 60 + b


def shown(text: str) -> str:
    m = TIMER_RE.search(text or "")
    return m.group(0) if m else text.strip()[:12]


def restarted(frames: list[dict], tolerance_s: float = 1.5) -> int:
    """How many later visits showed MORE time left than a real countdown could (previous minus elapsed)."""
    n = 0
    for a, b in zip(frames, frames[1:]):
        if a["remaining_s"] is None or b["remaining_s"] is None:
            continue
        if b["remaining_s"] > a["remaining_s"] - (b["at"] - a["at"]) + tolerance_s:
            n += 1
    return n


async def sample_timer(check_id: str, url: str, selector: Optional[str], gap: float, visits: int = VISITS) -> list[dict]:
    if not is_storelab(url):
        raise PermissionError("the timer test only loads Store Lab pages")
    out_dir = TRACES / (re.sub(r"[^A-Za-z0-9_-]", "", check_id) or "check")
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    async with chromium() as browser:
        for v in range(1, visits + 1):
            if v > 1:
                await asyncio.sleep(gap)
            ctx = await browser.new_context(viewport={"width": 1280, "height": 900})  # fresh: no cookies, no storage
            page = await ctx.new_page()
            page.set_default_timeout(6000)
            await page.goto(url, wait_until="load")
            await page.wait_for_timeout(300)
            el = page.locator(selector).first if selector else None
            if el is not None and await el.count():
                text = await el.inner_text()
            else:
                el, text = None, await page.evaluate(FIND_TIMER_JS, TIMER_RE.pattern.replace("(?!\\s?(am|pm))", ""))
            at = time.time()
            shot = out_dir / f"timer-visit{v}.png"
            await (el.screenshot(path=str(shot)) if el is not None else page.screenshot(path=str(shot)))
            frames.append({"visit": v, "at": at, "time": time.strftime("%H:%M:%S", time.localtime(at)),
                           "shown": shown(text or ""), "remaining_s": to_seconds(text),
                           "screenshot": f"/traces/{out_dir.name}/{shot.name}"})
            await ctx.close()
            if frames[0]["remaining_s"] is None:
                break  # no countdown on the page: no need to wait for more visits
    return frames


def _lead(text: str) -> str:
    """The words in front of the countdown, e.g. "⏱ Deal ends in 09:58" -> "deal ends"."""
    lead = re.sub(r"[^A-Za-z ]", " ", TIMER_RE.split(text or "")[0])
    lead = re.sub(r"\s+(in|at)\s*$", "", " ".join(lead.split()), flags=re.I).lower()
    return lead if 3 <= len(lead) <= 40 else ""


def _timer_signal(page: PageFacts) -> Optional[PageSignal]:
    for kind in ("resetting_timer", "countdown"):
        for s in page.signals:
            if s.type == kind:
                return s
    return None


async def check_timer(check_id: str, url: str, page: PageFacts, gap: Optional[float] = None) -> ToolResult:
    sig = _timer_signal(page)
    if not is_storelab(url):
        return passthrough(check_id, sig)
    gap = timer_gap_s() if gap is None else gap
    frames = await sample_timer(check_id, url, sig.selector if sig else None, gap)
    out = ToolResult()
    if all(f["remaining_s"] is None for f in frames):
        return out  # no countdown on the page
    resets = restarted(frames)
    if not resets:
        return out  # it kept counting down: the deadline looks real
    same = len({f["shown"] for f in frames}) == 1
    name = f"The \"{_lead(sig.text)}\" timer" if sig and _lead(sig.text) else "The countdown"
    summary = (f"{name} restarts at {frames[0]['shown']} on every visit (seen on {len(frames)} visits)." if same
               else f"{name} restarted on {resets} of {len(frames) - 1} later visits, so the deadline is not real.")
    ev = [{"kind": "selector", "ref": sig.selector, "label": "Timer resets"}] if sig and sig.selector else []
    ev += [{"kind": "screenshot", "ref": f["screenshot"], "label": f"Visit {f['visit']} · {f['time']} · {f['shown']}"} for f in frames]
    out.findings.append(finding(check_id, "resetting_timer", "confirmed", summary, ev, confidence=0.97 if same else 0.9,
                                model="hidden browser, 3 fresh visits + rules"))
    out.evidence_data["timer_frames"] = [{"visit": f["visit"], "time": f["time"], "shown": f["shown"], "screenshot": f["screenshot"],
                                          "at": f["at"], "remaining_s": f["remaining_s"]}  # for skeptic.visits_from_frames
                                         for f in frames]
    return out


def passthrough(check_id: str, sig: Optional[PageSignal]) -> ToolResult:
    """Real pages: what the extension saw. Its own revisit check sets resetting_timer; otherwise it's just a countdown."""
    out = ToolResult()
    if not sig:
        return out
    ev = [{"kind": "selector", "ref": sig.selector, "label": "Timer resets" if sig.type == "resetting_timer" else "Countdown"}] \
        if sig.selector else [{"kind": "log", "ref": f"page-signal:{sig.type}", "label": " ".join(sig.text.split())[:70]}]
    if sig.type == "resetting_timer":
        d = sig.detail or {}
        extra = f" (it showed more time left after {d['seconds_since_last_visit']} s)" if d.get("seconds_since_last_visit") else ""
        out.findings.append(finding(check_id, "resetting_timer", "confirmed",
                                    f"This countdown restarted since your last visit{extra}, so the deadline is not real.", ev,
                                    confidence=0.85))
    else:
        out.findings.append(finding(check_id, "countdown", "proposed",
                                    f"Countdown timer on the page ({' '.join(sig.text.split())[:40]}). "
                                    "Pricise will check if it resets on your next visit.", ev, confidence=0.5))
    return out
