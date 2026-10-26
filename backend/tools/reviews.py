"""Review-pattern check: bursts (more than 40% of reviews inside any 7-day window) and duplicate review text."""
from __future__ import annotations

import re
from collections import Counter
from datetime import date
from typing import Optional

from backend.tools.trust_common import Signal

BURST_SHARE = 0.40
BURST_WINDOW_DAYS = 7
MIN_REVIEWS = 10   # fewer is too little data to call a pattern
DUP_SHARE = 0.20   # more than 20% of reviews share their exact text with another review


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def max_window_share(days: list[date]) -> tuple[float, Optional[date]]:
    """Largest share of reviews inside any BURST_WINDOW_DAYS window, and where that window starts."""
    days = sorted(days)
    best, best_start, j = 0, None, 0
    for i, start in enumerate(days):
        while j < len(days) and (days[j] - start).days < BURST_WINDOW_DAYS:
            j += 1
        if j - i > best:
            best, best_start = j - i, start
    return (best / len(days) if days else 0.0), best_start


def check_reviews(reviews: object, reviews_url: str) -> list[Signal]:
    """Two weak signals: review_burst and duplicate_reviews."""
    ev = [{"kind": "url", "ref": reviews_url}]
    if not isinstance(reviews, list) or len(reviews) < MIN_REVIEWS:
        msg = (f"Too few reviews to judge ({len(reviews)})." if isinstance(reviews, list)
               else "Couldn't read this store's reviews.")
        return [Signal("review_burst", "weak", False, False, msg, ev, 0.3),
                Signal("duplicate_reviews", "weak", False, False, msg, ev, 0.3)]
    reviews = [r for r in reviews if isinstance(r, dict)]
    days = []
    for r in reviews:
        try:
            days.append(date.fromisoformat(str(r.get("date"))[:10]))
        except ValueError:
            continue
    share, start = max_window_share(days)
    burst = Signal("review_burst", "weak", share > BURST_SHARE, bool(days),
                   f"{share:.0%} of reviews were posted in a single week." if share > BURST_SHARE
                   else "Reviews are spread out over time.",
                   ev + [{"kind": "log", "ref": f"max_7day_share={share:.2f} window_start={start} reviews={len(days)}"}], 0.8)
    counts = Counter(t for t in (_norm(str(r.get("text", ""))) for r in reviews) if len(t) >= 8)
    dup_n = sum(c for c in counts.values() if c > 1)
    dup_share = dup_n / max(len(reviews), 1)
    top = counts.most_common(1)[0][1] if counts else 0
    dup = Signal("duplicate_reviews", "weak", dup_share > DUP_SHARE, True,
                 f"{dup_n} reviews repeat the same text." if dup_share > DUP_SHARE else "No copy-pasted reviews.",
                 ev + [{"kind": "log", "ref": f"duplicate_share={dup_share:.2f} most_repeated={top}x"}], 0.85)
    return [burst, dup]
