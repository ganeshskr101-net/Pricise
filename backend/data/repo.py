"""Pricise data access beyond price history: listings for the Price Agent, the wishlist, stored findings.

Reads: DATA_SOURCE=zetaris -> Lightning SQL through Zetaris REST; otherwise the in-memory catalog (backend/data/catalog.py).
Writes (wishlist, findings): straight to the Pricise Postgres (backend/data/pg.py), which is the database Zetaris reads, so the
next Zetaris read sees them. Why not write through Zetaris: tested on 2.4.3.1, UPDATE on a JDBC datasource is
unsupported (SparkUnsupportedOperationException) and INSERT needs every column, ids included (no defaults/serials), ~2 s each.
Every function raises ZetarisError / PgError on failure; callers decide the fallback.
"""
from __future__ import annotations

import json
from typing import Optional

from backend.contracts import Finding, WishlistItem
from backend.data import catalog, pg, zetaris

MONEY = ("price", "shipping", "fees", "card_offer", "true_total")
WISHLIST_COLS = ("id", "name", "url", "rule", "target", "best_price", "best_store", "status", "tone")


def _num(v) -> Optional[float]:
    return None if v is None or v == "" or str(v).lower() == "null" else round(float(v), 2)


# ----------------------------------------------------------------------------- listings (Price Agent)


def listings_sql(day: int) -> str:
    """Every Store Lab listing with its price on demo `day` (1..9) and its true total, cheapest first."""
    ds, off = zetaris.datasource(), int(day) - 1
    return (f"SELECT s.store_id, s.name AS store, s.url, s.trust, s.status, l.product_id, p.family, p.model, p.model_key, "
            f"l.sku_key, l.condition, COALESCE(h.price, l.price) AS price, l.shipping, l.fees, "
            f"COALESCE(o.amount, 0) AS card_offer, "
            f"COALESCE(h.price, l.price) + l.shipping + l.fees - COALESCE(o.amount, 0) AS true_total "
            f"FROM {ds}.listings l "
            f"JOIN {ds}.stores s ON s.store_id = l.store_id "
            f"JOIN {ds}.products p ON p.product_id = l.product_id "
            f"LEFT JOIN {ds}.price_history h ON h.store_id = l.store_id AND h.product_id = l.product_id AND h.day_offset = {off} "
            f"LEFT JOIN {ds}.card_offers o ON o.store_id = l.store_id AND o.product_id = l.product_id "
            f"WHERE s.kind = 'storelab' ORDER BY true_total, s.name")


def _catalog_listings(day: int) -> list[dict]:
    data, off = catalog.rows(), int(day) - 1
    stores = {s["store_id"]: s for s in data["stores"]}
    products = {p["product_id"]: p for p in data["products"]}
    hist = {(r["store_id"], r["product_id"]): r["price"] for r in data["price_history"] if r["day_offset"] == off}
    offers = {(o["store_id"], o["product_id"]): o["amount"] for o in data["card_offers"]}
    out = []
    for l in data["listings"]:
        s, p = stores[l["store_id"]], products[l["product_id"]]
        if s["kind"] != "storelab":
            continue
        price = hist.get((s["store_id"], p["product_id"]), l["price"])
        offer = offers.get((s["store_id"], p["product_id"]), 0.0)
        out.append({"store_id": s["store_id"], "store": s["name"], "url": s["url"], "trust": s["trust"], "status": s["status"],
                    "product_id": p["product_id"], "family": p["family"], "model": p["model"], "model_key": p["model_key"],
                    "sku_key": l["sku_key"], "condition": l["condition"], "price": price, "shipping": l["shipping"],
                    "fees": l["fees"], "card_offer": offer, "true_total": price + l["shipping"] + l["fees"] - offer})
    return sorted(out, key=lambda r: (r["true_total"], r["store"]))


def listings(day: int = 1) -> tuple[list[dict], Optional[str], str]:
    """(rows, sql or None, source). Money columns are floats in both modes."""
    if zetaris.use_zetaris():
        sql = listings_sql(day)
        rows, source = zetaris.run_sql(sql), "zetaris"
    else:
        rows, sql, source = _catalog_listings(day), None, "fixture"
    return [{**r, **{k: _num(r.get(k)) for k in MONEY}} for r in rows], sql, source


# ----------------------------------------------------------------------------- wishlist


def get_wishlist() -> list[WishlistItem]:
    ds = zetaris.datasource()
    rows = zetaris.run_sql(f"SELECT {', '.join(WISHLIST_COLS)} FROM {ds}.wishlist ORDER BY sort, id")
    return [WishlistItem(**{k: (None if v in (None, "null") else v) for k, v in r.items()}) for r in rows]


def add_wishlist(item: WishlistItem) -> None:
    with pg.connect() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO wishlist (id, name, url, rule, target, best_price, best_store, status, tone, sort) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, (SELECT COALESCE(MAX(sort), 0) + 1 FROM wishlist))",
                    [getattr(item, c) for c in WISHLIST_COLS])


def update_wishlist(item_id: str, **fields) -> None:
    cols = [c for c in fields if c in WISHLIST_COLS and c != "id"]
    if not cols:
        return
    with pg.connect() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE wishlist SET {', '.join(f'{c} = %s' for c in cols)} WHERE id = %s",
                    [fields[c] for c in cols] + [item_id])


# ----------------------------------------------------------------------------- findings


def save_findings(check_id: str, url: str, findings: list[Finding]) -> int:
    """Store a finished check's findings (one transaction). Returns the number written."""
    rows = [(check_id, url, f.agent, f.type, f.status, f.summary, json.dumps([e.model_dump() for e in f.evidence]),
             f.confidence, f.model, f.challenged_by, f.rounds, f.ts) for f in findings]
    if not rows:
        return 0
    with pg.connect() as conn, conn.cursor() as cur:
        cur.executemany("INSERT INTO findings (check_id, url, agent, type, status, summary, evidence, confidence, model, "
                        "challenged_by, rounds, ts) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", rows)
    return len(rows)


def count_findings(check_id: str) -> int:
    """How many findings Zetaris sees for a check (proof that writes land where Zetaris reads)."""
    ds = zetaris.datasource()
    rows = zetaris.run_sql(f"SELECT COUNT(*) AS n FROM {ds}.findings WHERE check_id = {zetaris.lit(check_id)}")
    return int(rows[0]["n"]) if rows else 0
