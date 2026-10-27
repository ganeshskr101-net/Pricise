"""Price history access. ALL price-history reads go through get_price_history, so the Deal Agent never cares where data lives.

host:    the store, e.g. "storelab/audiodealz" for Store Lab pages, or the real host ("shop.example") for real pages.
product: an identifier from the page (SKU, model or name); matched case- and punctuation-insensitively against the store's
         listing SKU or the product's model.

DATA_SOURCE=fixture (default): the synthetic catalog in backend/data/catalog.py, in memory.
DATA_SOURCE=zetaris: one Lightning SQL query through Zetaris. If Zetaris fails, falls back to the catalog (logged), so the demo
never breaks. The result is a History: a plain list of (date, price) plus .source, .sql and .raw (the rows Zetaris returned),
which the fake-discount finding cites as evidence. Day offsets map to dates ending today (offset 0 = today).
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Optional

from backend.data import catalog, zetaris

log = logging.getLogger("pricise.data")
HOST_RE = re.compile(r"[a-z0-9.:/-]+")


class History(list):
    """[(date, price), ...] oldest first, plus where it came from."""

    def __init__(self, rows, source: str, sql: Optional[str] = None, raw: Optional[list[dict]] = None):
        super().__init__(rows)
        self.source, self.sql, self.raw = source, sql, raw or []


def _key(s: str) -> str:
    return catalog.key(s)


def _dated(offsets_prices: list[tuple[int, float]]) -> list[tuple[date, float]]:
    today = date.today()
    return [(today + timedelta(days=d), p) for d, p in sorted(offsets_prices)]


def history_sql(path: str, want: str) -> str:
    ds, k, p = zetaris.datasource(), zetaris.lit(want), zetaris.lit(path)
    return (f"SELECT h.day_offset, h.price FROM {ds}.price_history h "
            f"JOIN {ds}.listings l ON l.store_id = h.store_id AND l.product_id = h.product_id "
            f"JOIN {ds}.products p ON p.product_id = l.product_id "
            f"JOIN {ds}.stores s ON s.store_id = l.store_id "
            f"WHERE s.path = {p} AND (l.sku_key = {k} OR p.model_key = {k}) "
            f"AND h.day_offset BETWEEN -{catalog.HISTORY_DAYS - 1} AND 0 ORDER BY h.day_offset")


def _from_zetaris(path: str, want: str) -> Optional[History]:
    sql = history_sql(path, want)
    raw = zetaris.run_sql(sql)
    if not raw:
        return None
    return History(_dated([(int(r["day_offset"]), float(r["price"])) for r in raw]), "zetaris", sql, raw)


def _from_catalog(path: str, want: str) -> Optional[History]:
    data = catalog.rows()
    store = next((s for s in data["stores"] if s["path"] == path), None)
    if not store:
        return None
    models = {p["product_id"]: p["model_key"] for p in data["products"]}
    pids = {l["product_id"] for l in data["listings"]
            if l["store_id"] == store["store_id"] and want in (l["sku_key"], models[l["product_id"]])}
    rows = [(r["day_offset"], r["price"]) for r in data["price_history"]
            if r["store_id"] == store["store_id"] and r["product_id"] in pids and r["day_offset"] <= 0]
    return History(_dated(rows), "fixture") if rows else None


def get_price_history(host: str, product: str) -> Optional[list[tuple[date, float]]]:
    """Daily (date, price) rows for this product at this store, oldest first, or None when we have no history."""
    h = (host or "").lower().removeprefix("www.")
    want = _key(product)
    if not want or not HOST_RE.fullmatch(h):
        return None
    if zetaris.use_zetaris():
        try:
            return _from_zetaris(h, want)
        except (zetaris.ZetarisError, ValueError) as e:
            log.warning("price history from Zetaris failed, using the catalog fixture: %s", e)
    return _from_catalog(h, want)



# ----------------------------------------------------------------------------- real pages: the page's own history, observations

MIN_SPAN_DAYS = 30  # fewer days than this is too little to judge a discount


def daily_from_points(points: list[tuple[date, float]], today: Optional[date] = None) -> list[tuple[date, float]]:
    """Price-change points -> one row per day (a price holds until the next point), up to today, last 90 days."""
    pts = sorted(points)
    if not pts:
        return []
    today = today or date.today()
    out, i = [], 0
    day = pts[0][0]
    while day <= today:
        while i + 1 < len(pts) and pts[i + 1][0] <= day:
            i += 1
        out.append((day, pts[i][1]))
        day += timedelta(days=1)
    return out[-catalog.HISTORY_DAYS:]


def page_history(signals) -> Optional[History]:
    """The "Price history" a real page shows itself (content.js price_history signal), as daily rows; None if absent."""
    for s in signals or []:
        if getattr(s, "type", None) == "price_history":
            pts = []
            for d, p in (s.detail or {}).get("points") or []:
                try:
                    pts.append((date.fromisoformat(str(d)[:10]), float(p)))
                except (TypeError, ValueError):
                    continue
            rows = daily_from_points(pts)
            return History(rows, "page") if rows else None
    return None


def observation_history(host: str, model_key: str, url: str = "") -> Optional[History]:
    """Prices this shopper saw for the same item on the same host (price observations), as daily rows; None if none."""
    from backend.data import repo

    rows = repo.own_observations(host, model_key, url)
    pts = [(date.fromtimestamp(float(r["seen_at"])), float(r["price"])) for r in rows]
    daily = daily_from_points(pts)
    return History(daily, "observations") if daily else None
