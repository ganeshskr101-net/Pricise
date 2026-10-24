"""Fake vs real discount, decided from price history only.

Rule (explicit, no model): the discount is FAKE when the was-price appeared on <= 7 of the last 90 days AND the sale price
equals the typical (median) price over those days. No history -> "claimed_discount", unverified. Careful wording on real stores.
"""
from __future__ import annotations

import re
import statistics
from datetime import date, timedelta
from typing import Optional
from urllib.parse import urlparse

from backend.contracts import PageFacts
from backend.data.history import get_price_history
from backend.tools.common import ToolResult, finding, is_storelab, money

WINDOW_DAYS = 90
MAX_DAYS_AT_WAS = 7
PCT_RE = re.compile(r"(\d{1,2})\s?%")


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.5, 0.01 * max(a, b))


def store_key(url: str, page: PageFacts) -> str:
    """Store Lab stores share one host, so key them by path: storelab/audiodealz, bench/deal. Real stores: the host."""
    if is_storelab(url):
        parts = [p for p in urlparse(url).path.split("/") if p]
        for root in ("storelab", "bench"):
            if root in parts and parts.index(root) + 1 < len(parts):
                return f"{root}/{parts[parts.index(root) + 1]}"
    return (page.host or urlparse(url).netloc).lower()


def claimed_was(page: PageFacts) -> tuple[Optional[float], Optional[int]]:
    """The was-price the page claims (from discount signals or the product), plus a claimed % if shown."""
    price = page.product.price
    amounts = [s.amount for s in page.signals if s.type == "claimed_discount" and s.amount and (price is None or s.amount > price)]
    was = max(amounts) if amounts else page.product.was_price
    pct = None
    for s in page.signals:
        m = PCT_RE.search(s.text) if s.type == "claimed_discount" else None
        if m:
            pct = int(m.group(1))
            break
    if was is None and pct and price:
        was = round(price / (1 - pct / 100), 2)
    return was, pct


def _history(url: str, page: PageFacts):
    host = store_key(url, page)
    p = page.product
    for ident in (p.sku, p.model, p.name):
        if ident:
            rows = get_price_history(host, ident)
            if rows:
                return host, ident, rows
    return host, None, None


def check_discount(check_id: str, url: str, page: PageFacts) -> ToolResult:
    out = ToolResult()
    claims = [s for s in page.signals if s.type == "claimed_discount"]
    was, pct = claimed_was(page)
    price = page.product.price
    if not claims and was is None:
        return out  # the page makes no discount claim
    seen, sel = set(), []
    for s in claims:
        if s.selector and s.selector not in seen:
            seen.add(s.selector)
            sel.append(s.selector)
    claim_text = " ".join((claims[0].text if claims else f"was {money(was)}").split())[:80]

    host, ident, rows = _history(url, page)
    if not rows or price is None or was is None:
        why = "no price history found for this item" if not rows else "the page's prices couldn't be read"
        out.findings.append(finding(check_id, "claimed_discount", "unverified",
                                    f"The page claims a reduced price ({claim_text}). {why.capitalize()}, so this is unverified.",
                                    [{"kind": "selector", "ref": r, "label": "Discount claim" if i == 0 else ""} for i, r in enumerate(sel)],
                                    confidence=0.4))
        return out

    since = date.today() - timedelta(days=WINDOW_DAYS - 1)
    recent = [p for d, p in rows if d >= since]
    typical = statistics.median(recent)
    days_at_was = sum(1 for p in recent if _same(p, was))
    days_at_price = sum(1 for p in recent if _same(p, price))
    pct_shown = pct or round((1 - price / was) * 100)
    out.evidence_data["price_history"] = {
        "store": host.split("/")[-1].replace("audiodealz", "AudioDealz"),
        "label": "Synthetic history (Store Lab)" if is_storelab(url) else f"Price history for {host}",
        "points": recent, "claimed_was": was, "days_at_claim": days_at_was, "days_at_price": days_at_price,
    }
    rows_ev = {"kind": "price_row", "ref": f"history:{host}:{ident}",
               "label": f"{money(price)} on {days_at_price} of {len(recent)} days; {money(was)} on {days_at_was} days; median {money(typical)}"}

    if days_at_was <= MAX_DAYS_AT_WAS and _same(price, typical):
        summary = (f"\"{pct_shown}% off\" is not real: the price was {money(price)} for {days_at_price} of the last {len(recent)} days. "
                   f"{money(was)} appeared for {days_at_was} day{'s' if days_at_was != 1 else ''} only.")
        if not is_storelab(url):
            summary = (f"This was-price isn't supported by the price history we found: {money(price)} was the usual price "
                       f"({days_at_price} of {len(recent)} days) and {money(was)} appeared on only {days_at_was} days.")
        ev = [{"kind": "selector", "ref": r, "label": f"Fake {pct_shown}% off" if i == 0 else ""} for i, r in enumerate(sel)]
        out.findings.append(finding(check_id, "fake_discount", "confirmed", summary, ev + [rows_ev], confidence=0.93))
    else:
        summary = (f"The price history supports this discount: {money(was)} was charged on {days_at_was} of the last {len(recent)} days "
                   f"and the usual price is {money(typical)}.")
        out.findings.append(finding(check_id, "claimed_discount", "confirmed", summary, [rows_ev], confidence=0.8))
    return out
