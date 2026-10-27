"""Online prices for real pages (Google Shopping through SerpApi, from a saved response: no live search in tests), the
same-item rules, Trust on every seller, the 6 h cache, chat, Amazon list prices, and price history from the page itself."""
import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from backend.agents import chat, price
from backend.contracts import PageFacts, PageProduct
from backend.data import repo
from backend.tools import shopping
from backend.tools.discount import check_discount

FIX = Path(__file__).resolve().parent / "fixtures"
OFFERS = shopping.parse(json.loads((FIX / "serpapi" / "energizer_aaa_32.json").read_text())["shopping_results"])
URL = "https://www.amazon.com/Energizer-AAA-Batteries/dp/B09RTVN5GN"
PRODUCT = PageProduct(name="Energizer AAA Batteries, Max Triple A Battery Alkaline, 32 Count", brand="Energizer", price=26.99)


@pytest.fixture(autouse=True)
def _clean():
    repo.OFFERS_MEMORY.clear()
    yield
    repo.OFFERS_MEMORY.clear()


# ----------------------------------------------------------------------------- parsing and same-item rules


def test_parse_offers_from_a_real_response():
    by = {o["source"]: o for o in OFFERS}
    assert by["Walmart"]["domain"] == "walmart.com" and by["Walmart"]["shipping"] == 0.0 and by["Walmart"]["price"] == 113.38
    assert by["mcgrocer.com"]["domain"] == "mcgrocer.com"                 # a store named by its domain
    assert by["eBay - ielectrica"]["domain"] == "ebay.com"                 # a marketplace seller: the marketplace's site
    assert by["ReStockIt"]["domain"] is None                               # unknown store name: no website, never vetted
    assert all(o["link"].startswith("https://www.google.com/shopping/") for o in OFFERS)


@pytest.mark.parametrize("text, cost", [("Free delivery", 0.0), ("$5.99 delivery", 5.99), (None, None), ("Delivery by Sat", None)])
def test_shipping(text, cost):
    assert shopping.shipping_of(text)[0] == cost


def test_same_item_is_strict():
    same = {o["title"] for o in OFFERS if shopping.same_item(PRODUCT, o)[0]}
    assert "Energizer Max Alkaline AAA Batteries - Pack of 32" in same
    assert "Energizer Max Alkaline Aaa Batteries - Pack Of 32" in same
    for other in ("Energizer MAX AAA Batteries",                                   # no pack size: can't tell
                  "Energizer Max Aaa Alkaline Batteries 8/24/32/48/60/64/96 Count",  # many sizes in one listing
                  "Energizer Ultimate AAA Lithium Batteries",
                  "Energizer AA and AAA Batteries, 32 Count, Combo Pack Contains 16 Max Double A and 16 Max Triple A Batteries"):
        assert other not in same
    assert not shopping.same_item(PRODUCT, {"title": "Energizer Max AAA Batteries Pack of 32 (Renewed)"})[0]
    assert not shopping.same_item(PRODUCT, {"title": "Duracell AAA Batteries Pack of 32"})[0]


def test_query_uses_brand_and_model_else_a_clean_title():
    assert shopping.query_for(PageProduct(brand="Sony", model="WH-1000XM5")) == "Sony WH-1000XM5"
    assert shopping.query_for(PageProduct(name="Running Phone Holder Vest with Bottle | Reflective | 6 Pockets",
                                          brand="SWIFTVEST")) == "SWIFTVEST Running Phone Holder Vest with Bottle"


# ----------------------------------------------------------------------------- the Price Agent on a real page


@pytest.fixture
def online(monkeypatch):
    """SerpApi on with the saved response; Trust answers per domain (default safe). Counts searches and vets."""
    calls = {"search": 0, "vetted": []}
    verdicts = {}

    def fake_search(query):
        calls["search"] += 1
        return list(OFFERS)

    def fake_vet(check_id, domains):
        calls["vetted"].append(list(domains))
        return {d: {"verdict": verdicts.get(d, "safe"), "checked": verdicts.get(d) != "unchecked", "reasons": []} for d in domains}

    monkeypatch.setenv("SERPAPI_KEY", "test-key-not-real")
    monkeypatch.setattr(shopping, "_search_serpapi", fake_search)
    monkeypatch.setattr(price, "_vet_domains", fake_vet)
    return calls, verdicts


def _real(price_=26.99):
    page = PageFacts(url=URL, host="www.amazon.com", product={**PRODUCT.model_dump(), "price": price_})
    return price.assess("c1", URL, page)


def test_cheapest_trusted_offer_below_this_page(online):
    calls, _ = online
    res = _real(26.99)
    [best] = [f for f in res.findings if f.type == "best_price"]
    assert best.status == "confirmed" and best.summary == "Same item for $22.99 at ebay.com, trusted store (free shipping)."
    assert res.best_price.store == "ebay.com" and res.best_price.total == 22.99 and res.alt["store"] == "ebay.com"
    assert res.alt["url"].startswith("https://www.google.com/shopping/")
    assert calls["search"] == 1 and "amazon.com" not in calls["vetted"][0]  # this page's own store is never an offer


def test_untrusted_seller_never_wins(online):
    _, verdicts = online
    verdicts["ebay.com"] = "unsafe"
    res = _real(26.99)
    assert not [f for f in res.findings if f.type == "best_price"]  # mcgrocer.com is the next same item at $45.89
    [low] = [f for f in res.findings if f.type == "lowest_price"]
    assert low.summary == "This is the lowest trusted price we found. Also checked: mcgrocer.com $45.89."
    assert any(r["store"] == "ebay.com" and r["status"] == "rejected" for r in res.stores)


def test_this_page_is_the_lowest_trusted_price(online):
    res = _real(19.99)
    [low] = [f for f in res.findings if f.type == "lowest_price"]
    assert low.summary.startswith("This is the lowest trusted price we found. Also checked: ebay.com $22.99")
    assert res.best_price.headline == "Lowest trusted price" and res.alt is None


def test_cache_for_six_hours(online):
    calls, _ = online
    _real()
    _real()
    assert calls["search"] == 1  # the second check reads the cached offers


def test_no_key_or_failure_skips_cleanly(monkeypatch):
    monkeypatch.setattr(shopping, "configured", lambda: False)
    monkeypatch.setattr(shopping, "_search_serpapi", lambda q: pytest.fail("no search without a key"))
    assert not [f for f in _real().findings if f.type == "online_offers"]  # no key: skipped cleanly, no extra row
    monkeypatch.setattr(shopping, "configured", lambda: True)
    monkeypatch.setattr(shopping, "_search_serpapi", lambda q: None)
    [f] = [f for f in _real().findings if f.type == "online_offers"]
    assert f.summary.startswith("Online price search didn't answer")


def test_chat_cheaper_uses_the_online_offer(online):
    res = _real(26.99)
    check = {"findings": [f.model_dump() for f in res.findings], "page": {"product": PRODUCT.model_dump()}, "final_card": {}}
    answer = chat.answer(check, "Where can I get this cheaper?")
    assert answer.answer.startswith("Same item for $22.99 at ebay.com, trusted store") and answer.links


# ----------------------------------------------------------------------------- pages: Amazon list prices, price history

from backend.tests.content_reader import detect_product, serve_repo  # noqa: E402
from backend.tools.browser import cached_chromium  # noqa: E402

needs_browser = pytest.mark.skipif(not cached_chromium(), reason="needs the hidden browser: run scripts/setup.sh")
BASE = "/backend/tests/fixtures/real_pages/"


@pytest.fixture(scope="module")
def site():
    server, url = serve_repo()
    yield url
    server.shutdown()


@needs_browser
def test_list_price_below_todays_price_is_no_discount(site):
    _found, facts, _ = asyncio.run(detect_product(site + BASE + "amazon-like-listprice/dp/B0TESTLIST/"))
    assert facts["product"]["price"] == 37.99 and facts["product"].get("was_price") is None
    assert not [s for s in facts["signals"] if s["type"] == "claimed_discount"]  # $36.99 list, sponsored $34.99/$49.99


@needs_browser
def test_buy_box_claim_text_is_clean(site):
    _found, facts, _ = asyncio.run(detect_product(site + BASE + "amazon-like-buybox/dp/B0TESTAAA1/"))
    [claim] = [s for s in facts["signals"] if s["type"] == "claimed_discount"]
    assert claim["text"].count("$19.99") == 1 and claim["text"].count("$15.99") <= 1  # no screen-reader duplicates


@needs_browser
def test_price_history_shown_on_the_page_is_read_and_used(site):
    _found, facts, _ = asyncio.run(detect_product(site + BASE + "history-widget/products/acme-kettle/"))
    [hist] = [s for s in facts["signals"] if s["type"] == "price_history"]
    assert len(hist["detail"]["points"]) == 6
    url = "https://shop.example/products/acme-kettle"
    page = PageFacts(url=url, host="shop.example", product=facts["product"], signals=facts["signals"])
    res = check_discount("c1", url, page)
    [f] = res.findings
    assert (f.type, f.status) == ("fake_discount", "confirmed")
    assert f.summary.startswith("This was-price isn't supported by the price history we found")  # careful real-store wording
    assert res.evidence_data["price_history"]["label"] == "Price history shown on the shop.example page"


def test_short_history_stays_unverified():
    today = date.today()
    sig = {"type": "price_history", "text": "", "detail": {"points": [[(today - timedelta(days=d)).isoformat(), 59.99]
                                                                    for d in (10, 8, 6, 4, 2)]}}
    url = "https://shop.example/p"
    page = PageFacts(url=url, host="shop.example", product={"price": 59.99},
                     signals=[sig, {"type": "claimed_discount", "text": "Was $89.99", "amount": 89.99}])
    [f] = check_discount("c1", url, page).findings
    assert f.status == "unverified" and "Only 11 days of the page's own price history" in f.summary
