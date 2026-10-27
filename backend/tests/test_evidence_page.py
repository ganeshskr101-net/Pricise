"""3-5 credible stores (search + the product's sellers list), the cheaper-at-top rule, and the evidence page for a real store
check: only sections with content, and an anchor for every Proof / See why / Compare link. Saved SerpApi data only."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.agents import price
from backend.contracts import PageFacts
from backend.tests.test_online_prices import KINDLE, KINDLE_RAW, KINDLE_URL, TARGET
from backend.tools import shopping

ROOT = Path(__file__).resolve().parents[2]
FIX = Path(__file__).resolve().parent / "fixtures" / "serpapi"
SELLERS = json.loads((FIX / "kindle_sellers.json").read_text())["stores"]
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="needs Node to import the extension's modules")
# Synthetic sellers for the cases the saved list lacks (clearly not from SerpApi).
WALMART = {"name": "Walmart", "title": "Amazon Kindle Paperwhite Signature Edition 32GB Metallic Black", "price": "$244.99",
           "extracted_price": 244.99, "shipping": "Free", "link": "https://www.walmart.com/ip/test/1"}
COSTCO = {"name": "Costco", "title": "Amazon Kindle Paperwhite Signature Edition 32GB (2024) Metallic Black", "price": "$229.99",
          "extracted_price": 229.99, "shipping": "Free", "link": "https://www.costco.com/test.product.1.html"}
FINANCED = {"name": "Best Buy", "title": "Amazon Kindle Paperwhite Signature Edition 32GB", "price": "$249.99",
            "extracted_price": 20.83, "details_and_offers": ["$20.83/mo for 12 months"], "link": "https://www.bestbuy.com/x"}
USED = {"name": "Target", "title": "Amazon Kindle Paperwhite Signature Edition 32GB", "price": "$189.99", "extracted_price": 189.99,
        "details_and_offers": ["Used - like new"], "link": "https://www.target.com/x"}


@pytest.fixture
def online(monkeypatch):
    calls = {"search": 0, "sellers": 0, "raw": [dict(r) for r in KINDLE_RAW], "stores": list(SELLERS)}
    best = next(r for r in calls["raw"] if r["source"] == "Best Buy" and r["extracted_price"] == 249.99)
    best["immersive_product_page_token"] = "test-token"

    def fake_search(q):
        calls["search"] += 1
        return shopping.parse(calls["raw"])

    def fake_sellers(token):
        assert token == "test-token"  # the best-matching product's sellers list
        calls["sellers"] += 1
        return shopping.parse_sellers(calls["stores"])

    monkeypatch.setenv("SERPAPI_KEY", "test-key-not-real")
    monkeypatch.setattr(shopping, "_search_serpapi", fake_search)
    monkeypatch.setattr(shopping, "_sellers_serpapi", fake_sellers)
    monkeypatch.setattr(price, "_vet_domains", lambda cid, ds: {d: {"verdict": "safe", "checked": True, "reasons": []} for d in ds})
    return calls


def _kindle(price_=249.99):
    page = PageFacts(url=KINDLE_URL, host="www.amazon.com", product={**KINDLE.model_dump(), "price": price_})
    return price.assess("c1", KINDLE_URL, page)


def test_sellers_list_brings_more_credible_stores_with_their_own_links(online):
    res = _kindle()
    assert online["sellers"] == 1 and online["search"] == 1
    shown = res.online["shown"]
    assert [s["store"] for s in shown] == ["Best Buy", "Target"]  # P.C. Richard is not on the allowlist
    assert shown[1]["url"] == "https://www.target.com/product/test-1"  # the store's own listing, not a Google page
    assert res.online["also_checked"] == ["Walmart (not found)"]  # fewer than 3: say who else was looked for
    assert res.findings[-1].summary == ("Same price at Best Buy and Target, this page is as good as it gets. "
                                        "Also checked: Walmart (not found).")
    assert res.alt == {"kind": "this_page"}


def test_three_to_five_stores_one_per_store_cheapest_first_and_cheaper_on_top(online):
    online["stores"] = [*SELLERS, WALMART, COSTCO, FINANCED, USED]
    online["raw"] = [TARGET, *online["raw"]]
    res = _kindle()
    assert [s["store"] for s in res.online["shown"]] == ["Costco", "Target", "Walmart", "Best Buy"]
    assert res.online["also_checked"] == []
    assert res.alt == {"kind": "cheaper", "store": "Costco", "total": 229.99, "url": "https://www.costco.com/test.product.1.html", "save": 20.0}
    groups = {d["group"] for d in res.online["dropped"]}
    assert {"Installment or monthly price", "Used or refurbished", "Unknown store"} <= groups


def test_not_a_dollar_cheaper_is_not_cheaper(online):
    online["stores"] = [*SELLERS, {**COSTCO, "price": "$249.49", "extracted_price": 249.49}]
    res = _kindle(249.99)
    assert res.alt == {"kind": "this_page"} and not [f for f in res.findings if f.type == "best_price"]


@needs_node
def test_cheaper_row_and_best_price_note_in_the_card():
    check_js = (ROOT / "extension" / "shared" / "check.js").as_uri()
    card = {"tricks": 0, "subtitle": "", "safe": {}, "honest": {}, "best_price": {"status": "good"}}
    script = (f"import * as m from {json.dumps(check_js)};\n"
              f"const card = {json.dumps(card)};\n"
              "const cheaper = m.quickVerdict({ ...card, alt: { kind: 'cheaper', store: 'Best Buy', total: 229.99, url: 'https://x', save: 20 } }, [], { product: { price: 249.99 } });\n"
              "const here = m.summarize({ ...card, alt: { kind: 'this_page' } }, [], { product: { price: 249.99 } });\n"
              "console.log(JSON.stringify([cheaper.lines[1], here.safer, here.bestHere]));\n")
    out = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    line, safer, best_here = json.loads(out.stdout)
    assert "".join(seg[0] for seg in line) == "Cheaper at Best Buy · $229.99 (save $20)"
    assert safer is None and best_here is True
    panel = (ROOT / "extension" / "sidepanel" / "panel.js").read_text()
    assert "Best price: this page" in panel and "Cheaper at ${esc(s.safer.store)}" in panel


# ----------------------------------------------------------------------------- evidence page for a real store check

@needs_node
def test_real_store_evidence_has_only_full_sections_with_anchors(online):
    res = _kindle()
    chk = {"url": KINDLE_URL, "page": {"url": KINDLE_URL, "host": "www.amazon.com", "product": {**KINDLE.model_dump(), "price": 249.99},
                                       "signals": [{"type": "store_info", "text": "returns link: yes · contact link: yes"}]},
           "findings": [f.model_dump() for f in res.findings] + [
               {"agent": "price", "type": "best_price", "status": "confirmed", "summary": "x", "model": "nvidia/nemotron-3-ultra-550b-a55b", "ts": 1}],
           "evidence_data": {"fees": [{"label": "Item", "amount": 249.99}], "timer_frames": [], "price_history": None,
                             "stores": res.stores, "online": res.online,
                             "store_checks": [{"check": "domain_age", "result": "Domain registered 32 years ago.", "status": "ok",
                                               "evidence": [{"kind": "url", "ref": "https://rdap.org/domain/amazon.com"},
                                                            {"kind": "log", "ref": "domain_created=1994-11-01"}]},
                                              {"check": "store_records", "result": "Company record and reviews aren't checked.",
                                               "status": "unchecked", "evidence": []}]}}
    script = (f"import {{ sections }} from {json.dumps((ROOT / 'extension' / 'evidence' / 'sections.js').as_uri())};\n"
              f"console.log(JSON.stringify(sections({json.dumps(chk)})));\n")
    out = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    secs = {s["id"]: s["html"] for s in json.loads(out.stdout)}
    assert list(secs) == ["overview", "page", "whois", "stores", "chart", "trace"]  # no empty timer replay or fee table
    for proof in ("whois", "stores", "chart"):  # every Proof / See why / Compare anchor the panel uses
        assert f'id="{proof}"' in secs[proof]
    assert "$249.99" in secs["page"] and "Amazon Kindle" in secs["page"]
    assert "rdap.org/domain/amazon.com" in secs["whois"] and "domain_created=1994-11-01" in secs["whois"] and "Not checked" in secs["whois"]
    assert "Best Buy" in secs["stores"] and "https://www.target.com/product/test-1" in secs["stores"]
    assert "Unknown store" in secs["stores"] and "Marketplace seller" in secs["stores"] and "Used or refurbished" in secs["stores"]
    assert "No price history for this store yet" in secs["chart"]
    assert "nvidia/nemotron-3-ultra-550b-a55b" in secs["trace"]


def test_drop_groups():
    assert price.drop_group("under 50% of this page's $249.99 (likely a monthly, partial or wrong price)") == "Under 50% (not a real price)"
    assert price.drop_group('"/mo" in its price') == "Installment or monthly price"
    assert price.drop_group('"pre-owned" in its condition') == "Used or refurbished"
    assert price.drop_group("not the same item: other edition (colorsoft)") == "Different item"
