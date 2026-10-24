"""Checks that the stub backend keeps its contract with the extension. Run: python -m pytest backend/tests"""
import json

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
