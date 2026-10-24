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
