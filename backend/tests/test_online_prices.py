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


# ----------------------------------------------------------------------------- the Price Agent on a real page (Kindle)

KINDLE_RAW = json.loads((FIX / "serpapi" / "kindle_paperwhite_signature_32gb.json").read_text())["shopping_results"]
KINDLE_URL = "https://www.amazon.com/dp/B0C8RR4WN3"
KINDLE = PageProduct(name="Amazon Kindle Paperwhite Signature Edition 32GB (2024 model) – Auto-adjusting front light, "
                          "wireless charging, and weeks of battery life – Metallic Black", brand="Amazon Kindle", price=249.99)
# Synthetic offers added to the real response for the cases it lacks (clearly not from SerpApi).
MONTHLY = {"title": "Amazon Kindle Paperwhite Signature Edition 32GB Metallic Black", "source": "Best Buy",
           "price": "$36.00/mo", "extracted_price": 36.0, "product_link": "https://example.test/monthly"}
UNKNOWN = {"title": "Amazon Kindle Paperwhite Signature Edition 32GB Metallic Black", "source": "Gadget Galaxy Outlet",
           "price": "$199.00", "extracted_price": 199.0, "product_link": "https://example.test/unknown"}
MARKET = {"title": "Amazon Kindle Paperwhite Signature Edition 32GB", "source": "Walmart - QuickShip Deals",
          "price": "$209.00", "extracted_price": 209.0, "product_link": "https://example.test/market"}
REGULAR_16 = {"title": "Amazon Kindle Paperwhite 16GB (2024) Black", "source": "Target", "price": "$159.99",
              "extracted_price": 159.99, "product_link": "https://example.test/regular"}
TARGET = {"title": "Amazon Kindle Paperwhite Signature Edition 32GB - Metallic Black", "source": "Target", "price": "$239.99",
          "extracted_price": 239.99, "delivery": "Free delivery", "product_link": "https://example.test/target"}


@pytest.fixture
def online(monkeypatch):
    """SerpApi on with a saved response (set calls["raw"]); Trust answers per domain (default safe)."""
    calls = {"search": 0, "vetted": [], "raw": list(KINDLE_RAW)}
    verdicts = {}

    def fake_search(query):
        calls["search"] += 1
        return shopping.parse(calls["raw"])

    def fake_vet(check_id, domains):
        calls["vetted"].append(list(domains))
        return {d: {"verdict": verdicts.get(d, "safe"), "checked": True, "reasons": []} for d in domains}

    monkeypatch.setenv("SERPAPI_KEY", "test-key-not-real")
    monkeypatch.setattr(shopping, "_search_serpapi", fake_search)
    monkeypatch.setattr(price, "_vet_domains", fake_vet)
    return calls, verdicts


def _kindle(price_=249.99):
    page = PageFacts(url=KINDLE_URL, host="www.amazon.com", product={**KINDLE.model_dump(), "price": price_})
    return price.assess("c1", KINDLE_URL, page)


def test_monthly_price_is_rejected_and_unknown_and_marketplace_stores_never_show(online):
    calls, _ = online
    calls["raw"] = [MONTHLY, UNKNOWN, MARKET, *KINDLE_RAW]
    res = _kindle()
    shown = " ".join(f.summary for f in res.findings) + json.dumps(res.stores) + json.dumps(res.best_price.model_dump())
    for never in ("Gadget Galaxy", "QuickShip", "$36.99", "eBay", "Poshmark", "mcgrocer"):
        assert never not in shown
    # a monthly price at a credible store is shown only as the Skeptic's rejection, with its reason; never as a price
    [monthly] = [f for f in res.findings if f.type == "best_price"]
    assert (monthly.status, monthly.challenged_by) == ("rejected", "skeptic")
    assert monthly.summary == "Skeptic rejected Best Buy $36: a monthly or installment price, not the full price."
    assert res.alt == {"kind": "this_page"} and res.best_price.headline == "As good as it gets"
    reasons = {(d["source"], d["price"]): d["reason"] for d in res.dropped}
    assert reasons[("Best Buy", 36.0)] == '"/mo" in its price'
    assert reasons[("Best Buy", 36.99)].startswith("under 50% of this page's $249.99")  # the real $36.99 one
    assert reasons[("Gadget Galaxy Outlet", 199.0)] == "not an allowlisted store"
    assert reasons[("Walmart - QuickShip Deals", 209.0)] == "marketplace seller, not the retailer"


def test_same_price_at_a_trusted_store_says_so(online):
    res = _kindle(249.99)
    [f] = [f for f in res.findings if f.type == "lowest_price"]
    assert f.summary == ("Same price at Best Buy, this page is as good as it gets. "
                         "Also checked: Walmart (not found); Target (not found).")  # fewer than 3 stores: say who else
    assert res.best_price.headline == "As good as it gets" and res.alt == {"kind": "this_page"}  # "Best price: this page"
    assert [s["store"] for s in res.stores] == ["Best Buy"]                      # one per store, never an unknown one
    # the offer's own Google Shopping product page from the search result, never a built store search URL
    assert [e.ref for e in f.evidence if e.kind == "url"][0] == "https://www.google.com/shopping/product/kindle-1"


def test_signature_32gb_never_matches_the_regular_16gb():
    assert not shopping.same_item(KINDLE, REGULAR_16)[0]
    assert shopping.same_item(KINDLE, TARGET)[0]
    colorsoft = next(r for r in KINDLE_RAW if "Colorsoft" in r["title"] and r["source"] == "Best Buy")
    assert shopping.same_item(KINDLE, colorsoft) == (False, "other edition (colorsoft)")


def test_one_to_three_offers_and_cheaper_only_by_a_dollar(online):
    calls, _ = online
    calls["raw"] = [TARGET, REGULAR_16, *KINDLE_RAW]
    res = _kindle(249.99)
    [best] = [f for f in res.findings if f.type == "best_price"]
    assert best.summary == ("Skeptic approved Target $239.99, $10 less than here: same item, new, full price, sold by "
                            "Target; store checks passed.")
    assert (best.status, best.challenged_by) == ("confirmed", "skeptic")
    assert [s["store"] for s in res.stores] == ["Target", "Best Buy"] and res.alt["store"] == "Target"
    res = _kindle(240.50)  # Target only $0.51 less: not "cheaper"
    assert not [f for f in res.findings if f.type == "best_price"]
    [low] = [f for f in res.findings if f.type == "lowest_price"]
    assert low.summary.startswith("Same price at Target, this page is as good as it gets.")
    res = _kindle(229.99)  # every credible store is dearer
    [low] = [f for f in res.findings if f.type == "lowest_price"]
    assert low.summary.startswith("This is the lowest price among trusted stores. Also at: Target $239.99, Best Buy $249.99.")


def test_a_store_that_fails_trust_is_left_out(online):
    calls, verdicts = online
    calls["raw"] = [TARGET, *KINDLE_RAW]
    verdicts["target.com"] = "unsafe"
    res = _kindle(249.99)
    assert "Target" not in json.dumps(res.stores) and ("Target", 239.99) in {(d["source"], d["price"]) for d in res.dropped}


def test_cache_for_six_hours(online):
    calls, _ = online
    _kindle()
    _kindle()
    assert calls["search"] == 1  # the second check reads the cached offers


def test_no_key_or_failure_skips_cleanly(monkeypatch):
    monkeypatch.setattr(shopping, "configured", lambda: False)
    monkeypatch.setattr(shopping, "_search_serpapi", lambda q: pytest.fail("no search without a key"))
    assert not [f for f in _kindle().findings if f.type == "online_offers"]  # no key: skipped cleanly, no extra row
    monkeypatch.setattr(shopping, "configured", lambda: True)
    monkeypatch.setattr(shopping, "_search_serpapi", lambda q: None)
    [f] = [f for f in _kindle().findings if f.type == "online_offers"]
    assert f.summary.startswith("Online price search didn't answer")


def test_chat_cheaper_uses_the_same_list(online):
    calls, _ = online
    calls["raw"] = [TARGET, UNKNOWN, *KINDLE_RAW]
    res = _kindle(249.99)
    check = {"findings": [f.model_dump() for f in res.findings], "page": {"product": KINDLE.model_dump()}, "final_card": {}}
    answer = chat.answer(check, "Where can I get this cheaper?")
    assert answer.answer.startswith("Skeptic approved Target $239.99") and "Gadget" not in answer.answer


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
