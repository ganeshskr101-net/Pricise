"""Fixes from the live run (check 0ad4db8a2e06): the Deal Agent timed out on its own safe_price read (8 s) and lost the
MSRP finding, and "Same price at Walmart, this page is as good as it gets" appeared next to an approved cheaper Woot offer.
Also the trace's "challenged, challenged by skeptic". No live calls: Zetaris and SerpApi are stubbed."""
import json
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.agents import deal, price, skeptic
from backend.data import repo, zetaris
from backend.tests.test_real_demo import page
from backend.tests.test_usl import PAGE, URL, USL_ROWS, stubs  # noqa: F401  (fixture)
from backend.tools import discount

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def slow_usl(stubs, monkeypatch):  # noqa: F811
    """safe_price takes a moment, as live (~5 s there), so both agents ask while the first read is still running."""
    lock = threading.Lock()

    def run_sql(sql, limit=1000, timeout=8):
        with lock:
            stubs["sql"].append(sql)
        if "pricise_usl.safe_price" in sql:
            time.sleep(0.3)
            if stubs["fail"]:
                raise zetaris.ZetarisError("Zetaris did not answer within 15s")
            return USL_ROWS
        raise zetaris.ZetarisError("not stubbed")

    monkeypatch.setattr(zetaris, "run_sql", run_sql)
    monkeypatch.setattr(repo, "own_observations", lambda *a, **k: [])
    return stubs


def usl_reads(stubs):
    return sum("pricise_usl.safe_price" in s for s in stubs["sql"])


def test_price_and_deal_share_one_safe_price_read_and_the_msrp_finding_comes_back(slow_usl):
    with ThreadPoolExecutor(2) as pool:  # the two agents of one check, in parallel
        res_f = pool.submit(price.assess, "c1", URL, PAGE)
        deal_f = pool.submit(discount.check_discount, "c1", URL, page())
        res, [f] = res_f.result(), deal_f.result().findings
    assert usl_reads(slow_usl) == 1
    assert res.online["source"] == "zetaris usl"
    assert f.type == "claimed_discount" and f.status == "confirmed" and "launch price (MSRP)" in f.summary
    assert "Woot $79.95 lower" in f.summary  # the same offers the Price Agent compared


def test_a_failed_read_is_shared_too_not_retried(slow_usl):
    slow_usl["fail"] = True
    assert repo.usl_offers("jblflip6blue")[0] is None
    assert repo.usl_offers("jblflip6blue")[0] is None
    assert usl_reads(slow_usl) == 1


def test_the_shared_read_expires(slow_usl, monkeypatch):
    repo.usl_offers("jblflip6blue")
    monkeypatch.setattr(repo, "USL_SHARE_S", 0)
    repo.usl_offers("jblflip6blue")
    assert usl_reads(slow_usl) == 2


def test_callers_get_their_own_copies(slow_usl):
    a, _ = repo.usl_offers("jblflip6blue")
    a[0]["price"] = 1.0
    b, _ = repo.usl_offers("jblflip6blue")
    assert b[0]["price"] == 79.95


def test_discount_tool_gets_the_usl_time_limit(slow_usl, monkeypatch):
    assert deal._tools("c", URL, page())["discount"][1] == repo.USL_TIMEOUT_S + 2 == 17
    monkeypatch.setenv("PRICISE_USL", "off")
    assert deal._tools("c", URL, page())["discount"][1] == deal.TOOL_TIMEOUT_S == 8
    monkeypatch.delenv("PRICISE_USL")
    monkeypatch.setenv("DATA_SOURCE", "fixture")
    assert deal._tools("c", URL, page())["discount"][1] == 8


def test_no_as_good_as_it_gets_when_a_cheaper_offer_is_approved(stubs):  # noqa: F811
    res = price.assess("c1", URL, PAGE)
    assert any(f.type == "best_price" and f.status == "confirmed" and "Woot $79.95" in f.summary for f in res.findings)
    assert not [f for f in res.findings if f.type == "lowest_price"]
    assert not any("as good as it gets" in f.summary for f in res.findings)
    stores = {s["store"] for s in res.online["shown"]}
    assert {"Walmart", "Best Buy"} <= stores  # the same-price stores are in the comparison instead


def test_as_good_as_it_gets_when_the_skeptic_rejects_every_cheaper_offer(stubs, monkeypatch):  # noqa: F811
    monkeypatch.setattr(skeptic, "review_low_offer",
                        lambda ctx: {"decision": "reject", "reasons": ["too cheap to trust"], "caveats": [], "model": "test-model"})
    res = price.assess("c1", URL, PAGE)
    assert any(f.type == "best_price" and f.status == "rejected" and "Woot" in f.summary for f in res.findings)
    [f] = [f for f in res.findings if f.type == "lowest_price"]
    assert f.summary.startswith("Same price at Best Buy and Walmart, this page is as good as it gets")


def test_as_good_as_it_gets_when_only_open_box_is_cheaper(stubs, monkeypatch):  # noqa: F811
    rows = [r for r in USL_ROWS if r["offer_source"] != "Woot"]  # Walmart open box $89.97: never approvable
    monkeypatch.setattr(zetaris, "run_sql", lambda sql, limit=1000, timeout=8: rows)
    res = price.assess("c1", URL, PAGE)
    assert [f for f in res.findings if f.type == "lowest_price"][0].summary.startswith("Same price at")


@pytest.mark.skipif(not shutil.which("node"), reason="needs Node to import the extension's modules")
def test_trace_status_says_challenged_once():
    findings = [{"status": "challenged", "challenged_by": "skeptic"}, {"status": "confirmed", "challenged_by": "skeptic"},
                {"status": "proposed"}]
    script = (f"import {{ traceStatus }} from {json.dumps((ROOT / 'extension' / 'evidence' / 'sections.js').as_uri())};\n"
              f"console.log(JSON.stringify({json.dumps(findings)}.map(traceStatus)));\n")
    out = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == ["challenged by skeptic", "confirmed, challenged by skeptic", "proposed"]
