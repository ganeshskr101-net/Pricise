"""Pricise data access beyond price history: listings for the Price Agent, the wishlist, stored findings.

Reads: DATA_SOURCE=zetaris -> Lightning SQL through Zetaris REST; otherwise the in-memory catalog (backend/data/catalog.py).
Writes (wishlist, findings): straight to the Pricise Postgres (backend/data/pg.py), which is the database Zetaris reads, so the
next Zetaris read sees them. Why not write through Zetaris: tested on 2.4.3.1, UPDATE on a JDBC datasource is
unsupported (SparkUnsupportedOperationException) and INSERT needs every column, ids included (no defaults/serials), ~2 s each.
Every function raises ZetarisError / PgError on failure; callers decide the fallback.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from backend.contracts import Finding, WishlistItem
from backend.data import catalog, pg, zetaris

log = logging.getLogger("pricise.data")
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


# ----------------------------------------------------------------------------- price observations (real store pages)
# Every real product page Pricise checks leaves one row: host, brand, model/sku, name, price, condition, url (no query
# string), seen_at. Nothing personal. Same url + price again within DEDUPE_S is not saved twice. Writes go to the Pricise
# Postgres (DATA_SOURCE=zetaris) or to memory (fixture mode); reads go through Zetaris, or straight to that Postgres if the
# price_observations table isn't registered in Zetaris yet (logged), or memory.

OBS_COLS = ("host", "brand", "model", "model_key", "sku", "name", "price", "currency", "condition", "safe", "url", "seen_at")
DEDUPE_S = 3600
OBS_MEMORY: list[dict] = []
_REFURB_RE = re.compile(r"\b(refurbished|renewed|reconditioned)\b", re.I)
_USED_RE = re.compile(r"\b(used|pre-?owned|open[- ]box|second[- ]hand)\b", re.I)


def clean_url(url: str) -> str:
    """The page URL without query string or fragment (they can carry session, tracking or account ids)."""
    s = urlsplit(url or "")
    return urlunsplit((s.scheme, s.netloc, s.path, "", ""))


def condition_of(name: Optional[str]) -> str:
    return "refurbished" if _REFURB_RE.search(name or "") else "used" if _USED_RE.search(name or "") else "new"


def save_observation(obs: dict) -> bool:
    """Store one observation. False = a duplicate (same url and price within the hour) or nothing to store."""
    obs = {k: obs.get(k) for k in OBS_COLS}
    if not obs["host"] or obs["price"] is None or not obs["url"] or not (obs["model_key"] or obs["sku"] or obs["name"]):
        return False
    since = float(obs["seen_at"]) - DEDUPE_S
    if not zetaris.use_zetaris():
        if any(o["url"] == obs["url"] and o["price"] == obs["price"] and o["seen_at"] > since for o in OBS_MEMORY):
            return False
        OBS_MEMORY.append(obs)
        return True
    with pg.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM price_observations WHERE url = %s AND price = %s AND seen_at > %s LIMIT 1",
                    [obs["url"], obs["price"], since])
        if cur.fetchone():
            return False
        cur.execute(f"INSERT INTO price_observations ({', '.join(OBS_COLS)}) VALUES ({', '.join(['%s'] * len(OBS_COLS))})",
                    [obs[c] for c in OBS_COLS])
    return True


def observations_sql(model_key: str, condition: str, host: str) -> str:
    ds = zetaris.datasource()
    return (f"SELECT host, brand, model, name, price, currency, condition, safe, url, seen_at FROM {ds}.price_observations "
            f"WHERE model_key = {zetaris.lit(model_key)} AND condition = {zetaris.lit(condition)} "
            f"AND host <> {zetaris.lit(host)} ORDER BY price, seen_at DESC LIMIT 20")


def _obs_row(r: dict) -> dict:
    return {**r, "price": _num(r.get("price")), "seen_at": float(r.get("seen_at") or 0)}


def other_observations(model_key: str, condition: str, host: str) -> tuple[list[dict], Optional[str], str]:
    """(rows cheapest first, sql or None, source) for the same model and condition seen on OTHER hosts, never an unsafe one."""
    if not model_key:
        return [], None, "none"
    if not zetaris.use_zetaris():
        rows = [o for o in OBS_MEMORY if o["model_key"] == model_key and o["condition"] == condition and o["host"] != host]
        return sorted(rows, key=lambda o: (o["price"], -o["seen_at"])), None, "memory"
    sql = observations_sql(model_key, condition, host)
    try:
        rows, source = zetaris.run_sql(sql), "zetaris"
    except zetaris.ZetarisError as e:  # e.g. price_observations not registered in Zetaris yet
        log.warning("price observations from Zetaris failed, reading the Pricise Postgres directly: %s", e)
        with pg.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT host, brand, model, name, price, currency, condition, safe, url, seen_at "
                        "FROM price_observations WHERE model_key = %s AND condition = %s AND host <> %s "
                        "ORDER BY price, seen_at DESC LIMIT 20", [model_key, condition, host])
            names = [d.name for d in cur.description]
            rows, source = [dict(zip(names, r)) for r in cur.fetchall()], "postgres"
    return [_obs_row(r) for r in rows if r.get("safe") != "bad"], sql, source
