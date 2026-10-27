"""Fixes from the live run (check b804777bb2d7): the page finding's model label, the "no model number" line only when
nothing was compared, every credible store in the discount finding, and the evidence page's trace that grows with its rows."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend import stub_server
from backend.agents import price
from backend.contracts import PageFacts
from backend.tests.test_real_demo import RAW, SELLERS, URL, jbl, page  # noqa: F401  (fixture)
from backend.tools import discount, shopping

ROOT = Path(__file__).resolve().parents[2]


def test_page_findings_say_extension_outside_sample_mode(monkeypatch):
    pf = PageFacts(url=URL, host="www.amazon.com", product={"name": "JBL Flip 6", "price": 114.95})
    [read] = [f for f in stub_server.real_findings("c", pf) if f.agent == "page"]
    assert read.model == "extension (no model call)"
    monkeypatch.setenv("SAMPLE_MODE", "true")
    [read] = [f for f in stub_server.real_findings("c", pf) if f.agent == "page"]
    assert read.model == "stub (no model call)"  # "stub" only in sample mode


def test_no_model_number_line_only_when_nothing_was_compared(jbl, monkeypatch):
    res = price.assess("c1", URL, page())  # the comparison succeeded: no "can't be matched" line
    assert not [f for f in res.findings if f.type == "catalog_only"]
    assert not any("can't be matched" in r.text for r in res.best_price.reasons)
    monkeypatch.setattr(shopping, "_search_serpapi", lambda q: [])  # nothing found anywhere
    monkeypatch.setattr(shopping, "_sellers_serpapi", lambda t: [])
    shopping.LIVE.clear()
    from backend.data import repo
    repo.OFFERS_MEMORY.clear()
    res = price.assess("c2", URL, page())
    [f] = [f for f in res.findings if f.type == "catalog_only"]
    assert f.summary.startswith("This page shows no model number")


def test_discount_finding_lists_every_credible_store_lower_same_higher(jbl):
    jbl["raw"] = [*RAW, {"title": "JBL Flip 6 Portable Waterproof Speaker - Blue", "source": "Target", "price": "$134.95",
                         "extracted_price": 134.95, "delivery": "Free delivery"}]
    [f] = discount.check_discount("c", URL, page()).findings
    # 3 credible stores in the search: no sellers list (the Price Agent's rule), so these are the comparison's stores
    assert f.summary.endswith("Credible stores now: Woot $79.95 lower, Walmart $114.95 same, Target $134.95 higher.")
    assert "credible stores charge from $79.95 to $134.95" in f.summary


def test_discount_and_price_read_the_same_offers(jbl, monkeypatch):
    """Live: the Price Agent read safe_price (with Woot), the Deal Agent the newer cache (without): they disagreed."""
    from backend.data import repo

    usl = [o for o in shopping.parse(RAW) if o["source"] in ("Woot", "Walmart")]
    monkeypatch.setattr(repo, "usl_offers", lambda key: (usl, "SELECT ... FROM lightning.metastore.pricise.pricise_usl.safe_price"))
    jbl["raw"] = [r for r in RAW if r["source"] != "Woot"]  # the newest search: Woot gone
    [f] = discount.check_discount("c", URL, page()).findings
    res = price.assess("c1", URL, page())
    assert "Woot $79.95 lower" in f.summary and res.online["shown"][0]["store"] == "Woot"


@pytest.mark.skipif(not shutil.which("node"), reason="needs Node to import the extension's modules")
def test_trace_tab_grows_shows_model_and_source_and_caps_at_40():
    def run(n):
        findings = [{"agent": "price", "type": "best_price", "status": "confirmed", "summary": f"row {i}", "ts": 1,
                     "model": "nvidia/nemotron-3-ultra-550b-a55b",
                     "evidence": [{"kind": "sql", "ref": "SELECT x FROM lightning.metastore.pricise.pricise_usl.safe_price"}]}
                    for i in range(n)]
        script = (f"import {{ tracePanel }} from {json.dumps((ROOT / 'extension' / 'evidence' / 'sections.js').as_uri())};\n"
                  f"console.log(JSON.stringify(tracePanel({json.dumps(findings)})));\n")
        out = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout)

    html = run(12)
    assert html.count('class="r"') == 12 and "hidden" not in html and "Show all" not in html
    assert "Model: nvidia/nemotron-3-ultra-550b-a55b · Source: Zetaris pricise_usl.safe_price" in html
    html = run(45)
    assert html.count('class="r"') == 40 and html.count('class="r more"') == 5 and "Show all 45 findings" in html
    css = (ROOT / "extension" / "evidence" / "index.html").read_text()
    trace_css = next(line for line in css.splitlines() if line.strip().startswith(".trace {"))
    assert "max-height" not in trace_css and "overflow" not in trace_css  # no inner scroll: the page scrolls
