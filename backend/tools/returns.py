"""Returns-policy check: flags a missing policy, 'no returns' wording, or a return window under 7 days."""
from __future__ import annotations

import html
import re
from typing import Optional

from backend.tools.trust_common import Signal

MIN_RETURN_DAYS = 7
_NO_RETURNS = re.compile(
    r"\b(no returns|all sales (are )?final|non[- ]?refundable|returns (are )?not accepted|no refunds)\b", re.I)
_DAYS = re.compile(r"\b(\d{1,3})[\s-]*(?:calendar\s+|business\s+)?days?\b", re.I)


def visible_text(page: str) -> str:
    page = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", page)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())


def check_returns(page: Optional[str], returns_url: str) -> Signal:
    ev = [{"kind": "url", "ref": returns_url}]
    text = visible_text(page) if page else ""
    if len(text) < 20:
        return Signal("bad_returns", "weak", True, True, "No returns policy found.", ev, 0.75)
    m = _NO_RETURNS.search(text)
    if m:
        return Signal("bad_returns", "weak", True, True, "The store says it accepts no returns.",
                      ev + [{"kind": "text", "ref": m.group(0)}], 0.9)
    days = [int(d) for d in _DAYS.findall(text)]
    if not days:
        return Signal("bad_returns", "weak", False, False, "There is a returns policy, but no clear return window.", ev, 0.4)
    window = max(days)
    ev.append({"kind": "log", "ref": f"return_window_days={window}"})
    if window < MIN_RETURN_DAYS:
        return Signal("bad_returns", "weak", True, True, f"Only {window} days to return items.", ev, 0.85)
    return Signal("bad_returns", "weak", False, True, f"{window}-day returns.", ev, 0.85)
