"""Prices from the Zetaris snapshot say when they were fetched (the search's own search_metadata.created_at), on the
evidence page, the side panel's best-price card and the Deal line: the offers are a cached snapshot, not a live search.
No live calls: Zetaris, Postgres and SerpApi are stubbed."""
import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend import rechecker
from backend.agents import price
from backend.data import repo, zetaris
from backend.tests.test_real_demo import page
from backend.tests.test_usl import PAGE, URL, USL_ROWS, stubs  # noqa: F401  (fixture)
from backend.tools import discount

ROOT = Path(__file__).resolve().parents[2]
CREATED = "2026-10-27 07:36:33 UTC"  # what the live snapshot returned
AT = datetime(2026, 10, 27, 7, 36, 33, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def snapshot(stubs, monkeypatch):  # noqa: F811
    """safe_price rows from the cached search (origin serpapi_live), and the snapshot's own time."""
    rows = [{**r, "origin": "serpapi_live"} for r in USL_ROWS]
    monkeypatch.setattr(repo, "_SNAPSHOT_AT", [])

    def run_sql(sql, limit=1000, timeout=8):
        stubs["sql"].append(sql)
        if sql == repo.SNAPSHOT_SQL:
            return [{"created": CREATED}]
        if "pricise_usl.safe_price" in sql:
            return rows
        raise zetaris.ZetarisError("not stubbed")

    monkeypatch.setattr(zetaris, "run_sql", run_sql)
    return stubs


def test_usl_offers_carry_the_snapshot_time(snapshot):
    offers, _ = repo.usl_offers("jblflip6blue")
    assert {o["fetched_at"] for o in offers} == {AT}
    assert repo.USL_FETCHED["jblflip6blue"] == [{"what": "Google Shopping (Zetaris snapshot)", "at": AT}]
    repo.usl_offers("jblflip6blue")
    assert snapshot["sql"].count(repo.SNAPSHOT_SQL) == 1  # remembered: one small read, never a new search
    assert "serpapi.com" not in repo.SNAPSHOT_SQL and repo.SNAPSHOT_SQL.startswith("SELECT ")


def test_price_agent_puts_the_time_on_the_comparison_and_each_row(snapshot):
    res = price.assess("c1", URL, PAGE)
    assert res.online["source"] == "zetaris usl"
    assert res.online["fetched"] == [{"what": "Google Shopping (Zetaris snapshot)", "at": AT}]
    assert res.online["shown"] and all(s["fetched_at"] == AT for s in res.online["shown"])


def test_card_reason_and_text_use_local_time(monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        online = {"fetched": [{"what": "Google Shopping (Zetaris snapshot)", "at": AT}]}
        [r] = rechecker.fetched_reason(online)
        assert r.text == "Prices from Google Shopping (Zetaris snapshot), fetched 3:36 AM Oct 27" and r.proof == "stores"
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()
    # a live search is "now": no line on the card
    assert rechecker.fetched_reason({"fetched": [{"what": "Google Shopping (live search)", "at": time.time()}]}) == []
    assert rechecker.fetched_reason({}) == []


def test_snapshot_time_unreadable_means_no_time_not_a_failure(snapshot, monkeypatch):
    def run_sql(sql, limit=1000, timeout=8):
        if sql == repo.SNAPSHOT_SQL:
            raise zetaris.ZetarisError("Zetaris did not answer within 8s")
        return [{**r, "origin": "serpapi_live"} for r in USL_ROWS]

    monkeypatch.setattr(zetaris, "run_sql", run_sql)
    offers, _ = repo.usl_offers("jblflip6blue")
    assert len(offers) == 4 and repo.USL_FETCHED["jblflip6blue"] == []
    assert price.assess("c1", URL, PAGE).online["fetched"] == []


def test_cache_rows_get_the_cache_time(snapshot, monkeypatch):
    rows = [{**USL_ROWS[0], "origin": "serpapi_live"}, {**USL_ROWS[1], "origin": "pricise_cache"}]
    monkeypatch.setattr(zetaris, "run_sql", lambda sql, limit=1000, timeout=8: [{"created": CREATED}] if sql == repo.SNAPSHOT_SQL else rows)
    monkeypatch.setattr(repo, "cache_fetched_at", lambda key: AT + 3600)
    offers, _ = repo.usl_offers("jblflip6blue")
    assert [o["fetched_at"] for o in offers] == [AT, AT + 3600]
    assert [x["what"] for x in repo.USL_FETCHED["jblflip6blue"]] == ["Google Shopping (Zetaris snapshot)", "Google Shopping (Pricise cache)"]


def test_deal_line_names_the_snapshot_time(snapshot, monkeypatch):
    monkeypatch.setattr(repo, "own_observations", lambda *a, **k: [])
    [f] = discount.check_discount("c", URL, page()).findings
    assert "Woot $79.95 lower" in f.summary
    assert "(prices from Google Shopping (Zetaris snapshot), fetched " in f.summary and "Credible stores now" not in f.summary


@pytest.mark.skipif(not shutil.which("node"), reason="needs Node to import the extension's modules")
def test_extension_says_today_and_shows_the_snapshot_note():
    now = time.time()
    script = (f"import {{ fetchedAt, todayize }} from {json.dumps((ROOT / 'extension' / 'shared' / 'check.js').as_uri())};\n"
              f"const d = new Date({now} * 1000).toLocaleDateString('en-US', {{ month: 'short', day: 'numeric' }});\n"
              f"console.log(JSON.stringify([fetchedAt({now}), fetchedAt({now} - 3 * 86400),\n"
              f"  todayize('Prices from Google Shopping (Zetaris snapshot), fetched 3:36 AM ' + d),\n"
              f"  todayize('Prices from Google Shopping (Zetaris snapshot), fetched 3:36 AM Jan 2')]));\n")
    out = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    today, older, said, kept = json.loads(out.stdout)
    assert today.endswith(" today") and not older.endswith(" today")
    assert said.endswith("fetched 3:36 AM today") and kept.endswith("fetched 3:36 AM Jan 2")
    js = (ROOT / "extension" / "evidence" / "sections.js").read_text()
    assert "This is a cached snapshot, not a live search" in js and "fetched ${esc(fetchedAt(s.fetched_at))}" in js
    assert "todayize(r.text)" in (ROOT / "extension" / "sidepanel" / "panel.js").read_text()
