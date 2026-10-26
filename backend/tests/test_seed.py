"""The synthetic catalog that scripts/seed.py writes: counts and the demo numbers from docs/PLAN.md. The live check reads the
Pricise Postgres (never re-seeds it) and skips itself when the database isn't running."""
import pytest

from backend.data import catalog, pg

DATA = catalog.rows()


def _hist(store, product=catalog.XM5):
    rows = sorted((r for r in DATA["price_history"] if r["store_id"] == store and r["product_id"] == product),
                  key=lambda r: r["day_offset"])
    return [r["price"] for r in rows if r["day_offset"] <= 0], {r["day_offset"] + 1: r["price"] for r in rows if r["day_offset"] > 0}


def _listing(store, product=catalog.XM5):
    return next(r for r in DATA["listings"] if r["store_id"] == store and r["product_id"] == product)


EXPECTED = {"stores": 11, "products": 9, "listings": 18, "price_history": 11 * 98 + 7 * 90, "card_offers": 1, "wishlist": 4,
            "findings": 0}


def test_seed_counts():
    assert {t: len(r) for t, r in DATA.items()} == EXPECTED
    assert {s["store_id"] for s in DATA["stores"] if s["kind"] == "storelab"} == \
        {"audiodealz", *(f"store-{c}" for c in "abcdefghi")}
    assert all(s["synthetic"] for s in DATA["stores"])


def test_every_series_has_90_days_and_storelab_has_the_demo_week():
    for s, p in catalog.HISTORY_RUNS:
        past, week = _hist(s, p)
        assert len(past) == 90 and sorted(week) == list(range(2, 10))


def test_audiodealz_numbers():
    l = _listing("audiodealz")
    assert (l["price"], l["was_price"]) == (149, 249)
    assert round(l["price"] + l["addon"] + l["fees"] + l["shipping"], 2) == 191.00
    past, _ = _hist("audiodealz")
    assert past.count(249) == 2 and past.count(149) == 81 and past[-1] == 149


def test_other_store_numbers():
    a = _listing("store-a")
    assert a["price"] + a["shipping"] == 252
    assert _listing("store-b")["condition"] == "refurbished" and _listing("store-b")["price"] == 199
    assert _listing("store-c")["price"] == 255 and DATA["card_offers"][0]["amount"] == 26
    assert _hist("store-c")[1][9] == 235  # Day 9 drop
    assert {s["store_id"]: s["trust"] for s in DATA["stores"]}["store-d"] == "scam" and _listing("store-d")["price"] == 189
    assert {s["store_id"]: s["status"] for s in DATA["stores"]}["store-e"] == "outage"
    g = _listing("store-g")
    assert g["fees"] > 0 and g["price"] + g["fees"] > 229


def test_store_names_never_imitate_real_brands_and_say_made_up():
    brands = {s["brand"] for s in DATA["stores"]}
    for real in ("Amazon", "Best Buy", "Walmart", "Target", "eBay", "Costco", "Newegg"):
        assert real not in brands


def test_live_postgres_counts_match_the_catalog():
    if not pg.configured():
        pytest.skip("Pricise Postgres not configured")
    try:
        conn = pg.connect()
    except pg.PgError as e:
        pytest.skip(str(e))
    with conn, conn.cursor() as cur:
        for table in ("stores", "products", "listings", "price_history", "card_offers"):
            cur.execute(f"SELECT count(*) FROM {table}")
            assert cur.fetchone()[0] == EXPECTED[table], table
