"""Checks that the stub backend keeps its contract with the extension. Run: python -m pytest backend/tests"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import stub_server
from backend.stub_server import app

stub_server.DEMO_DELAY = 0  # no waiting in tests
client = TestClient(app)


def _events(cid: str) -> list[dict]:
    with client.stream("GET", f"/check/{cid}/events") as r:
        return [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]


def test_health():
    assert client.get("/health").json()["ok"] is True


def test_demo_page_streams_three_tricks_and_store_c():
    cid = client.post("/check", json={"url": "http://localhost:8787/storelab/audiodealz/"}).json()["check_id"]
    events = _events(cid)
    card = events[-1]["card"]
    assert events[-1]["kind"] == "final_card"
    assert card["tricks"] == 3 and card["best_price"]["total"] == 229
    assert "$191" in card["subtitle"]


def test_real_page_uses_page_signals_and_stays_careful():
    page = {"url": "https://shop.example/item", "host": "shop.example", "https": True,
            "product": {"name": "Ticket", "price": 89.0},
            "signals": [{"type": "drip_fees", "selector": "#a", "text": "Service fee $18.50", "amount": 18.5},
                        {"type": "drip_fees", "selector": "#b", "text": "Facility fee $6.00", "amount": 6.0},
                        {"type": "prechecked_addon", "selector": "#c", "text": "Add insurance for $9.99", "amount": 9.99},
                        {"type": "claimed_discount", "selector": "#d", "text": "Was $129"}]}
    cid = client.post("/check", json={"url": page["url"], "page": page}).json()["check_id"]
    events = _events(cid)
    card = events[-1]["card"]
    assert card["tricks"] == 2  # fees grouped into one trick + the add-on
    assert "$123.49" in card["subtitle"]
    claim = next(e["finding"] for e in events if e["kind"] == "finding" and e["finding"]["type"] == "claimed_discount")
    assert claim["status"] == "unverified"  # never call a real store's discount fake without price history


def test_demo_clock_sends_one_alert_on_day_9():
    client.post("/demo/clock", json={"day": 1})
    first = client.post("/demo/clock", json={"day": 9}).json()
    again = client.post("/demo/clock", json={"day": 9}).json()
    assert len(first["alerts"]) == 1 and again["alerts"] == []
    client.post("/demo/clock", json={"day": 1})  # leave the clock as other tests expect it


def test_demo_clock_is_back_on_day_1_after_the_clock_test():
    """Runs right after the clock test: a clock left on Day 9 makes later checks show $209 instead of $229."""
    assert stub_server.CLOCK["day"] == 1


def test_agent_deal_real_streams_real_deal_findings(monkeypatch):
    """AGENT_DEAL=real swaps only the deal findings; everything else (trust, price, card) stays the same."""
    from backend.agents import deal
    from backend.tools.common import ToolResult, finding

    async def fake_runahead(cid, url, page):
        return ToolResult([finding(cid, "drip_fees", "confirmed", "$37.01 in fees appear only at checkout step 4. Real total: $191.00.")],
                          {"fees": [{"label": "Item", "amount": 149.0, "step": 1}], "total": 191.0})

    async def no_timer(cid, url, page):
        return ToolResult()
    monkeypatch.setenv("AGENT_DEAL", "real")
    monkeypatch.setattr(deal, "check_runahead", fake_runahead)
    monkeypatch.setattr(deal, "check_timer", no_timer)
    page = {"url": "http://localhost:8787/storelab/audiodealz/", "host": "localhost:8787",
            "product": {"price": 149.0, "sku": "WH1000XM5/B"},
            "signals": [{"type": "claimed_discount", "selector": "#deal-badge", "text": "-40% List Price: $249.00", "amount": 249.0},
                        {"type": "prechecked_addon", "selector": "#addon-row", "text": "Protection Plan for $4.99", "amount": 4.99}]}
    cid = client.post("/check", json={"url": page["url"], "page": page}).json()["check_id"]
    events = _events(cid)
    deal_f = [e["finding"] for e in events if e["kind"] == "finding" and e["finding"]["agent"] == "deal"]
    assert {f["type"] for f in deal_f} == {"fake_discount", "prechecked_addon", "drip_fees"}
    assert all(f["model"] != "nemotron fast lane (stub)" for f in deal_f)  # real agent, not the script
    assert any(e["finding"]["agent"] == "trust" for e in events if e["kind"] == "finding")
    assert events[-1]["kind"] == "final_card" and events[-1]["card"]["best_price"]["total"] == 229
    ev = client.get(f"/check/{cid}").json()["evidence_data"]
    assert ev["total"] == 191.0 and ev["price_history"]["days_at_claim"] == 2


def test_unverified_tricks_are_not_counted():
    from backend.contracts import PageFacts
    page = PageFacts(url="https://x.example", host="x.example")
    f = stub_server._f("c", "deal", "drip_fees", "unverified", "Couldn't check late checkout fees.")
    assert stub_server.real_card("c", page, [f], 1.0).tricks == 0


@pytest.mark.parametrize("orch", ["off", "on"])  # on: the orchestrator's Skeptic checks evidence (rule 1)
def test_agent_deal_real_sends_a_verdict_early_and_firms_it_up(monkeypatch, orch):
    """The first verdict goes out when the fast Deal tools finish; later ones arrive as the slow proof lands."""
    import asyncio as aio
    from backend.agents import deal
    from backend.tools.common import ToolResult, finding

    async def slow_runahead(cid, url, page):
        await aio.sleep(0.3)
        return ToolResult([finding(cid, "drip_fees", "confirmed", "$37.01 in fees appear only at checkout step 4 (x). Real total: $191.00.",
                                   [{"kind": "screenshot", "ref": "runahead:step4"}])],
                          {"fees": [{"label": "Item", "amount": 149.0, "step": 1}], "total": 191.0})

    async def slow_timer(cid, url, page):
        await aio.sleep(0.6)
        return ToolResult([finding(cid, "resetting_timer", "confirmed", "The timer restarts at 09:59 on every visit.",
                                   [{"kind": "screenshot", "ref": "timer:visit1"}])])
    monkeypatch.setenv("AGENT_ORCH", orch)
    monkeypatch.setenv("AGENT_DEAL", "real")
    monkeypatch.setattr(deal, "check_runahead", slow_runahead)
    monkeypatch.setattr(deal, "check_timer", slow_timer)
    page = {"url": "http://localhost:8787/storelab/audiodealz/", "host": "localhost:8787",
            "product": {"price": 149.0, "sku": "WH1000XM5/B"},
            "signals": [{"type": "claimed_discount", "selector": "#deal-badge", "text": "List Price: $249.00", "amount": 249.0},
                        {"type": "countdown", "selector": "#timer", "text": "Deal ends in 09:58"}]}
    cid = client.post("/check?fresh=1", json={"url": page["url"], "page": page}).json()["check_id"]
    events = _events(cid)
    kinds = [e["kind"] if e["kind"] == "final_card" else e["finding"]["type"] for e in events]
    if orch == "off":  # the orchestrator sends a card when an agent finishes, not when the fast Deal tools do
        first_card = kinds.index("final_card")
        assert first_card < kinds.index("resetting_timer")  # verdict before the timer proof
        first = events[first_card]["card"]
        assert "Checking if the timer resets…" in [r["text"] for r in first["honest"]["reasons"]]
    last = events[-1]["card"]
    assert last["tricks"] == 3 and "$191" in last["subtitle"]
    assert all(r["text"] != "Checking if the timer resets…" for r in last["honest"]["reasons"])
    stored = client.get(f"/check/{cid}").json()
    assert all(f.get("model") != deal.PENDING for f in stored["findings"])  # no stale "still checking" rows
    assert set(stored["evidence_data"]["timings"]) >= {"runahead", "timer"}


# real_card: what the card shows the shopper (bench pages fd-02, t-01, ad-06)

def _rc_page(https=True):
    from backend.contracts import PageFacts
    return PageFacts(url="http://localhost:8787/bench/deal/x.html", host="localhost:8787", https=https,
                     product={"name": "Phone Case", "price": 24.99})


def _rc_finding(type_, status):
    from backend.contracts import Evidence, Finding
    return Finding(check_id="c", agent="deal", type=type_, status=status, summary=f"{type_} {status}",
                   evidence=[Evidence(kind="selector", ref="#x")])


def test_real_card_counts_a_confirmed_fake_discount():  # bench fd-02: was good/0
    card = stub_server.real_card("c", _rc_page(), [_rc_finding("fake_discount", "confirmed")], 1.0)
    assert (card.tone, card.tricks, card.honest.status) == ("caution", 1, "bad")


def test_real_card_headline_follows_an_unsafe_or_caution_store():  # bench t-01: safe bad, tone was good
    from backend.agents import trust
    tv = {"verdict": "unsafe", "reasons": ["Domain registered 4 days ago."], "findings": [], "store": None,
          "store_url": "x", "checked": True, "elapsed_ms": 1}
    card = stub_server._with_trust(stub_server.real_card("c", _rc_page(), [], 1.0), tv)
    assert (card.safe.status, card.tone, card.title) == ("bad", "bad", "Don't buy here")
    card = stub_server._with_trust(stub_server.real_card("c", _rc_page(), [], 1.0), {**tv, "verdict": "caution"})
    assert (card.safe.status, card.tone, card.title) == ("caution", "caution", "Be careful here")
    no_https = stub_server._with_trust(stub_server.real_card("c", _rc_page(https=False), [], 1.0), None)  # stub Trust
    assert (no_https.safe.status, no_https.tone) == ("bad", "bad")  # real_card's own "Risky" (no HTTPS) is final here
    assert trust.safe_verdict(tv).status == "bad"


def test_headline_uses_the_final_safe_section_not_real_cards_provisional_one():
    """Bench pages are http://: real_card's provisional "Shop safe?" is bad (no HTTPS), then the Trust verdict replaces
    it. A store Trust calls safe must not keep a "bad" headline (bench t-08: Safe good, tone was bad)."""
    safe = {"verdict": "safe", "reasons": ["No warning signs found."], "findings": [], "store": None, "store_url": "x",
            "checked": True, "elapsed_ms": 1}
    card = stub_server._with_trust(stub_server.real_card("c", _rc_page(https=False), [], 1.0), safe)
    assert (card.safe.status, card.tone, card.title) == ("good", "good", "No tricks spotted")


def test_real_card_counts_confirmed_tricks_only():  # bench ad-06: a no-cost opt-in box was "1 trick / Be careful here"
    card = stub_server.real_card("c", _rc_page(), [_rc_finding("prechecked_addon", "proposed")], 1.0)
    assert (card.tricks, card.title, card.tone, card.honest.status) == (0, "Check before you buy", "caution", "caution")
    assert card.honest.reasons[0].text == "prechecked_addon proposed"


# CORS, test isolation, the panel's rows, the page reader, the run scripts

def test_cors_allows_only_the_extension_and_localhost():
    for origin in ("chrome-extension://abcdefghijklmnopabcdefghijklmnop", "http://localhost:8787", "http://127.0.0.1:5500"):
        assert client.get("/health", headers={"Origin": origin}).headers.get("access-control-allow-origin") == origin
    for origin in ("https://evil.example", "http://localhost.evil.example", "null"):
        assert "access-control-allow-origin" not in client.get("/health", headers={"Origin": origin}).headers


def test_conftest_pins_the_orchestrator_switch_and_the_sidecar_url():
    """A developer's shell or .env (AGENT_ORCH=on, a running sidecar) must not change test results."""
    assert "AGENT_ORCH" not in os.environ and os.environ["WORLD_MODEL_URL"] == "http://127.0.0.1:1"


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(not shutil.which("node"), reason="needs Node to import extension/shared/check.js")
def test_panel_replaces_a_finding_version_instead_of_adding_a_row():
    script = (f"import {{ upsertFinding }} from {json.dumps((ROOT / 'extension' / 'shared' / 'check.js').as_uri())};\n"
              "const ev = [{ kind: 'selector', ref: '#t' }, { kind: 'screenshot', ref: 'v1' }];\n"
              "let rows = [];\n"
              "for (const status of ['confirmed', 'challenged', 'rejected'])\n"
              "  rows = upsertFinding(rows, { check_id: 'c', agent: 'deal', type: 'resetting_timer', status, evidence: ev });\n"
              "rows = upsertFinding(rows, { check_id: 'c', agent: 'deal', type: 'resetting_timer', status: 'confirmed',\n"
              "                             evidence: [ev[1], ev[0]] });  // same evidence, other order: same finding\n"
              "rows = upsertFinding(rows, { check_id: 'c', agent: 'deal', type: 'drip_fees', status: 'confirmed', evidence: [] });\n"
              "console.log(JSON.stringify(rows.map((r) => [r.type, r.status])));\n")
    out = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [["resetting_timer", "confirmed"], ["drip_fees", "confirmed"]]
    background = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
    assert "upsertFinding(c.findings, f)" in background and "[...c.findings, f]" not in background


def _browser_ok() -> bool:
    import asyncio
    from backend.tools.browser import chromium

    async def go():
        async with chromium():
            return True
    try:
        return asyncio.run(go())
    except Exception:
        return False


@pytest.mark.skipif(not _browser_ok(), reason="needs the hidden browser (Playwright Chromium or PLAYWRIGHT_CHROMIUM_PATH)")
def test_content_reader_reads_a_page_on_current_chromium():
    """Chromium defines window.chrome as non-configurable; the fake chrome.* API must still install."""
    import asyncio
    from backend.tests.content_reader import read_page_facts, serve_repo

    server, base = serve_repo()
    try:
        facts = asyncio.run(read_page_facts(f"{base}/bench/deal/fd-02.html"))
    finally:
        server.shutdown()
    assert facts["product"]["name"] == "Phone Case"


@pytest.mark.parametrize("name", ["setup.sh", "setup.ps1"])
def test_setup_scripts_install_the_sidecar_with_node_20(name):
    text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
    assert "npm install --ignore-scripts" in text and "backend/engines/world_model_ts".replace("/", "\\" if name.endswith(".ps1") else "/") in text
    assert "20" in text and "node" in text.lower()


@pytest.mark.parametrize("name, stop", [("run.sh", "kill"), ("run.ps1", "Stop-Process")])
def test_run_scripts_start_and_stop_the_sidecar(name, stop):
    text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
    assert "server.ts" in text and "tsx" in text and "8788" in text and stop in text
