"""The quick card in every state (checking, live trick count, final verdicts, backend down, check failed), the compare links
(same in the extension and the backend), and no auto-hide. The JS is run with Node, as the extension runs it."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.compare import compare_links, compare_query
from backend.contracts import PageProduct

ROOT = Path(__file__).resolve().parents[2]
CHECK_JS = (ROOT / "extension" / "shared" / "check.js").as_uri()
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="needs Node to import extension/shared/check.js")


def _node(body: str):
    script = f"import * as m from {json.dumps(CHECK_JS)};\nconsole.log(JSON.stringify({body}));\n"
    out = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _text(view):
    return " ".join("".join(seg[0] for seg in line) for line in view["lines"])


PAGE = {"product": {"price": 149, "brand": "Sony", "model": "WH-1000XM5"}, "signals": []}
TRICK = {"agent": "deal", "type": "drip_fees", "status": "confirmed", "summary": "$37 fees", "evidence": []}


@needs_node
def test_card_states():
    views = _node(f"""[
      m.cardView({{ page: {json.dumps(PAGE)} }}),
      m.cardView({{ page: {json.dumps(PAGE)}, findings: [{json.dumps(TRICK)}] }}),
      m.cardView({{ error: 'Failed to fetch', errorKind: 'backend' }}),
      m.cardView({{ error: '/check/x/events returned 500', errorKind: 'check' }}),
      m.cardView({{ page: {json.dumps(PAGE)}, findings: [{json.dumps(TRICK)}],
                    card: {{ tricks: 1, subtitle: '1 trick · real total $186.00, not $149.00', safe: {{ status: 'unknown' }},
                             honest: {{ reasons: [] }}, best_price: {{}} }} }}),
      m.cardView({{ page: {json.dumps(PAGE)}, findings: [], card: {{ tricks: 0, subtitle: '', safe: {{}}, honest: {{}},
                    best_price: {{}} }} }}),
    ]""")
    checking, live, down, failed, red, green = views
    assert (checking["state"], checking["tone"], checking["title"]) == ("checking", "unknown", "Checking this page…")
    assert "listed $149" in _text(checking)
    assert (live["state"], live["tone"]) == ("checking", "bad") and "1 trick found so far" in _text(live)
    assert (down["state"], down["title"]) == ("error", "Pricise backend isn't running") and "scripts/run.sh" in _text(down)
    assert (failed["state"], failed["title"]) == ("error", "Couldn't check this page")
    assert (red["state"], red["tone"], red["title"]) == ("final", "bad", "1 trick on this page")
    assert (green["tone"], green["title"]) == ("good", "No tricks spotted")


@needs_node
def test_caution_state_is_amber():
    claim = {"agent": "deal", "type": "claimed_discount", "status": "unverified", "summary": "Was $199", "evidence": []}
    view = _node(f"m.cardView({{ page: {json.dumps(PAGE)}, findings: [{json.dumps(claim)}], card: {{ tricks: 0, subtitle: '',"
                 f" safe: {{}}, honest: {{}}, best_price: {{}} }} }})")
    assert (view["tone"], view["title"]) == ("caution", "Check before you buy")


@pytest.mark.parametrize("product, query", [
    ({"brand": "Sony", "model": "WH-1000XM5", "name": "Sony WH-1000XM5 Headphones"}, "Sony WH-1000XM5"),
    ({"brand": "Sony", "model": "Sony WH-1000XM5"}, "Sony WH-1000XM5"),          # brand already in the model
    ({"name": "Acme Kettle 2L | Acme Shop"}, "Acme Kettle 2L"),                    # no model: the name, store suffix dropped
    ({}, ""),
])
def test_compare_query(product, query):
    assert compare_query(PageProduct(**product)) == query


@needs_node
def test_compare_links_are_the_same_in_the_extension_and_the_backend():
    product = {"brand": "Sony", "model": "WH-1000XM5/B", "name": "x"}
    js = _node(f"m.compareLinks({json.dumps(product)})")
    assert js == compare_links(PageProduct(**product))
    assert [x["label"] for x in js] == ["Google Shopping", "Best Buy", "Walmart", "eBay"]
    assert js[0]["url"] == "https://www.google.com/search?tbm=shop&q=Sony%20WH-1000XM5%2FB"
    assert _node("[m.isCompareUrl('https://www.ebay.com/sch/i.html?_nkw=x'), m.isCompareUrl('https://evil.example/?q=x')]") == [True, False]


def test_card_never_hides_itself_and_shows_from_the_start():
    card = (ROOT / "extension" / "content" / "quick-card.js").read_text()
    bg = (ROOT / "extension" / "background.js").read_text()
    assert "HIDE_AFTER_MS" not in card and "setTimeout(() => hide" not in card
    assert "if (wantCard) showCard(tabId, c); // straight away" in bg       # "Checking this page…" before any finding
    assert "if (c.wantCard && !c.card) showCard(tabId, c); // \"Checking" in bg  # live updates while checking
    assert "c.errorKind = " in bg and "showCard(tabId, c); // the card says so" in bg
    assert "isCompareUrl(msg.url)" in bg  # the card can only open our own search links
