"""Example 3, Walmart's "(Open Box) JBL Flip 7" page: the extension read "current price $429.95, Was $563.70" from a carousel
card as this item's discount, the search ran for "Blue" (the title's first part was the condition tag), and nothing said the
item was open box. Fixture: backend/tests/fixtures/real_pages/walmart-openbox (made up, shaped like that page). Offline."""
import asyncio
import json
from pathlib import Path

import pytest

from backend import stub_server
from backend.agents import price
from backend.contracts import FinalCard, PageFacts, Verdict, BestPrice, Reason
from backend.tests.content_reader import detect_product, serve_repo
from backend.tests.test_usl import URL, stubs  # noqa: F401  (fixture)
from backend.tools import discount, shopping
from backend.tools.browser import cached_chromium

ROOT = Path(__file__).resolve().parents[2]
BASE = "/backend/tests/fixtures/real_pages/"
WALMART = "walmart-openbox/ip/JBL-Flip-7-Open-Box-Blue/21220572138/"
PREOWNED = "ebay-like-preowned/itm/acme-ac-500x-headphones/223456789012/"
TITLE = "(Open Box) JBL Flip 7 Portable Bluetooth Speaker, IP68 Waterproof and Dustproof, Blue, 14 Hours Playtime - Walmart.com"
needs_browser = pytest.mark.skipif(not cached_chromium(), reason="needs the hidden browser: run scripts/setup.sh")


@pytest.fixture(scope="module")
def site():
    server, url = serve_repo()
    yield url
    server.shutdown()


@needs_browser
def test_walmart_buy_box_price_and_was_price_not_the_carousel(site):
    _found, facts, _logs = asyncio.run(detect_product(site + BASE + WALMART))
    p = facts["product"]
    assert (p["price"], p["was_price"]) == (68.53, 77.0)
    claims = [s for s in facts["signals"] if s["type"] == "claimed_discount"]
    assert [c["amount"] for c in claims] == [77.0] and "$68.53" in claims[0]["text"]
    every = " ".join(s["text"] for s in facts["signals"] if s["type"] != "page_text")
    for other in ("429.95", "563.70", "149.95", "199.95", "72.10", "80.00", "$12/mo"):  # carousel, other seller, monthly offer
        assert other not in every, other


@needs_browser
def test_condition_from_the_title_and_from_a_condition_row(site):
    _found, facts, _logs = asyncio.run(detect_product(site + BASE + WALMART))
    [c] = [s for s in facts["signals"] if s["type"] == "item_condition"]
    assert c["detail"] == {"condition": "open box", "source": "title"}
    _found, facts, _logs = asyncio.run(detect_product(site + BASE + PREOWNED))
    [c] = [s for s in facts["signals"] if s["type"] == "item_condition"]
    assert c["detail"] == {"condition": "used", "source": "page"} and c["text"] == "Condition: Pre-owned"


@pytest.mark.parametrize("title,cond", [
    (TITLE, "open box"), ("Restored Apple iPhone 12, 64GB, Black", "refurbished"), ("Apple AirPods Pro (Renewed)", "refurbished"),
    ("Sony WH-1000XM5 Refurbished", "refurbished"), ("Sony WH-1000XM5 - Pre-Owned", "used"), ("Used - Kindle Paperwhite", "used"),
    ("JBL Flip 6 - Portable Waterproof Speaker - Blue", "new"), ("Most Used Headphones Ever", "new"),
])
def test_page_condition_from_the_title(title, cond):
    assert shopping.page_condition(PageFacts(url="https://x", product={"name": title})) == cond


def test_the_signal_wins_over_the_title():
    page = PageFacts(url="https://x", product={"name": "Acme AC-500X"},
                     signals=[{"type": "item_condition", "text": "Condition: Pre-owned", "detail": {"condition": "used"}}])
    assert shopping.page_condition(page) == "used"


def test_the_search_is_for_the_item_not_its_condition_tag():
    """The live run searched Google Shopping for "Blue" (perfume and vases came back)."""
    assert shopping.query_for(PageFacts(url="https://x", product={"name": TITLE}).product) == "JBL Flip 7 Portable Bluetooth Speaker Blue"
    assert shopping.query_for(PageFacts(url="https://x", product={"name": "JBL Flip 6 - Portable Waterproof Speaker - Blue"}).product) \
        == "JBL Flip 6 Blue"  # unchanged


def test_the_recorded_walmart_page_reads_as_open_box_even_without_the_signal():
    rec = json.loads((ROOT / "samples" / "walmart_jbl_flip7_openbox.json").read_text())
    assert rec["name"] == "walmart_jbl_flip7_openbox"
    assert shopping.page_condition(PageFacts(**rec["page"])) == "open box"


def _card(title="No tricks spotted", tone="good", reasons=()):
    return FinalCard(check_id="c", title=title, subtitle="0 tricks · 0 claims to check", tone=tone,
                     safe=Verdict(status="good", headline="Looks safe"), honest=Verdict(status="good", headline="Looks honest"),
                     best_price=BestPrice(status="unknown", headline="Compare", reasons=list(reasons)))


def test_the_verdict_says_open_box_once():
    page = PageFacts(url="https://www.walmart.com/ip/x/1", product={"name": TITLE, "price": 68.53})
    [f] = [f for f in stub_server.real_findings("c", page) if f.type == "item_condition"]
    assert (f.agent, f.status, f.summary) == ("page", "confirmed", "This listing is open box, not new.")
    shared = BestPrice(status="unknown", headline="Compare", reasons=[Reason(text="Credible stores: none")])
    card = _card()
    card.best_price = shared
    card = stub_server._with_condition(stub_server._with_condition(card, page), page)  # rebuilt cards: never twice
    assert [r.text for r in card.best_price.reasons] == ["This listing is open box, not new", "Credible stores: none"]
    assert card.subtitle.endswith("· open box, not new") and card.subtitle.count("not new") == 1
    assert (card.title, card.tone) == ("Check before you buy", "caution")
    assert [r.text for r in shared.reasons] == ["Credible stores: none"]  # the Price Agent's verdict is copied, not edited
    assert stub_server._with_condition(_card(), PageFacts(url="https://x", product={"name": "JBL Flip 6"})).title == "No tricks spotted"


def test_price_agent_compares_an_open_box_page_with_new_prices(stubs):  # noqa: F811
    page = PageFacts(url=URL, host="www.walmart.com", product={"name": "(Open Box) JBL Flip 6 Portable Speaker, Blue", "brand": "JBL",
                                                               "price": 70.0})
    res = price.assess("c1", URL, page)
    [f] = [f for f in res.findings if f.type == "lowest_price"]
    assert f.summary.startswith("This listing is open box, not new. New at credible stores: Woot $79.95")
    assert "as good as it gets" not in f.summary
    assert (res.best_price.headline, res.best_price.status) == ("New from $79.95", "caution")


def test_deal_agent_does_not_judge_an_open_box_was_price_against_msrp(stubs, monkeypatch):  # noqa: F811
    from backend.data import repo

    monkeypatch.setattr(repo, "own_observations", lambda *a, **k: [])
    page = PageFacts(url=URL, host="www.walmart.com",
                     product={"name": "(Open Box) JBL Flip 6 Portable Speaker, Blue", "brand": "JBL", "price": 89.0, "was_price": 129.95},
                     signals=[{"type": "claimed_discount", "selector": "#was", "text": "Now $89.00 $129.95", "amount": 129.95}])
    [f] = discount.check_discount("c", URL, page).findings
    assert f.type == "claimed_discount" and f.status == "unverified"
    assert "this listing is open box" in f.summary and "MSRP" in f.summary and "real against MSRP" not in f.summary
    # Walmart is this page's store: its NEW listing counts on an open-box page
    assert "New at credible stores now: Woot $79.95 lower, Best Buy $114.95 higher, Walmart $114.95 higher." in f.summary


# ----------------------------------------------------------------------------- follow-up fixes (re-recorded example 3)

def _flip7_page(name=TITLE, host="www.walmart.com", price=68.53):
    return PageFacts(url="https://www.walmart.com/ip/x/21220572138", host=host, product={"name": name, "price": price})


def test_model_name_ends_at_its_number_so_targets_flip_7_matches():
    p = _flip7_page().product
    assert shopping.model_name(p) == "jblflip7"
    assert shopping.same_item(p, {"title": "JBL Flip 7"}) == (True, "model name matches")  # Target's title, rejected live
    assert shopping.same_item(p, {"title": "JBL Flip 6 Portable Speaker Blue"}) == (False, "other model")


def test_jbl_flip_6_and_kindle_examples_match_exactly_the_same_offers_as_before():
    """Every same_item decision for the JBL Flip 6 and Kindle pages (test and recorded) over their saved Google Shopping
    results, against the decisions saved before the model-name change."""
    from backend.tests.test_online_prices import KINDLE
    from backend.tests.test_real_demo import NAME
    from backend.contracts import PageProduct

    fix = ROOT / "backend" / "tests" / "fixtures"
    before = json.loads((fix / "same_item" / "baseline_before_digit_fix.json").read_text())
    pages = {"jbl_test": PageProduct(name=NAME, brand="JBL", price=114.95),
             "jbl_sample": PageFacts(**json.loads((ROOT / "samples" / "jbl_flip6_amazon.json").read_text())["page"]).product,
             "kindle_sig": KINDLE,
             "kindle_sample": PageFacts(**json.loads((ROOT / "samples" / "kindle_amazon.json").read_text())["page"]).product}
    raws = {"jbl": json.loads((fix / "serpapi" / "jbl_flip_6_blue.json").read_text())["shopping_results"],
            "kindle": json.loads((fix / "serpapi" / "kindle_paperwhite_signature_32gb.json").read_text())["shopping_results"]}
    for name, prod in pages.items():
        raw = raws["jbl" if name.startswith("jbl") else "kindle"]
        now = [[o.get("source"), o.get("title"), *shopping.same_item(prod, {"title": o.get("title"),
                                                                            "condition": o.get("second_hand_condition")})] for o in raw]
        assert now == before[name], name
    assert {"Walmart", "Woot"} <= {s for s, _t, ok, _w in before["jbl_sample"] if ok}


def _offer(title, source, price):
    return shopping.parse([{"title": title, "source": source, "price": f"${price}", "extracted_price": price}])[0]


def test_the_same_stores_new_listing_counts_on_a_non_new_page_only():
    walmart_new = _offer("JBL Flip 7 Bluetooth Portable Waterproof Speaker", "Walmart", 129.95)
    walmart_red = _offer("JBL Flip 7 Bluetooth Portable Waterproof Speaker - Red", "Walmart", 129.95)
    walmart_ob = {**_offer("JBL Flip 7 Speaker - Blue (Open Box)", "Walmart", 66.0), "condition": "excluded: open box"}
    p = _flip7_page().product
    kept, _blocked, dropped = shopping.classify_offers(p, [walmart_new, walmart_red, walmart_ob], 68.53, "walmart.com", "open box")
    assert [(o["source"], o["total"], o.get("same_store")) for o in kept] == [("Walmart", 129.95, True)]
    assert {d["reason"] for d in dropped} == {"this page's own store", "open box"}  # another colour; another open box
    kept, _blocked, dropped = shopping.classify_offers(p, [walmart_new], 68.53, "walmart.com")  # a new page: as before
    assert not kept and dropped[0]["reason"] == "this page's own store"


def test_price_agent_shows_the_same_stores_new_listing_labelled(stubs):  # noqa: F811
    page = PageFacts(url=URL, host="www.walmart.com", product={"name": "(Open Box) JBL Flip 6 Portable Speaker, Blue", "brand": "JBL",
                                                               "price": 70.0})
    res = price.assess("c1", URL, page)
    walmart = next(s for s in res.online["shown"] if s["store"] == "Walmart")
    assert (walmart["total"], walmart["condition"]) == (114.95, "new, same store")
    [f] = [f for f in res.findings if f.type == "lowest_price"]
    assert "Walmart $114.95 (new, same store)" in f.summary


def test_no_model_number_line_only_without_a_search_or_a_model(stubs, monkeypatch):  # noqa: F811
    from backend.data import zetaris

    def lines(page):
        return [f for f in price.assess("c1", URL, page).findings if f.type == "catalog_only"]

    flip7 = _flip7_page(host="www.walmart.com")
    assert lines(flip7) == []  # the search ran and the title names the model ("JBL Flip 7"): nothing to say
    nameless = _flip7_page(name="Acme Wireless Headphones, Black")
    assert lines(nameless)[0].summary.startswith("This page shows no model number")  # searched, but no model to match
    monkeypatch.setenv("PRICISE_USL", "off")
    monkeypatch.setenv("SERPAPI_KEY", "")
    monkeypatch.setattr(zetaris, "configured", lambda: False)
    assert lines(flip7)[0].summary.startswith("This page shows no model number")  # no search ran


@needs_browser
def test_hidden_screen_reader_text_reads_with_spaces(site):
    _found, facts, _logs = asyncio.run(detect_product(site + BASE + WALMART))
    [claim] = [s for s in facts["signals"] if s["type"] == "claimed_discount"]
    assert claim["text"] == "Now $68.53 · You save $8.47 · was $77.00"
