"""Chat page claims must be in the page text; non-product pages (e.g. Amazon's "Amazon.com" interstitial) end before any
agent, SerpApi search or model call, and get no quick card. No live calls."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import stub_server
from backend.agents import chat
from backend.contracts import PageFacts
from backend.llm import meterless

ROOT = Path(__file__).resolve().parents[2]
PAGE = ("Amazon Kindle Paperwhite Signature Edition 32GB. Our fastest Kindle ever, with 20% faster page turns. 7 inch "
        "glare-free display. Wireless charging. Auto-adjusting front light. Waterproof (IPX8). A single charge via USB-C "
        "lasts up to 12 weeks.")


@pytest.mark.parametrize("answer, kept", [
    ("According to this page, it is waterproof (IPX8).", "According to this page, it is waterproof (IPX8)."),
    ("According to this page, the Signature Edition adds wireless charging and 32GB storage, while the regular Paperwhite "
     "has a smaller screen and slower performance. Generally, the Signature Edition is the premium version.",
     "Generally, the Signature Edition is the premium version."),                     # the unsupported comparison is dropped
    ("According to this page, it has a 300 ppi display.", chat.DOESNT_SAY),           # a number the page doesn't have
    ("Generally, the regular Paperwhite has a 6.8 inch screen and lasts 10 weeks, while this one is faster.", chat.DOESNT_SAY),
    ("Generally, e-readers are easy on the eyes.", "Generally, e-readers are easy on the eyes."),
])
def test_only_page_backed_claims_survive(answer, kept):
    assert chat.supported(answer, PAGE) == kept


def test_chat_drops_unsupported_page_claims(monkeypatch):
    monkeypatch.setattr(meterless, "complete", lambda *a, **k: (
        "According to this page, the regular Paperwhite has a smaller screen and slower performance."))
    check = {"page": {"product": {"name": "Kindle", "price": 249.99}, "signals": [{"type": "page_text", "text": PAGE}]},
             "findings": [], "final_card": {}}
    assert chat.answer(check, "What's the difference vs the regular Paperwhite?").answer == chat.DOESNT_SAY


INTERSTITIAL = {"url": "https://www.amazon.com/dp/B0C8RR4WN3", "host": "www.amazon.com", "title": "Amazon.com",
                "product": {"name": "Amazon.com", "price": None}, "signals": [{"type": "store_info", "text": "x", "detail": {}}]}


@pytest.mark.parametrize("page, skip", [
    (INTERSTITIAL, True),
    ({**INTERSTITIAL, "product": {"name": "Amazon", "price": None}}, True),
    ({**INTERSTITIAL, "product": {"name": "Kindle Paperwhite", "price": None, "sku": "B0C8RR4WN3"}}, False),  # unpriced product
    ({**INTERSTITIAL, "product": {"name": "Amazon.com", "price": 249.99}}, False),
    ({"url": "https://blog.example/post", "host": "blog.example", "product": {"name": "How to choose headphones"}, "signals": []}, True),
])
def test_not_a_product_page_rule(page, skip):
    assert stub_server.not_a_product_page(page["url"], PageFacts(**page)) is skip


def test_store_lab_is_never_skipped():
    assert not stub_server.not_a_product_page("http://localhost:8787/storelab/audiodealz/", PageFacts(url="x"))


@pytest.mark.parametrize("orch", ["off", "on"])
def test_interstitial_ends_before_any_agent_search_or_model(monkeypatch, orch):
    from backend.agents import price, trust
    from backend.tools import shopping

    def boom(*a, **k):
        raise AssertionError("nothing may run for a non-product page")

    for k, v in {"AGENT_ORCH": orch, "AGENT_PRICE": "real", "AGENT_TRUST": "real", "AGENT_DEAL": "stub",
                 "SERPAPI_KEY": "test-key-not-real"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(shopping, "_search_serpapi", boom)
    monkeypatch.setattr(trust, "assess_store", boom)
    monkeypatch.setattr(meterless, "complete", boom)
    monkeypatch.setattr(price, "assess_async", boom)
    client = TestClient(stub_server.app)
    cid = client.post("/check", json={"url": INTERSTITIAL["url"], "page": INTERSTITIAL}).json()["check_id"]
    with client.stream("GET", f"/check/{cid}/events") as r:
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    assert [e["kind"] for e in events] == ["final_card"]
    card = events[0]["card"]
    assert card["title"] == "Not a product page" and card["alt"] == {"kind": "not_product"} and card["tricks"] == 0


def test_no_quick_card_for_a_non_product_page():
    bg = (ROOT / "extension" / "background.js").read_text()
    assert "c.card?.alt?.kind === 'not_product'" in bg and "pricise:quick-card-hide" in bg
    assert "pricise:quick-card-hide" in (ROOT / "extension" / "content" / "quick-card.js").read_text()
    content = (ROOT / "extension" / "content" / "content.js").read_text()
    assert "not a product page after all" in content  # the content script doesn't even ask for a check
