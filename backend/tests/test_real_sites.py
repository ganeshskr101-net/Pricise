"""Real store pages with every agent real: Trust's real-site checks (HTTPS, domain age from public RDAP data, returns and
contact links from the page), no "not connected" stub text anywhere, /health shows every switch, run.sh defaults to
everything real, and the verdict's "Store risk" column has room. No network: RDAP answers come from a mocked transport."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import stub_server
from backend.agents import trust
from backend.agents.trust import assess_store, safe_verdict
from backend.contracts import PageFacts
from backend.tools import rdap

ROOT = Path(__file__).resolve().parents[2]
URL = "https://www.amazon.com/Acme-AAA-Batteries/dp/B0TESTAAA1"
INFO = {"type": "store_info", "text": "returns link: yes · contact link: yes", "detail": {"returns": True, "contact": True}}


def _page(https=True, info=INFO):
    return PageFacts(url=URL if https else URL.replace("https", "http"), host="www.amazon.com", https=https,
                     product={"name": "Acme AAA Batteries, 32 Pack", "price": 15.99, "was_price": 19.99},
                     signals=[info] if info else [])


@pytest.fixture
def registry(monkeypatch):
    """RDAP on: answers {domain: "YYYY-MM-DD" | None} from a mocked transport; records the domains asked."""
    asked, answers = [], {}

    def handler(req: httpx.Request) -> httpx.Response:
        domain = req.url.path.rsplit("/", 1)[-1]
        asked.append(domain)
        if answers.get(domain) is None:
            return httpx.Response(404)
        return httpx.Response(200, json={"events": [{"eventAction": "registration", "eventDate": f"{answers[domain]}T04:00:00Z"},
                                                    {"eventAction": "expiration", "eventDate": "2030-01-01T00:00:00Z"}]})

    monkeypatch.setenv("TRUST_RDAP", "on")
    monkeypatch.setattr(trust, "make_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    rdap.clear_cache()
    trust.clear_cache()
    yield answers, asked
    rdap.clear_cache()


def _vet(page):
    return asyncio.run(assess_store(page.url, "r1", page))


@pytest.mark.parametrize("host, domain", [("www.amazon.com", "amazon.com"), ("shop.example.co.uk", "example.co.uk"),
                                          ("bestbuy.com", "bestbuy.com"), ("a.b.target.com:443", "target.com")])
def test_registrable_domain(host, domain):
    assert rdap.registrable(host) == domain


def test_old_domain_with_links_is_low_risk(registry):
    answers, asked = registry
    answers["amazon.com"] = "1994-11-01"
    tv = _vet(_page())
    assert asked == ["amazon.com"] and tv["checked"] and tv["verdict"] == "safe"
    v = safe_verdict(tv)
    assert (v.status, v.headline) == ("good", "Looks safe")
    texts = {r.text: r.tone for r in v.reasons}
    assert texts["Secure connection (HTTPS)"] == "good" and texts["Returns policy link on the page"] == "good"
    assert any(t.startswith("Domain registered 3") and tone == "good" for t, tone in texts.items())  # 30+ years
    assert any("aren't checked" in t and tone == "unknown" for t, tone in texts.items())  # never claims more than it checked


def test_new_domain_without_links_is_high_risk(registry):
    answers, _ = registry
    from datetime import date, timedelta
    answers["amazon.com"] = (date.today() - timedelta(days=5)).isoformat()
    tv = _vet(_page(info={**INFO, "detail": {"returns": False, "contact": False}}))
    assert tv["verdict"] == "unsafe" and safe_verdict(tv).headline == "Risky"   # 1 strong + 2 weak warning signs
    assert {f.type for f in tv["findings"] if f.status == "proposed"} == {"domain_age", "bad_returns", "no_contact"}


def test_registry_unreachable_is_unknown_not_safe(registry):
    tv = _vet(_page())  # no answer for amazon.com
    assert not tv["checked"] and tv["verdict"] == "caution"
    v = safe_verdict(tv)
    assert (v.status, v.headline) == ("unknown", "Unknown")
    assert any("Couldn't look up when amazon.com was registered" in r.text for r in v.reasons)


def test_rdap_off_never_touches_the_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no network")
    monkeypatch.setattr(httpx.AsyncClient, "get", boom)
    trust.clear_cache()
    tv = _vet(_page())
    assert not tv["checked"] and safe_verdict(tv).headline == "Unknown"


# ----------------------------------------------------------------------------- the backend with every agent real

STUB_TEXT = ("not connected", "Not fully checked", "Search not connected")


@pytest.fixture
def all_real(monkeypatch, registry):
    answers, _ = registry
    answers["amazon.com"] = "1994-11-01"
    for k in ("AGENT_TRUST", "AGENT_PRICE"):
        monkeypatch.setenv(k, "real")
    monkeypatch.setattr(stub_server, "DEMO_DELAY", 0)
    return TestClient(stub_server.app)


def test_real_page_with_real_agents_never_shows_stub_text(all_real):
    page = _page().model_dump()
    cid = all_real.post("/check", json={"url": URL, "page": page}).json()["check_id"]
    with all_real.stream("GET", f"/check/{cid}/events") as r:
        body = "".join(r.iter_text())
    for text in STUB_TEXT:
        assert text not in body
    card = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")][-1]["card"]
    assert card["safe"]["headline"] == "Looks safe" and card["best_price"]["headline"] == "Compare"


def test_real_card_placeholders_follow_the_switches(monkeypatch):
    page = _page()
    monkeypatch.setenv("AGENT_TRUST", "real")
    monkeypatch.setenv("AGENT_PRICE", "real")
    card = stub_server.real_card("c1", page, stub_server.real_findings("c1", page), 1.0)
    dumped = json.dumps(card.model_dump())
    assert not any(t in dumped for t in STUB_TEXT)
    assert card.safe.headline == "Unknown" and card.best_price.headline == "Compare"
    monkeypatch.setenv("AGENT_TRUST", "stub")
    monkeypatch.setenv("AGENT_PRICE", "stub")
    card = stub_server.real_card("c1", page, stub_server.real_findings("c1", page), 1.0)
    assert card.safe.headline == "Not fully checked" and card.best_price.headline == "Search not connected"  # stub demo


def test_health_reports_every_switch(monkeypatch):
    monkeypatch.setenv("AGENT_ORCH", "on")
    h = TestClient(stub_server.app).get("/health").json()
    for k in ("agent_deal", "agent_trust", "agent_price", "agent_orch", "data_source", "trust_rdap", "nemotron"):
        assert k in h
    assert h["agent_orch"] == "on" and h["trust_rdap"] == "off" and set(h["nemotron"]) == {"fast", "deep"}


def test_run_sh_defaults_to_everything_real_and_prints_the_switches():
    text = (ROOT / "scripts" / "run.sh").read_text()
    for default in ('AGENT_DEAL="${AGENT_DEAL:-real}"', 'AGENT_TRUST="${AGENT_TRUST:-real}"', 'AGENT_PRICE="${AGENT_PRICE:-real}"',
                    'AGENT_ORCH="${AGENT_ORCH:-on}"', 'DATA_SOURCE="${DATA_SOURCE:-zetaris}"'):
        assert default in text
    assert "AGENT_DEAL=stub AGENT_TRUST=stub AGENT_PRICE=stub AGENT_ORCH=off DATA_SOURCE=fixture" in text  # --stub
    assert 'echo "  AGENT_DEAL=$AGENT_DEAL AGENT_TRUST=$AGENT_TRUST' in text


def test_store_risk_column_has_room():
    css = (ROOT / "extension" / "sidepanel" / "panel.css").read_text()
    assert "grid-template-columns: minmax(0, 1.3fr) minmax(0, .7fr) minmax(0, 1.1fr)" in css
    assert ".stat .v.unknown { color: var(--ink-3); font-size: 13px;" in css
    assert "['Unknown', 'unknown']" in (ROOT / "extension" / "shared" / "check.js").read_text()
