"""Price observations: what a real store page leaves behind (nothing personal, never Store Lab, never an unsafe store),
dedupe within the hour, the Price Agent's cross-site match ("You saw this for $X at host"), and the chat's "cheaper?"
answers with and without observations. The live Postgres test skips itself when the database isn't running."""
import time

import pytest

from backend import stub_server
from backend.agents import chat, price
from backend.contracts import PageFacts
from backend.data import pg, repo

DAY = 86400


def _chk(url, price_=229.0, name="Sony WH-1000XM5 Wireless Headphones", model="WH-1000XM5", safe="unknown", host=None):
    host = host or url.split("/")[2]
    return {"check_id": "c1", "url": url, "final_card": {"safe": {"status": safe}},
            "page": {"url": url, "host": host, "product": {"name": name, "price": price_, "brand": "Sony", "model": model,
                                                          "sku": "6505727", "currency": "USD"}, "signals": []}}


def test_observation_has_product_fields_only_and_a_clean_url():
    obs = stub_server.observation(_chk("https://www.bestbuy.com/site/sony-xm5/6505727.p?skuId=6505727&session=abc#reviews"))
    assert obs["url"] == "https://www.bestbuy.com/site/sony-xm5/6505727.p"  # no query string, no fragment
    assert (obs["host"], obs["model_key"], obs["price"], obs["condition"], obs["safe"]) == \
        ("bestbuy.com", "wh1000xm5", 229.0, "new", "unknown")
    assert set(obs) == set(repo.OBS_COLS)


@pytest.mark.parametrize("chk", [
    _chk("http://localhost:8787/storelab/audiodealz/"),           # Store Lab: synthetic, never an observation
    _chk("http://localhost:8787/bench/deal/fd-01.html"),
    _chk("https://shop.example/x", price_=None),                 # no price: nothing to compare
    _chk("https://shop.example/x", safe="bad"),                  # a store found unsafe is never a price to beat
])
def test_no_observation_for_store_lab_unpriced_or_unsafe_pages(chk):
    assert stub_server.observation(chk) is None


def test_condition_from_the_name():
    assert [repo.condition_of(n) for n in ("Sony XM5 (Renewed)", "Open-box Sony XM5", "Sony XM5")] == ["refurbished", "used", "new"]


def test_save_and_dedupe_within_the_hour():
    obs = stub_server.observation(_chk("https://www.bestbuy.com/site/x/1.p"))
    assert repo.save_observation(obs) is True
    assert repo.save_observation({**obs, "seen_at": obs["seen_at"] + 600}) is False      # same url + price, 10 min later
    assert repo.save_observation({**obs, "price": 219.0, "seen_at": obs["seen_at"] + 600}) is True  # new price
    assert repo.save_observation({**obs, "seen_at": obs["seen_at"] + 2 * 3600}) is True   # same price, 2 hours later
    assert len(repo.OBS_MEMORY) == 3


def _seen(host, price_, days_ago=1, model="WH-1000XM5", name="Sony WH-1000XM5"):
    obs = stub_server.observation(_chk(f"https://{host}/p/1", price_=price_, model=model, name=name))
    obs["seen_at"] = time.time() - days_ago * DAY - 60
    assert repo.save_observation(obs)


def _real(url="https://www.walmart.com/ip/sony-xm5/123456", price_=249.0, model="WH-1000XM5", name="Sony WH-1000XM5"):
    page = PageFacts(url=url, host=url.split("/")[2], product={"name": name, "price": price_, "model": model})
    return price.assess("c1", url, page)


def test_price_agent_reports_the_cheapest_price_seen_on_another_store():
    _seen("bestbuy.com", 229.0, days_ago=1)
    _seen("target.com", 239.0, days_ago=3)
    _seen("walmart.com", 199.0, days_ago=1)                      # the same host as the page: never compared with itself
    _seen("ebay.com", 150.0, name="Sony WH-1000XM5 (Renewed)")    # refurbished: not the same item
    _seen("example-shop.com", 120.0, model="WH-1000XM4")          # another model
    res = _real()
    [f] = [f for f in res.findings if f.type == "seen_elsewhere"]
    assert f.status == "confirmed" and f.agent == "price"
    assert f.summary == ("You saw this for $229 at bestbuy.com yesterday, $20 less than here. "
                         "That price may have changed since.")
    assert res.best_price.headline == "Seen for $229 at bestbuy.com" and res.best_price.status == "caution"
    assert res.alt is None  # not a verified safer store: no "Safer:" line on the card
    assert [s["store"] for s in res.stores] == ["bestbuy.com", "target.com"]


def test_this_page_cheaper_than_anything_seen():
    _seen("bestbuy.com", 229.0)
    res = _real(price_=199.0)
    assert "this page is cheaper" in res.findings[0].summary and res.best_price.headline == "Cheapest you've seen"


def test_no_observations_says_so_and_points_to_compare():
    res = _real()
    assert [f.type for f in res.findings] == ["catalog_only"] and res.findings[0].status == "unverified"
    assert "No other store prices seen yet" in res.findings[0].summary and "Compare prices" in res.findings[0].summary


def test_zetaris_sql_is_the_evidence(monkeypatch):
    monkeypatch.setenv("DATA_SOURCE", "zetaris")
    rows = [{"host": "bestbuy.com", "brand": "Sony", "model": "WH-1000XM5", "name": "x", "price": "229.000000",
             "currency": "USD", "condition": "new", "safe": "unknown", "url": "https://www.bestbuy.com/site/x/1.p",
             "seen_at": str(time.time() - DAY - 60)}]
    seen = []
    monkeypatch.setattr(repo.zetaris, "run_sql", lambda sql, limit=1000: seen.append(sql) or rows)
    res = _real()
    [f] = [f for f in res.findings if f.type == "seen_elsewhere"]
    [ev] = [e for e in f.evidence if e.kind == "sql"]
    assert ev.ref == seen[0] and "pricise_pg.price_observations" in ev.ref and "host <> 'walmart.com'" in ev.ref


# ----------------------------------------------------------------------------- chat: "where can I get this cheaper?"

def _check_for_chat(findings, product=None):
    return {"findings": findings, "page": {"product": product or {"brand": "Sony", "model": "WH-1000XM5"}}, "final_card": {}}


def test_chat_cheaper_with_an_observation():
    seen = {"agent": "price", "type": "seen_elsewhere", "status": "confirmed",
            "summary": "You saw this for $229 at bestbuy.com yesterday, $20 less than here. That price may have changed since."}
    res = chat.answer(_check_for_chat([seen]), "Where can I get this cheaper?")
    assert res.answer.startswith("You saw this for $229 at bestbuy.com yesterday")
    assert "seen_elsewhere" in res.sources and res.links[0]["label"] == "Google Shopping"
    assert chat.CANT not in res.answer


def test_chat_cheaper_without_anything_offers_compare_links(monkeypatch):
    monkeypatch.setattr(chat.meterless, "complete", lambda *a, **k: pytest.fail("no model call for price facts"))
    res = chat.answer(_check_for_chat([]), "Is there a cheaper option?")
    assert res.answer == ("I haven't seen this item for less at another store yet. "
                          "Compare prices on other stores with the links below.")
    assert [l["label"] for l in res.links] == ["Google Shopping", "Best Buy", "Walmart", "eBay"]
    assert "q=Sony%20WH-1000XM5" in res.links[0]["url"]


def test_chat_cheaper_uses_the_store_lab_best_price():
    best = {"agent": "price", "type": "best_price", "status": "confirmed",
            "summary": "Store C, $229: exact match (new, same model), $255 minus a $26 card offer. Skeptic: Same item."}
    res = chat.answer(_check_for_chat([best]), "where else can I buy this for less, cheaper?")
    assert res.answer.startswith("Store C, $229: exact match") and "Skeptic" not in res.answer


# ----------------------------------------------------------------------------- live Pricise Postgres


def test_live_postgres_save_dedupe_and_match(monkeypatch):
    if not pg.configured():
        pytest.skip("Pricise Postgres not configured")
    try:
        with pg.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM price_observations LIMIT 1")
    except Exception as e:
        pytest.skip(f"Pricise Postgres or its price_observations table not available: {type(e).__name__}")
    monkeypatch.setenv("DATA_SOURCE", "zetaris")
    host = "pytest-observation.example"
    obs = {**stub_server.observation(_chk(f"https://{host}/p/1", model="PYTEST-MODEL-1")), "host": host}
    try:
        assert repo.save_observation(obs) is True and repo.save_observation(obs) is False
        rows, _sql, source = repo.other_observations("pytestmodel1", "new", "other.example")
        assert source in ("zetaris", "postgres") and [r["host"] for r in rows] == [host] and rows[0]["price"] == 229.0
    finally:
        with pg.connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM price_observations WHERE host = %s", [host])
