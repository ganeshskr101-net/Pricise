"""Price Agent through the orchestrator (AGENT_ORCH=on, AGENT_PRICE=real) and backend/rechecker.py.

Fake Price + fake Trust (no network): look-alike rejected, honest cheaper store wins, unsafe winner falls back to the
runner-up or shows "no runner-up", Price outage -> unverified and the check continues, no evidence -> rejected.
Josh's real agent: the adapter adds the exact-match ref and the card shows Store C $229 (fixture data). Contract test
against price.assess_async skips if price.py is missing.
"""
import asyncio
import inspect
import json
import socket

import pytest
from fastapi.testclient import TestClient

from backend import orchestrator, rechecker, stub_server
from backend.contracts import BestPrice, Evidence, Finding, PageFacts

LAB = "http://localhost:8787/storelab/{}/"
PAGE_URL = LAB.format("store-g")
PAGE = {"url": PAGE_URL, "host": "localhost:8787", "product": {"name": "Sony WH-1000XM5", "price": 219.0, "sku": "KW-XM5-BK",
                                                               "model": "WH-1000XM5"}, "signals": []}


def unused_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(orchestrator, "_RUNS", {})
    monkeypatch.setattr(orchestrator, "TRACES_DIR", tmp_path)
    monkeypatch.setattr(stub_server, "DEMO_DELAY", 0)
    monkeypatch.setenv("WORLD_MODEL_URL", f"http://127.0.0.1:{unused_port()}")  # never write into a real sidecar
    monkeypatch.setenv("AGENT_ORCH", "on")
    monkeypatch.setenv("AGENT_PRICE", "real")


client = TestClient(stub_server.app)


def _events(url=PAGE_URL, page=PAGE):
    cid = client.post("/check", json={"url": url, **({"page": page} if page else {})}).json()["check_id"]
    with client.stream("GET", f"/check/{cid}/events") as r:
        return cid, [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]


def _price_rows(events, type_="best_price"):
    """Every version of each finding of that type, keyed by its summary."""
    out: dict[str, list[str]] = {}
    for e in events:
        if e["kind"] == "finding" and e["finding"]["agent"] == "price" and e["finding"]["type"] == type_:
            out.setdefault(e["finding"]["summary"], []).append(e["finding"]["status"])
    return out


def _fake_trust(monkeypatch, verdicts: dict):
    """Trust Agent answers by store id ({store_id: verdict}, default safe); calls are recorded."""
    from backend.agents import trust

    monkeypatch.setenv("AGENT_TRUST", "real")
    calls = []

    async def fake(store_url, check_id, page=None, **_kw):
        calls.append(check_id)
        v = verdicts.get(rechecker.store_id(store_url), "safe")
        return {"verdict": v, "reasons": [f"store is {v}"], "findings": [], "store": None, "store_url": store_url,
                "checked": v != "unchecked", "elapsed_ms": 1}

    monkeypatch.setattr(trust, "assess_store", fake)
    return calls


def bp(cid, name, store, total, model=True, condition=True, evidence=True, status="confirmed"):
    url = LAB.format(store)
    ev = [Evidence(kind="url", ref=url), Evidence(kind="price_row", ref=rechecker.exact_ref(url, model, condition, total))]
    return Finding(check_id=cid, agent="price", type="best_price", status=status, summary=f"{name}, ${total}: claimed best price.",
                   evidence=ev if evidence else [], model="fake price")


def _fake_price(monkeypatch, make=None, error=None):
    from backend.agents import price

    async def fake(check_id, url, page, day=1, use_trust=False, propose_lookalikes=False):
        if error is not None:
            raise error
        found = make(check_id)
        return price.PriceResult(findings=found, best_price=BestPrice(status="good", headline="claimed"), source="fake")

    monkeypatch.setattr(price, "assess_async", fake)


# ----------------------------------------------------------------------------- fake Price, fake Trust


def test_lookalike_rejected_and_honest_cheaper_store_wins(monkeypatch):
    calls = _fake_trust(monkeypatch, {})
    _fake_price(monkeypatch, lambda cid: [bp(cid, "Look-alike Shop", "store-x", 150, model=False),
                                          bp(cid, "Store A", "store-a", 252), bp(cid, "Store C", "store-c", 229)])
    cid, events = _events()
    rows = _price_rows(events)
    assert rows["Look-alike Shop, $150: claimed best price."][-1] == "rejected"
    assert rows["Store C, $229: claimed best price."][-1] == "confirmed"
    card = events[-1]["card"]
    assert (card["best_price"]["store"], card["best_price"]["total"]) == ("Store C", 229)
    assert card["alt"] == {"store": "Store C", "total": 229, "url": LAB.format("store-c")}
    assert f"{cid}:recheck:store-c" in calls and f"{cid}:recheck:store-x" in calls
    assert "not in data" in card["best_price"]["reasons"][0]["text"]  # never claims all four


def test_unsafe_winner_falls_back_to_the_runner_up(monkeypatch):
    _fake_trust(monkeypatch, {"store-d": "unsafe"})
    _fake_price(monkeypatch, lambda cid: [bp(cid, "Store D", "store-d", 189), bp(cid, "Store C", "store-c", 229)])
    _, events = _events()
    assert _price_rows(events)["Store D, $189: claimed best price."][-1] == "rejected"
    assert events[-1]["card"]["best_price"]["total"] == 229


def test_single_winner_rejected_shows_no_runner_up(monkeypatch):
    _fake_trust(monkeypatch, {"store-d": "unsafe"})
    _fake_price(monkeypatch, lambda cid: [bp(cid, "Store D", "store-d", 189)])
    _, events = _events()
    card = events[-1]["card"]
    assert card["best_price"]["headline"] == "No safe match" and card["alt"] is None
    texts = [r["text"] for r in card["best_price"]["reasons"]]
    assert texts[0] == "Store D, $189: not accepted by the Skeptic (store flagged unsafe)"
    assert texts[-1] == rechecker.NO_RUNNER_UP


def test_unchecked_store_is_not_accepted(monkeypatch):
    _fake_trust(monkeypatch, {"store-c": "unchecked"})
    _fake_price(monkeypatch, lambda cid: [bp(cid, "Store C", "store-c", 229)])
    _, events = _events()
    assert events[-1]["card"]["best_price"]["reasons"][0]["text"].endswith("(store safety not checked)")


def test_caution_store_passes_the_skeptic_rule(monkeypatch):
    """The rule only blocks unsafe. (The real Price Agent never proposes a caution store: see test_price.py.)"""
    _fake_trust(monkeypatch, {"store-c": "caution"})
    _fake_price(monkeypatch, lambda cid: [bp(cid, "Store C", "store-c", 229)])
    _, events = _events()
    assert events[-1]["card"]["best_price"]["total"] == 229


def test_no_evidence_finding_is_rejected_and_never_on_the_card(monkeypatch):
    _fake_trust(monkeypatch, {})
    _fake_price(monkeypatch, lambda cid: [bp(cid, "Store Z", "store-z", 99, evidence=False), bp(cid, "Store C", "store-c", 229)])
    _, events = _events()
    assert _price_rows(events)["Store Z, $99: claimed best price."] == ["confirmed", "challenged", "rejected"]
    assert events[-1]["card"]["best_price"]["total"] == 229


def test_price_outage_is_unverified_and_the_check_continues(monkeypatch):
    _fake_trust(monkeypatch, {})
    _fake_price(monkeypatch, error=RuntimeError("store down"))
    _, events = _events()
    failed = [e["finding"] for e in events if e["kind"] == "finding" and e["finding"]["type"] == "agent_failed"]
    assert [(f["agent"], f["status"]) for f in failed] == [("price", "unverified")] and "store down" not in failed[0]["summary"]
    assert any(e["kind"] == "finding" and e["finding"]["agent"] == "page" for e in events)
    assert events[-1]["kind"] == "final_card"


def test_couldnt_compare_finding_stays_unverified(monkeypatch):
    """Josh's "Couldn't compare" finding has no evidence and claims nothing: the Skeptic leaves it alone."""
    _fake_trust(monkeypatch, {})
    _fake_price(monkeypatch, lambda cid: [Finding(check_id=cid, agent="price", type="best_price", status="unverified",
                                                  summary="Couldn't compare other stores (RuntimeError).", confidence=0.0)])
    _, events = _events()
    assert _price_rows(events) == {"Couldn't compare other stores (RuntimeError).": ["unverified"]}


def test_price_stub_mode_keeps_the_scripted_rows_and_no_rechecker(monkeypatch):
    monkeypatch.setenv("AGENT_PRICE", "stub")
    _, events = _events(LAB.format("audiodealz"), None)
    skeptic_rows = [e["finding"]["status"] for e in events if e["kind"] == "finding" and e["finding"]["agent"] == "skeptic"]
    assert skeptic_rows == ["confirmed"]  # the scripted $229 row, not re-checked
    assert events[-1]["card"]["best_price"]["total"] == 229


# ----------------------------------------------------------------------------- Josh's real agent (fixture data)


def test_real_price_agent_gets_the_exact_match_ref_and_store_c_wins():
    _, events = _events(LAB.format("audiodealz"), None)
    best = [e["finding"] for e in events if e["kind"] == "finding" and e["finding"]["type"] == "best_price"]
    last = best[-1]
    refs = [e["ref"] for e in last["evidence"] if e["kind"] == "price_row"]
    assert refs[0].startswith("listing:store-c:") and refs[1] == f"store={LAB.format('store-c')};model=1;variant=1;condition=1;warranty=1;price=229"
    assert rechecker.ASSUMED in last["summary"] and (last["status"], last["challenged_by"], last["rounds"]) == ("confirmed", "skeptic", 1)
    card = events[-1]["card"]
    assert (card["best_price"]["store"], card["best_price"]["total"]) == ("Store C", 229) and card["alt"]["store"] == "Store C"


def test_adapter_writes_model_and_condition_flags_from_the_data():
    from backend.agents import price

    page = PageFacts(url=LAB.format("audiodealz"), product={"sku": "WH1000XM5/B"})
    res = price.assess("c1", page.url, page)
    winner = next(f for f in res.findings if f.type == "best_price")
    store_b = Finding(check_id="c1", agent="price", type="best_price", status="confirmed", summary="Store B, $199: x",
                      evidence=[Evidence(kind="url", ref=LAB.format("store-b")),
                                Evidence(kind="price_row", ref="listing:store-b:sony-wh1000xm5:day1")])
    res.findings = [winner, store_b]
    adapted = rechecker.adapt(res, page, page.url)
    assert rechecker.parse_ref(adapted[0])["match"] == {"model": True, "variant": True, "condition": True, "warranty": True}
    assert rechecker.parse_ref(adapted[1])["match"]["condition"] is False  # refurbished vs new, read from the data
    assert rechecker.parse_ref(adapted[1])["price"] == 199


def test_seeded_verdicts_when_trust_is_stub():
    assert [asyncio.run(rechecker.store_verdict(LAB.format(s), "c")) for s in ("store-c", "audiodealz", "store-d", "nowhere")] \
        == ["safe", "caution", "unsafe", None]


def test_ref_round_trip():
    f = bp("c", "Store H", "store-h", 254.99)
    assert rechecker.parse_ref(f) == {"store": LAB.format("store-h"), "price": 254.99, "ref": f.evidence[1].ref,
                                      "match": {"model": True, "variant": True, "condition": True, "warranty": True},
                                      "offer": {}}  # a Store Lab listing: no online-offer fields
    assert rechecker.parse_ref(f.model_copy(update={"evidence": f.evidence[:1]})) is None


# ----------------------------------------------------------------------------- contract with Josh's agent


def test_contract_with_the_real_price_agent():
    """What the wiring relies on. Skips if price.py is missing; fails loudly if its shape changes."""
    price = pytest.importorskip("backend.agents.price")
    assert inspect.iscoroutinefunction(price.assess_async)
    assert list(inspect.signature(price.assess_async).parameters) == ["check_id", "url", "page", "day", "use_trust",
                                                                      "propose_lookalikes"]
    page = PageFacts(url=LAB.format("audiodealz"), product={"sku": "WH1000XM5/B"})
    res = asyncio.run(price.assess_async("c1", page.url, page, 1))
    assert isinstance(res, price.PriceResult) and all(isinstance(f, Finding) for f in res.findings)
    best = [f for f in res.findings if f.type == "best_price"]
    assert len(best) == 1 and best[0].status == "confirmed"  # one winner only: no runner-up to fall back to
    kinds = {e.kind: e.ref for e in best[0].evidence}
    assert rechecker.LISTING_RE.fullmatch(kinds["price_row"]) and kinds["url"] == res.best_price.url
    assert res.alt == {"store": res.best_price.store, "total": res.best_price.total, "url": res.best_price.url}  # alt = winner
    from backend.data import repo

    assert res.source.startswith("fixture") and callable(price._identify) and callable(repo._catalog_listings)  # adapter
    # propose_lookalikes (the orchestrator): cheaper look-alikes become "proposed" best_price findings with the exact-match
    # ref, for the Skeptic; the winner is unchanged
    proposed = asyncio.run(price.assess_async("c1", page.url, page, 1, propose_lookalikes=True)).findings
    looks = [f for f in proposed if f.type == "best_price" and f.status == "proposed"]
    assert {f.summary.split(",")[0] for f in looks} == {"Store B", "Store I"}
    assert all(rechecker.parse_ref(f) for f in looks)
    assert [f.status for f in proposed if f.type == "best_price" and f.status != "proposed"] == ["confirmed"]
    real_site = asyncio.run(price.assess_async("c1", "https://shop.example/x", PageFacts(url="https://shop.example/x")))
    assert [f.status for f in real_site.findings] == ["unverified"]  # real sites: never compared, never raises
