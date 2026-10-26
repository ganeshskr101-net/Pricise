"""The SYNTHETIC Store Lab catalog: made-up stores, listings, card offers, 90-day price histories and the demo wishlist.

One source of truth: scripts/seed.py writes this into the Pricise Postgres (which Zetaris queries), and DATA_SOURCE=fixture
reads it straight from here, so both modes give the same numbers. Every store is made up; every history is synthetic.

Days are offsets, not dates, so the data never goes stale: -89..0 are the 90 days up to demo Day 1 (0 = Day 1 = "today"),
1..8 are demo Days 2..9 (the wishlist week driven by POST /demo/clock).

Demo numbers (docs/PLAN.md): AudioDealz $149 "was $249" (2 of 90 days), real total $191 = 149 + 4.99 add-on + 37.01 fees.
A $239 + $13 shipping = $252 · B $199 refurbished · C $255 - $26 card offer = $229, Day 9 $235 - $26 = $209 · D $189 scam ·
E outage · F fake discount · G drip fees · H countdown + "only 2 left" · I clean.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

BASE_URL = "http://localhost:8787"
HISTORY_DAYS = 90
DEMO_DAYS = 9  # demo Day 1..9 -> day offsets 0..8
BENCH_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "price_history.json"


def key(s: str) -> str:
    """Matching key: lower case, letters and digits only ("WH-1000XM5" -> "wh1000xm5")."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# store_id, display name, page brand, kind, trust (ok | risky | scam | unknown), status (up | outage), trick on the page
STORES = [
    ("audiodealz", "AudioDealz", "AudioDealz", "storelab", "risky", "up", "fake 40% off, resetting timer, pre-ticked add-on, late fees"),
    ("store-a", "Store A", "Pinecrest Sound Co.", "storelab", "ok", "up", "none (shipping is extra)"),
    ("store-b", "Store B", "Second Spin Refurbs", "storelab", "ok", "up", "sells refurbished units"),
    ("store-c", "Store C", "storec", "storelab", "ok", "up", "none (card offer)"),
    ("store-d", "Store D", "Ultra Bargain Zone 24", "storelab", "scam", "up", "scam store: too-cheap price, no company record"),
    ("store-e", "Store E", "Maplehollow Hi-Fi", "storelab", "ok", "outage", "site down during checks"),
    ("store-f", "Store F", "Markdown Mountain", "storelab", "ok", "up", "fake discount"),
    ("store-g", "Store G", "Kestrelwick Electronics", "storelab", "ok", "up", "drip fees at checkout"),
    ("store-h", "Store H", "Tickdown Deals", "storelab", "ok", "up", "countdown timer + \"only 2 left\""),
    ("store-i", "Store I", "Quietlane Audio", "storelab", "ok", "up", "none (clean store)"),
]

# product_id, brand, family, model, name
XM5 = "sony-wh1000xm5"
XM4 = "sony-wh1000xm4"
PRODUCTS = [
    (XM5, "Sony", "WH-1000X", "WH-1000XM5", "Sony WH-1000XM5 Wireless Noise Cancelling Headphones"),
    (XM4, "Sony", "WH-1000X", "WH-1000XM4", "Sony WH-1000XM4 Wireless Noise Cancelling Headphones"),
]

# store_id, product_id, sku, condition, price (Day 1), was_price, shipping, fees (added at checkout), pre-ticked add-on
LISTINGS = [
    ("audiodealz", XM5, "WH1000XM5/B", "new", 149.00, 249.00, 0.00, 37.01, 4.99),
    ("store-a", XM5, "PCS-XM5-BLK", "new", 239.00, None, 13.00, 0.00, 0.00),
    ("store-b", XM5, "SSR-XM5-RFB", "refurbished", 199.00, None, 0.00, 0.00, 0.00),
    ("store-c", XM5, "SC-WH1000XM5", "new", 255.00, None, 0.00, 0.00, 0.00),
    ("store-d", XM5, "UBZ-XM5", "new", 189.00, None, 0.00, 0.00, 0.00),
    ("store-e", XM5, "MHF-XM5-B", "new", 245.00, None, 0.00, 0.00, 0.00),
    ("store-f", XM5, "MM-XM5", "new", 259.00, 399.00, 0.00, 0.00, 0.00),
    ("store-g", XM5, "KW-XM5-BK", "new", 219.00, None, 0.00, 29.00, 0.00),
    ("store-h", XM5, "TD-XM5", "new", 245.00, None, 9.99, 0.00, 0.00),
    ("store-i", XM5, "QL-XM5", "new", 249.00, None, 0.00, 0.00, 0.00),
    ("store-i", XM4, "QL-XM4", "new", 178.00, None, 0.00, 0.00, 0.00),  # older model: a variant mismatch the Price Agent rejects
]

# store_id, product_id, amount, description
CARD_OFFERS = [
    ("store-c", XM5, 26.00, "$26 off at checkout with a linked card"),
]

# Price history: [price, days] runs, oldest first, covering the 90 days up to Day 1. Then the demo week (Days 2..9):
# the Day 1 price, except where DEMO_WEEK says otherwise ({day: price}).
HISTORY_RUNS = {
    ("audiodealz", XM5): [[149, 30], [169, 7], [149, 3], [249, 2], [149, 48]],
    ("store-a", XM5): [[249, 40], [239, 50]],
    ("store-b", XM5): [[199, 90]],
    ("store-c", XM5): [[255, 90]],
    ("store-d", XM5): [[189, 90]],
    ("store-e", XM5): [[245, 90]],
    ("store-f", XM5): [[259, 50], [399, 3], [259, 37]],
    ("store-g", XM5): [[219, 90]],
    ("store-h", XM5): [[245, 90]],
    ("store-i", XM5): [[255, 30], [249, 60]],
    ("store-i", XM4): [[178, 90]],
}
DEMO_WEEK = {("store-c", XM5): {9: 235.00}}  # Day 9: Store C drops to $235 -> $209 after the card offer

# id, name, url, rule, target, best_price, best_store, status, tone, product_id
WISHLIST = [
    ("sony", "Sony WH-1000XM5", f"{BASE_URL}/storelab/audiodealz/", "Under $215 · new only", 215, 229, "Store C",
     "Watching", "unknown", XM5),
    ("kindle", "Kindle Paperwhite", None, "Under $120", 120, 139, "Store A", "Watching", "unknown", None),
    ("shoes", "Running shoes, size 10", None, "Under $90 · trusted only", 90, 84, "Store F", "Held: fake discount", "bad", None),
    ("fryer", "Air fryer, 6 qt", None, "Under $80", 80, 92, "Store B", "Wait: sale likely", "caution", None),
]


def store_path(store_id: str) -> str:
    return f"storelab/{store_id}"


def _expand(runs: list[list[float]]) -> list[float]:
    prices = [float(p) for p, days in runs for _ in range(int(days))]
    assert len(prices) == HISTORY_DAYS, f"history must cover {HISTORY_DAYS} days, got {len(prices)}"
    return prices


def _bench() -> dict:
    """Bench (labelled test page) histories from the JSON fixture, so DATA_SOURCE=zetaris covers them too."""
    return {s["product"]: s["runs"] for s in json.loads(BENCH_FIXTURE.read_text())["series"] if s["host"] == "bench/deal"}


@lru_cache(maxsize=1)
def rows() -> dict[str, list[dict]]:
    """Every table as a list of row dicts, exactly what scripts/seed.py inserts. Shared and cached: never mutate."""
    stores = [{"store_id": s, "name": n, "brand": b, "kind": k, "path": store_path(s), "url": f"{BASE_URL}/{store_path(s)}/",
               "trust": t, "status": st, "trick": tr, "synthetic": True} for s, n, b, k, t, st, tr in STORES]
    stores.append({"store_id": "bench-deal", "name": "Bench pages", "brand": "Bench", "kind": "bench", "path": "bench/deal",
                   "url": f"{BASE_URL}/bench/", "trust": "unknown", "status": "up", "trick": "labelled test pages",
                   "synthetic": True})
    products = [{"product_id": p, "brand": b, "family": f, "model": m, "model_key": key(m), "name": n}
                for p, b, f, m, n in PRODUCTS]
    listings = [{"listing_id": f"{s}:{p}", "store_id": s, "product_id": p, "sku": sku, "sku_key": key(sku), "condition": c,
                 "price": price, "was_price": was, "shipping": ship, "fees": fees, "addon": addon,
                 "url": f"{BASE_URL}/{store_path(s)}/"}
                for s, p, sku, c, price, was, ship, fees, addon in LISTINGS]
    history = []
    for (s, p), runs in HISTORY_RUNS.items():
        past = _expand(runs)
        for i, price in enumerate(past):
            history.append({"store_id": s, "product_id": p, "day_offset": i - (HISTORY_DAYS - 1), "price": price})
        for day in range(2, DEMO_DAYS + 1):
            price = DEMO_WEEK.get((s, p), {}).get(day, past[-1])
            history.append({"store_id": s, "product_id": p, "day_offset": day - 1, "price": price})
    for ident, runs in _bench().items():
        pid = f"bench-{key(ident)}"
        products.append({"product_id": pid, "brand": "Bench", "family": "bench", "model": ident, "model_key": key(ident),
                         "name": f"Bench item {ident}"})
        listings.append({"listing_id": f"bench-deal:{pid}", "store_id": "bench-deal", "product_id": pid, "sku": ident,
                         "sku_key": key(ident), "condition": "new", "price": float(runs[-1][0]), "was_price": None,
                         "shipping": 0.0, "fees": 0.0, "addon": 0.0, "url": f"{BASE_URL}/bench/"})
        for i, price in enumerate(_expand(runs)):
            history.append({"store_id": "bench-deal", "product_id": pid, "day_offset": i - (HISTORY_DAYS - 1), "price": price})
    offers = [{"offer_id": f"{s}:{p}", "store_id": s, "product_id": p, "amount": a, "description": d} for s, p, a, d in CARD_OFFERS]
    wishlist = [{"id": i, "name": n, "url": u, "rule": r, "target": t, "best_price": bp, "best_store": bs, "status": st,
                 "tone": tone, "product_id": pid, "sort": idx} for idx, (i, n, u, r, t, bp, bs, st, tone, pid) in enumerate(WISHLIST)]
    return {"stores": stores, "products": products, "listings": listings, "price_history": history,
            "card_offers": offers, "wishlist": wishlist, "findings": []}
