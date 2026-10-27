"""General rules (no store or URL is special-cased):
1. Store links are the offer's own listing, else its Google Shopping product page, else a Google Shopping search for the
   exact product; never a store search URL (woot.com/search?... is a 404).
2. A Skeptic-approved same item, new, at least 10% or $10 under this page's real total makes the card amber:
   "Same item $X cheaper at <store>". Tricks still count separately.
3. A countdown that keeps counting down across fresh loads is informational, not a claim to check; a leading "(NEW)" tag
   doesn't hide the model, so a "was" price equal to the MSRP reads as real vs the launch price. The real Woot JBL Flip 6
   listing is then green.
Offline: SerpApi, Trust and the models are stubbed; the pages are the PageFacts the extension read in Chrome."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import stub_server
from backend.agents import price
from backend.contracts import BestPrice, FinalCard, PageFacts, Reason, Verdict
from backend.data import msrp
from backend.data.trusted_retailers import offer_url
from backend.tools import shopping, timer_test

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "backend" / "tests" / "fixtures"
RAW = json.loads((FIX / "serpapi" / "jbl_flip_6_blue.json").read_text())["shopping_results"]
SELLERS = json.loads((FIX / "serpapi" / "jbl_flip_6_sellers.json").read_text())["product_results"]["stores"]
AMAZON = json.loads((ROOT / "samples" / "jbl_flip6_amazon.json").read_text())
WOOT = json.loads((FIX / "page_facts" / "woot_jbl_flip6.json").read_text())
STEADY = {"type": "countdown", "selector": "#timer", "text": "Deal ends in 4d 23:59:10",
          "detail": {"remaining_s": 431950, "previous_s": 432250, "seconds_since_last_visit": 300, "steady": True}}


# ----------------------------------------------------------------------------- 1. links

def test_offer_links_never_build_a_store_search_url():
    q = "JBL Flip 6 Blue"
    own = "https://www.bestbuy.com/product/jbl-flip6-portable-waterproof-speaker-blue/J7LXF"
    gshop = "https://www.google.com/search?ibp=oshop&q=JBL+Flip+6+Blue&prds=productid:1,pvt:hg&udm=28"
    assert offer_url({"link": own}, q) == own  # the store's own listing
    assert offer_url({"link": gshop}, q) == gshop  # the Google Shopping product page
    assert offer_url({"link": "https://www.google.com/url?q=x"}, q) == "https://www.google.com/search?tbm=shop&q=JBL%20Flip%206%20Blue"
    for o in ({"link": None}, {}, {"link": "http://insecure.example/x"}):
        assert offer_url({**o, "domain": "woot.com"}, q) == "https://www.google.com/search?tbm=shop&q=JBL%20Flip%206%20Blue"


def test_search_results_keep_the_stores_own_link_first():
    [o] = shopping.parse([{"title": "JBL Flip 6", "source": "Walmart", "price": "$114.95", "extracted_price": 114.95,
                           "link": "https://www.walmart.com/ip/123", "product_link": "https://www.google.com/search?ibp=oshop&q=x"}])
    assert o["link"] == "https://www.walmart.com/ip/123"


def _stub_offers(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "test-key-not-real")
    monkeypatch.setattr(shopping, "_search_serpapi", lambda q: shopping.parse(RAW))
    monkeypatch.setattr(shopping, "_sellers_serpapi", lambda t: shopping.parse_sellers(SELLERS))
    monkeypatch.setattr(price, "_vet_domains", lambda cid, ds: {d: {"verdict": "safe", "checked": True, "reasons": []} for d in ds})


def test_every_shown_and_go_link_is_a_listing_or_google_shopping(monkeypatch):
    _stub_offers(monkeypatch)
    res = price.assess("c1", AMAZON["url"], PageFacts(**AMAZON["page"]))
    urls = [s["url"] for s in res.online["shown"]] + [res.alt["url"]]
    assert urls and all(u.startswith(("https://www.google.com/search?ibp=oshop", "https://www.google.com/search?tbm=shop"))
                        or "/search" not in u.split("?")[0] for u in urls), urls
    assert not any("woot.com/search" in u or "/s?k=" in u or "searchpage.jsp" in u for u in urls)


# ----------------------------------------------------------------------------- 2. cheaper same item

def _card(tone="good", title="No tricks spotted", alt=None, reasons=()):
    return FinalCard(check_id="c", title=title, subtitle="0 tricks · 0 claims to check", tone=tone,
                     safe=Verdict(status="good", headline="Looks safe"), honest=Verdict(status="good", headline="Looks honest"),
                     best_price=BestPrice(status="good", headline="Woot · $79.95", reasons=list(reasons)), tricks=0, alt=alt)


def _page(price_=114.95, fees=()):
    return PageFacts(url="https://shop.example/p", product={"name": "JBL Flip 6", "price": price_},
                     signals=[{"type": "drip_fees", "text": "Service fee", "amount": a} for a in fees])


CHEAPER = {"kind": "cheaper", "store": "Woot", "total": 79.95, "url": "https://x", "save": 35.0}


def test_a_cheaper_same_item_turns_a_green_card_amber():
    card = stub_server._with_cheaper(stub_server._with_cheaper(_card(alt=CHEAPER), _page()), _page())  # rebuilt: once
    assert (card.title, card.tone, card.tricks) == ("Check before you buy", "caution", 0)
    assert card.best_price.reasons[0].text == "Same item $35 cheaper at Woot"
    assert card.subtitle == "0 tricks · 0 claims to check · same item $35 cheaper at Woot"


@pytest.mark.parametrize("page_total,offer,amber", [
    (100.0, 90.0, True),    # $10 and 10%
    (100.0, 90.01, False),  # $9.99 and 9.99%: neither
    (200.0, 189.0, True),   # $11 (5.5%): $10 is enough
    (50.0, 45.0, True),     # $5 (10%): 10% is enough
    (50.0, 45.5, False),    # $4.50 (9%)
])
def test_ten_percent_or_ten_dollars(page_total, offer, amber):
    card = stub_server._with_cheaper(_card(alt={**CHEAPER, "total": offer}), _page(price_=page_total))
    assert (card.tone == "caution") is amber


def test_the_real_total_counts_fees_and_red_cards_stay_red():
    card = stub_server._with_cheaper(_card(alt={**CHEAPER, "total": 95.0}), _page(price_=90.0, fees=(15.0,)))  # $105 real
    assert card.best_price.reasons[0].text == "Same item $10 cheaper at Woot"
    red = stub_server._with_cheaper(_card(tone="bad", title="Don't buy here", alt=CHEAPER), _page())
    assert (red.title, red.tone) == ("Don't buy here", "bad") and red.best_price.reasons[0].text.startswith("Same item $35")
    assert stub_server._with_cheaper(_card(alt={"kind": "this_page"}), _page()).tone == "good"
    assert stub_server._with_cheaper(_card(alt=None), _page()).tone == "good"


# ----------------------------------------------------------------------------- 3. countdowns, "(NEW)", the Woot page

def test_a_countdown_that_held_is_informational_and_a_first_visit_one_is_a_claim():
    held = timer_test.passthrough("c", PageFacts(url="https://x", signals=[STEADY]).signals[0])
    [f] = held.findings
    assert (f.type, f.status) == ("countdown", "confirmed") and "kept counting down between your visits (5 min apart)" in f.summary
    first = {**STEADY, "detail": {"remaining_s": 431950}}
    [g] = timer_test.passthrough("c", PageFacts(url="https://x", signals=[first]).signals[0]).findings
    assert g.status == "proposed"
    page = PageFacts(url="https://x", product={"name": "JBL Flip 6", "price": 79.95}, signals=[STEADY])
    card = stub_server.real_card("c", page, stub_server.real_findings("c", page), 1.0)
    assert (card.title, card.tone) == ("No tricks spotted", "good")
    page = PageFacts(url="https://x", product={"name": "JBL Flip 6", "price": 79.95}, signals=[first])
    assert stub_server.real_card("c", page, stub_server.real_findings("c", page), 1.0).title == "Check before you buy"


def test_a_leading_new_tag_does_not_hide_the_model_or_its_msrp():
    name = WOOT["page"]["product"]["name"]  # "(NEW) JBL Flip 6 Portable IP67 Waterproof Speaker - $79.95 - Free shipping..."
    p = PageFacts(**WOOT["page"]).product
    assert shopping.query_for(p) == "JBL Flip 6 Portable IP67 Waterproof Speaker" and shopping.model_name(p) == "jblflip6"
    assert msrp.lookup(None, name)[0] == 129.95
    assert shopping.page_condition(PageFacts(**WOOT["page"])) == "new"
    assert shopping.query_for(PageFacts(url="https://x", product={"name": "New Balance 990v6 Running Shoe"}).product) \
        == "New Balance 990v6 Running Shoe"  # a bare "New" is part of the name


def _verdict(monkeypatch, url, page):
    for k, v in {"SAMPLE_MODE": "false", "AGENT_DEAL": "real", "AGENT_PRICE": "real", "AGENT_TRUST": "stub"}.items():
        monkeypatch.setenv(k, v)
    _stub_offers(monkeypatch)
    client = TestClient(stub_server.app)
    cid = client.post("/check?fresh=1", json={"url": url, "page": page}).json()["check_id"]
    with client.stream("GET", f"/check/{cid}/events") as r:
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    return [e["card"] for e in events if e["kind"] == "final_card"][-1], [e["finding"] for e in events if e["kind"] == "finding"]


def test_the_woot_jbl_flip_6_listing_is_green(monkeypatch):
    page = {**WOOT["page"], "signals": [*WOOT["page"]["signals"], STEADY]}
    card, findings = _verdict(monkeypatch, WOOT["url"], page)
    assert (card["title"], card["tone"]) == ("No tricks spotted", "good"), card["subtitle"]
    by = {f["type"]: f for f in findings}
    assert by["claimed_discount"]["status"] == "confirmed" and "launch price (MSRP)" in by["claimed_discount"]["summary"]
    assert by["countdown"]["status"] == "confirmed" and "catalog_only" not in by


def test_the_amazon_jbl_flip_6_page_is_amber_for_the_cheaper_woot_offer(monkeypatch):
    card, _findings = _verdict(monkeypatch, AMAZON["url"], AMAZON["page"])
    assert (card["title"], card["tone"], card["tricks"]) == ("Check before you buy", "caution", 0)
    assert card["best_price"]["reasons"][0]["text"] == "Same item $35 cheaper at Woot"
    assert "woot.com/search" not in card["alt"]["url"]


@pytest.mark.skipif(not shutil.which("node"), reason="needs Node to import the extension's modules")
def test_the_quick_card_and_panel_show_the_cheaper_reason():
    card = _card(tone="caution", title="Check before you buy", alt=CHEAPER,
                 reasons=[Reason(text="Same item $35 cheaper at Woot", tone="caution")]).model_dump()
    script = (f"import {{ quickVerdict }} from {json.dumps((ROOT / 'extension' / 'shared' / 'check.js').as_uri())};\n"
              f"console.log(JSON.stringify(quickVerdict({json.dumps(card)}, [], {{ product: {{ price: 114.95 }} }})));\n")
    out = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    v = json.loads(out.stdout)
    assert (v["tone"], v["title"]) == ("caution", "Check before you buy")
    assert v["lines"][0][0][0] == "Same item $35 cheaper at Woot"
