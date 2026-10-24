"""Price history access. ALL price-history reads go through get_price_history, so Josh can swap the JSON fixture for a
Zetaris query (same signature) without touching the Deal Agent.

host:    the store, e.g. "storelab/audiodealz" for Store Lab pages, or the real host ("shop.example") for real pages.
product: an identifier from the page (SKU, model or name); matched case- and punctuation-insensitively.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Optional

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "price_history.json"


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


@lru_cache(maxsize=1)
def _series() -> list[dict]:
    return json.loads(FIXTURE.read_text())["series"]


def _expand(runs: list[list[float]], today: date) -> list[tuple[date, float]]:
    prices = [float(p) for p, days in runs for _ in range(int(days))]
    start = today - timedelta(days=len(prices) - 1)
    return [(start + timedelta(days=i), p) for i, p in enumerate(prices)]


def get_price_history(host: str, product: str) -> Optional[list[tuple[date, float]]]:
    """Daily (date, price) rows for this product at this store, oldest first, or None when we have no history."""
    h = (host or "").lower().removeprefix("www.")
    want = _key(product)
    if not want:
        return None
    for s in _series():
        if s["host"] != h:
            continue
        if want in {_key(k) for k in [s["product"], *s.get("aliases", [])]}:
            return _expand(s["runs"], date.today())
    return None
