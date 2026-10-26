"""Price Agent: true totals, exact-match rules, the demo numbers (Store C $229, Day 9 $209), real-site wording, the
AGENT_PRICE switch in the backend, and live Zetaris checks that skip themselves when Zetaris or pricise_pg isn't there."""
import json

import pytest
from fastapi.testclient import TestClient

from backend import stub_server
from backend.agents import price
from backend.contracts import PageFacts
from backend.data import repo, zetaris
from backend.data.zetaris import ZetarisError

LAB = "http://localhost:8787/storelab/{}/"


def _assess(store="audiodealz", sku="WH1000XM5/B", day=1, model="WH-1000XM5"):
    url = LAB.format(store)
    return price.assess("c1", url, PageFacts(url=url, host="localhost:8787", product={"sku": sku, "model": model}), day)


def _by_store(res):
    return {s["store"]: s for s in res.stores}


def test_audiodealz_best_safe_price_is_store_c_229():
    res = _assess()
    assert (res.best_price.status, res.best_price.store, res.best_price.total) == ("good", "Store C", 229)
    assert res.alt == {"store": "Store C", "total": 229, "url": LAB.format("store-c")}
    assert "$255 minus a $26 card offer" in res.best_price.reasons[1].text
    stores = _by_store(res)
    assert stores["Store A"]["total"] == 252 and stores["Store G"]["total"] == 248  # $219 + $29 fees
    assert stores["Store H"]["total"] == 254.99 and stores["Store C"]["status"] == "chosen"
    assert all(f.agent == "price" for f in res.findings)


def test_day_9_store_c_drops_to_209():
    res = _assess(day=9)
    assert (res.best_price.store, res.best_price.total) == ("Store C", 209)
    assert "$235 minus a $26 card offer" in res.best_price.reasons[1].text


def test_refurbished_rejected_scam_skipped_variant_rejected_outage_unverified():
    res = _assess()
    text = {f.summary: f.status for f in res.findings if f.type == "candidate"}
    assert text["Store B, $199: refurbished, not the same item."] == "rejected"
    assert text["Store D, $189: flagged as a scam in our store data."] == "rejected"
    assert text["Store I, $178: WH-1000XM4 is a different model, not the same item."] == "rejected"
    assert text["Store E, $245: site down during the check."] == "unverified"
    assert "Skipped Store D (scam), Store B (refurbished) and Store E (site down)" == res.best_price.reasons[2].text
    assert _by_store(res)["Store B"]["status"] == "rejected" and _by_store(res)["Store E"]["status"] == "skipped"


def test_risky_store_never_wins_even_when_cheapest():
    res = _assess("store-g", "KW-XM5-BK")  # AudioDealz is $186.01 before its add-on, but it's not a trusted store
    assert res.best_price.store == "Store C" and _by_store(res)["AudioDealz"]["status"] == "rejected"


def test_store_c_page_is_already_the_best():
    res = _assess("store-c", "SC-WH1000XM5")
    assert res.best_price.store == "Store C" and res.alt is None
    assert any(f.summary.startswith("This store already has the best safe price") for f in res.findings)


def test_refurbished_page_compares_refurbished_only():
    res = _assess("store-b", "SSR-XM5-RFB")
    assert res.best_price.store == "Store B" and res.best_price.reasons[0].text == "Exact match: same model, refurbished"


def test_real_site_gets_one_careful_unverified_finding():
    url = "https://shop.example/headphones"
    res = price.assess("c1", url, PageFacts(url=url, host="shop.example", product={"name": "Headphones", "price": 199.0}))
    [f] = res.findings
    assert (f.agent, f.status, f.summary) == ("price", "unverified",
                                              "Other-store comparison covers our demo catalog only, so other stores weren't checked for this page.")
    assert res.best_price.status == "unknown" and res.alt is None


def test_item_outside_the_catalog_is_unverified():
    res = _assess("generic-shop", "XYZ-1", model="Toaster 9000")
    assert res.findings[0].status == "unverified" and res.best_price.status == "unknown"


def test_zetaris_mode_uses_the_sql_and_cites_it(monkeypatch):
    rows, _, _ = repo.listings(1)
    strings = [{k: (f"{v:.2f}" if isinstance(v, float) else v) for k, v in r.items()} for r in rows]
    monkeypatch.setenv("DATA_SOURCE", "zetaris")
    seen = []
    monkeypatch.setattr(zetaris, "run_sql", lambda sql, limit=1000: seen.append(sql) or strings)
    res = _assess()
    assert res.best_price.total == 229 and res.source == "zetaris"
    best = next(f for f in res.findings if f.type == "best_price")
    [sql_ev] = [e for e in best.evidence if e.kind == "sql"]
    assert sql_ev.ref == seen[0] and "pricise_pg.listings" in sql_ev.ref and "true_total" in sql_ev.ref


def test_zetaris_down_falls_back_to_catalog(monkeypatch):
    monkeypatch.setenv("DATA_SOURCE", "zetaris")

    def boom(sql, limit=1000):
        raise ZetarisError("Cannot reach Zetaris")

    monkeypatch.setattr(zetaris, "run_sql", boom)
    res = _assess()
    assert res.best_price.total == 229 and res.source.startswith("fixture")


def test_best_for_wishlist():
    assert price.best_for("WH-1000XM5", day=1)["true_total"] == 229
    assert price.best_for("WH-1000XM5", day=9)["true_total"] == 209


# ----------------------------------------------------------------------------- backend switch

stub_server.DEMO_DELAY = 0
client = TestClient(stub_server.app)


def _events(cid):
    with client.stream("GET", f"/check/{cid}/events") as r:
        return [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]


def _check(url, page=None):
    cid = client.post("/check", json={"url": url, **({"page": page} if page else {})}).json()["check_id"]
    return cid, _events(cid)


@pytest.fixture
def real_price(monkeypatch):
    monkeypatch.setenv("AGENT_PRICE", "real")
    yield
    client.post("/demo/clock", json={"day": 1})


def test_backend_demo_uses_the_price_agent(real_price):
    cid, events = _check(LAB.format("audiodealz"))
    findings = [e["finding"] for e in events if e["kind"] == "finding"]
    card = events[-1]["card"]
    assert card["best_price"]["total"] == 229 and card["alt"]["store"] == "Store C" and card["tricks"] == 3
    assert not any(f["agent"] == "skeptic" and f["type"] == "best_price" for f in findings)  # scripted $229 replaced
    assert any(f["agent"] == "price" and f["type"] == "best_price" and f["model"] == "rules (no model)" for f in findings)
    assert client.get(f"/check/{cid}").json()["evidence_data"]["stores"][0]["store"] == "Store C"


def test_backend_demo_clock_day_9(real_price):
    r = client.post("/demo/clock", json={"day": 9}).json()
    assert r["alerts"] and "$209 at Store C" in r["alerts"][0]["title"]
    _, events = _check(LAB.format("audiodealz"))
    assert events[-1]["card"]["best_price"]["total"] == 209


def test_backend_real_page_says_demo_catalog_only(real_price):
    page = {"url": "https://shop.example/x", "host": "shop.example", "product": {"name": "Ticket", "price": 89.0}, "signals": []}
    _, events = _check(page["url"], page)
    price_rows = [e["finding"] for e in events if e["kind"] == "finding" and e["finding"]["agent"] == "price"]
    assert [f["status"] for f in price_rows] == ["unverified"] and "demo catalog only" in price_rows[0]["summary"]
    assert events[-1]["card"]["best_price"]["headline"] == "Demo catalog only"


def test_backend_stub_mode_keeps_the_scripted_demo():
    _, events = _check(LAB.format("audiodealz"))
    assert any(e["kind"] == "finding" and e["finding"]["agent"] == "skeptic" for e in events)
    assert events[-1]["card"]["best_price"]["total"] == 229


# ----------------------------------------------------------------------------- live Zetaris (skips when not available)


def _live():
    if not zetaris.configured():
        pytest.skip("Zetaris not configured")
    try:
        zetaris.run_sql(f"SELECT 1 FROM {zetaris.datasource()}.stores LIMIT 1")
    except ZetarisError as e:
        pytest.skip(f"Zetaris or the {zetaris.datasource()} datasource isn't available: {e}")


def test_live_zetaris_price_math(monkeypatch):
    _live()
    monkeypatch.setenv("DATA_SOURCE", "zetaris")
    res = _assess()
    assert res.source == "zetaris" and (res.best_price.store, res.best_price.total) == ("Store C", 229)
    assert _assess(day=9).best_price.total == 209


def test_live_zetaris_history_and_writes_land_where_zetaris_reads(monkeypatch):
    _live()
    from backend.contracts import Finding
    from backend.data.history import get_price_history
    from backend.data.pg import PgError

    monkeypatch.setenv("DATA_SOURCE", "zetaris")
    rows = get_price_history("storelab/audiodealz", "WH1000XM5/B")
    assert rows.source == "zetaris" and [p for _, p in rows].count(249) == 2
    cid = "pytest-live-check"
    f = Finding(check_id=cid, agent="price", type="best_price", status="confirmed", summary="live test row")
    try:
        repo.save_findings(cid, "http://localhost/test", [f])
    except PgError as e:
        pytest.skip(str(e))
    try:
        assert repo.count_findings(cid) >= 1
    finally:
        from backend.data import pg
        with pg.connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM findings WHERE check_id = %s", [cid])
