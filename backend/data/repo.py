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
import os
import re
import threading
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


# ----------------------------------------------------------------------------- shopping offers (SerpApi cache, 6 h)

OFFER_COLS = ("source", "domain", "title", "price", "shipping", "total", "shipping_note", "condition", "link")
OFFERS_MEMORY: dict[str, tuple[float, list[dict]]] = {}
TOKEN_COLS = ("token", "multi")  # Postgres columns Zetaris doesn't read: the sellers-lookup token and "several stores"


def save_offers(query_key: str, offers: list[dict], fetched_at: float) -> None:
    if not zetaris.use_zetaris():
        OFFERS_MEMORY[query_key] = (fetched_at, offers)
        return
    rows = offers or [{"source": "", "price": 0}]  # marker: searched, nothing found
    cols = (*OFFER_COLS, *TOKEN_COLS)  # the sellers-lookup token is kept too (read back by cached_tokens, Postgres only)
    try:
        with pg.connect() as conn, conn.cursor() as cur:
            cur.executemany(f"INSERT INTO shopping_offers (query_key, fetched_at, {', '.join(cols)}) "
                            f"VALUES (%s, %s, {', '.join(['%s'] * len(cols))})",
                            [[query_key, fetched_at, *[o.get(c) for c in cols]] for o in rows])
    except pg.PgError as e:
        log.warning("shopping offers not cached: %s", e)


def offers_sql(query_key: str, since: float) -> str:
    return (f"SELECT fetched_at, {', '.join(OFFER_COLS)} FROM {zetaris.datasource()}.shopping_offers "
            f"WHERE query_key = {zetaris.lit(query_key)} AND fetched_at > {float(since):.0f} ORDER BY fetched_at DESC, total")


def cached_offers(query_key: str, since: float) -> Optional[list[dict]]:
    """The newest cached search for this query since `since` (offers, possibly []), or None when there is none."""
    if not zetaris.use_zetaris():
        hit = OFFERS_MEMORY.get(query_key)
        return list(hit[1]) if hit and hit[0] > since else None
    try:
        rows = zetaris.run_sql(offers_sql(query_key, since))
    except zetaris.ZetarisError as e:  # e.g. shopping_offers not registered in Zetaris yet
        log.warning("shopping offers from Zetaris failed, reading the Pricise Postgres directly: %s", e)
        try:
            with pg.connect() as conn, conn.cursor() as cur:
                cur.execute(f"SELECT fetched_at, {', '.join(OFFER_COLS)} FROM shopping_offers WHERE query_key = %s "
                            "AND fetched_at > %s ORDER BY fetched_at DESC, total", [query_key, since])
                names = [d.name for d in cur.description]
                rows = [dict(zip(names, r)) for r in cur.fetchall()]
        except pg.PgError as e2:
            log.warning("shopping offers cache not read: %s", e2)
            return None
    rows = [r for r in rows if r.get("fetched_at") is not None]
    if not rows:
        return None
    newest = max(float(r["fetched_at"]) for r in rows)
    out = []
    for r in rows:
        if float(r["fetched_at"]) != newest or not r.get("source"):
            continue
        o = {c: r.get(c) for c in OFFER_COLS}
        for c in ("price", "shipping", "total"):
            o[c] = _num(o[c])
        out.append(o)
    return out



def cached_tokens(query_key: str, since: float) -> list[dict]:
    """The newest cached search's offers WITH their sellers-lookup tokens ({source, title, price, total, condition,
    token, multi}), straight from Postgres (Zetaris doesn't read these columns). [] when there are none."""
    if not zetaris.use_zetaris():
        hit = OFFERS_MEMORY.get(query_key)
        return [o for o in hit[1] if o.get("token")] if hit and hit[0] > since else []
    try:
        with pg.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT source, title, price, total, condition, token, multi FROM shopping_offers WHERE query_key = %s "
                        "AND token IS NOT NULL AND fetched_at = (SELECT max(fetched_at) FROM shopping_offers "
                        "WHERE query_key = %s AND fetched_at > %s)", [query_key, query_key, since])
            names = [d.name for d in cur.description]
            rows = [dict(zip(names, r)) for r in cur.fetchall()]
    except pg.PgError as e:
        log.warning("cached sellers tokens not read: %s", e)
        return []
    for r in rows:
        r["price"], r["total"], r["multi"] = _num(r["price"]), _num(r["total"]), bool(r["multi"])
    return rows


def own_observations(host: str, model_key: str, url: str = "") -> list[dict]:
    """Observations of this item on THIS host (same model, or the same page URL), oldest first. Direct read: the
    discount check runs in the Deal Agent's fast tools, so no Zetaris round trip here."""
    clean = clean_url(url) if url else ""
    if not zetaris.use_zetaris():
        rows = [o for o in OBS_MEMORY if o["host"] == host and ((model_key and o["model_key"] == model_key) or (clean and o["url"] == clean))]
        return sorted(rows, key=lambda o: o["seen_at"])
    try:
        with pg.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT price, seen_at FROM price_observations WHERE host = %s AND ((%s <> '' AND model_key = %s) "
                        "OR (%s <> '' AND url = %s)) ORDER BY seen_at", [host, model_key, model_key, clean, clean])
            return [{"price": _num(p), "seen_at": float(s)} for p, s in cur.fetchall()]
    except pg.PgError as e:
        log.warning("own observations not read: %s", e)
        return []


# ----------------------------------------------------------------------------- the USL (docs/zetaris/USL.md)

USL_TABLE = "lightning.metastore.pricise.pricise_usl.safe_price"
USL_ERRORS: dict[str, str] = {}  # product key -> why the last USL read didn't give offers
USL_TIMEOUT_S = 15  # Zetaris recomputes safe_price on every read (~5 s): no documented, configured way to precompute it
USL_COLS = ("offer_source", "retailer", "store_domain", "seller", "title", "price", "item_condition", "full_price",
            "warranty_note", "domain_age_days", "unusually_low", "is_safe", "origin")
USL_FETCHED: dict[str, list[dict]] = {}  # product key -> when the last USL read's offers were fetched, oldest first
# The Google Shopping search cached in Zetaris (pricise_usl.sql step 2): SerpApi stamps every response with its time (UTC)
SNAPSHOT_SQL = "SELECT search_metadata.created_at AS created FROM pricise_rest.serp_jbl_flip6"
SNAPSHOT_LABEL = "Google Shopping (Zetaris snapshot)"
CACHE_LABEL = "Google Shopping (Pricise cache)"
_SNAPSHOT_AT: list = []  # [read at, fetched at]: the snapshot only changes when its CACHE TABLE is re-run


def usl_offers_sql(product_key: str) -> str:
    return (f"SELECT {', '.join(USL_COLS)} FROM {USL_TABLE} WHERE product_key = {zetaris.lit(product_key)} "
            "ORDER BY price, offer_source")


USL_SHARE_S = 30  # the Price and Deal Agents of one check run in parallel: they share one safe_price read
USL_MEMO: dict[str, tuple[float, tuple]] = {}  # product key -> (read at, (offers, sql))
_USL_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def usl_offers(product_key: str) -> tuple[Optional[list[dict]], Optional[str]]:
    """safe_price for one product, read once per check: the Price and Deal Agents both ask (in parallel), the second
    waits for the first's read and reuses it, success or failure, for USL_SHARE_S seconds. See _read_usl."""
    import time

    if os.environ.get("PRICISE_USL", "on").strip().lower() == "off" or not zetaris.use_zetaris():
        return None, None
    with _LOCKS_GUARD:
        lock = _USL_LOCKS.setdefault(product_key, threading.Lock())
    with lock:
        hit = USL_MEMO.get(product_key)
        if hit and time.time() - hit[0] < USL_SHARE_S:
            return ([dict(o) for o in hit[1][0]] if hit[1][0] else None), hit[1][1]
        out = _read_usl(product_key)
        USL_MEMO[product_key] = (time.time(), out)
        return ([dict(o) for o in out[0]] if out[0] else None), out[1]


def _read_usl(product_key: str) -> tuple[Optional[list[dict]], Optional[str]]:
    """Offers for one product from the USL's safe_price table (Google Shopping via SerpApi, joined with RDAP domain age and
    Pricise's own prices, inside Zetaris), in the shape shopping.parse() gives. (None, sql) when it has no rows or
    Zetaris can't answer: the caller falls back to today's search. PRICISE_USL=off skips it."""
    sql = usl_offers_sql(product_key)
    try:
        rows = zetaris.run_sql(sql, timeout=USL_TIMEOUT_S)
    except zetaris.ZetarisError as e:
        log.warning("safe_price from the USL failed, using the search: %s", e)
        USL_ERRORS[product_key] = zetaris.redact(str(e))[:160]  # the evidence page says why it fell back
        return None, sql
    USL_ERRORS.pop(product_key, None)
    if not rows:
        USL_ERRORS[product_key] = "no rows for this product in safe_price"
    offers, seen = [], set()
    for r in rows:
        # safe_price has the live search (REST table) and the same search from Pricise's cache: one offer once
        key = (r.get("offer_source"), r.get("title"), r.get("price"), r.get("item_condition"), r.get("full_price"))
        if key in seen:
            continue
        seen.add(key)
        price = float(r["price"]) if r.get("price") not in (None, "") else None
        if not price or price <= 0:
            continue
        cond = (r.get("item_condition") or "new").strip().lower()
        full = str(r.get("full_price")) != "0"
        why = None if cond == "new" and full else (f'"{cond}" in its condition' if cond != "new" else '"monthly" in its price')
        offers.append({"title": r.get("title") or "", "source": r.get("offer_source") or "", "domain": r.get("store_domain") or None,
                       "price": round(price, 2), "shipping": None, "total": round(price, 2), "shipping_note": "shipping not shown",
                       "condition": f"excluded: {why}" if why else "new", "link": None, "token": None,
                       "usl": {k: r.get(k) for k in ("warranty_note", "domain_age_days", "unusually_low", "is_safe", "origin")}})
    _stamp(product_key, offers)
    return (offers or None), sql


def _stamp(product_key: str, offers: list[dict]) -> None:
    """Each USL offer's fetched_at (epoch) from its origin, and USL_FETCHED[product_key] for the evidence page and panel:
    safe_price is a snapshot, not a live search, so every place that shows these prices says when they were fetched."""
    origins = {(o.get("usl") or {}).get("origin") for o in offers}
    at = {"serpapi_live": snapshot_fetched_at() if "serpapi_live" in origins else None,
          "pricise_cache": cache_fetched_at(product_key) if "pricise_cache" in origins else None}
    for o in offers:
        o["fetched_at"] = at.get((o.get("usl") or {}).get("origin"))
    stamps = [{"what": SNAPSHOT_LABEL, "at": at["serpapi_live"]}, {"what": CACHE_LABEL, "at": at["pricise_cache"]}]
    USL_FETCHED[product_key] = sorted((x for x in stamps if x["at"]), key=lambda x: x["at"])


def snapshot_fetched_at() -> Optional[float]:
    """When SerpApi ran the search cached in Zetaris (its search_metadata.created_at). A read of the cached REST table:
    no new search. Remembered for 10 minutes; None when Zetaris doesn't answer."""
    import time
    from datetime import datetime, timezone

    if _SNAPSHOT_AT and time.time() - _SNAPSHOT_AT[0] < 600:
        return _SNAPSHOT_AT[1]
    try:
        created = zetaris.run_sql(SNAPSHOT_SQL)[0]["created"]
        at = datetime.strptime(created, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc).timestamp()
    except (zetaris.ZetarisError, LookupError, ValueError, TypeError) as e:
        log.warning("snapshot time not read: %s", zetaris.redact(str(e))[:160])
        return None
    _SNAPSHOT_AT[:] = [time.time(), at]
    return at


def cache_fetched_at(product_key: str) -> Optional[float]:
    """The newest cached search for this product (or its sellers list) in Pricise's own Postgres."""
    try:
        with pg.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT max(fetched_at) FROM shopping_offers WHERE query_key IN (%s, %s)",
                        [product_key, product_key + "sellers"])
            row = cur.fetchone()
    except pg.PgError as e:
        log.warning("cache time not read: %s", e)
        return None
    return float(row[0]) if row and row[0] is not None else None


def fetched_text(stamps: list[dict]) -> str:
    """"Google Shopping (Zetaris snapshot), fetched 3:36 AM Oct 27": local time (the backend runs on the shopper's
    machine). The panel and evidence page say "today" when the date is today."""
    from datetime import datetime

    def clock(at: float) -> str:
        d = datetime.fromtimestamp(at)
        return f"{d:%I}".lstrip("0") + f":{d:%M %p} {d:%b} {d.day}"

    return "; ".join(f"{s['what']}, fetched {clock(s['at'])}" for s in stamps if s.get("at"))
