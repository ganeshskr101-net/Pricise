"""Trust Agent end to end on the committed Store Lab and bench/trust stores, served over real HTTP; plus the rules,
read-only behaviour on real pages, the vet API and AGENT_TRUST=real in the stub server."""
from __future__ import annotations

import asyncio
import csv
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import stub_server
from backend.agents import trust
from backend.agents.trust import assess_store, decide, safe_verdict
from backend.contracts import Finding, PageFacts
from backend.engines import world_model_stub
from backend.tests.content_reader import serve_repo
from backend.tools.trust_common import Signal

ROOT = Path(__file__).resolve().parents[2]


def vet(*args, **kw):
    return asyncio.run(assess_store(*args, **kw))


@pytest.fixture(scope="module")
def base():
    server, url = serve_repo()
    yield url
    server.shutdown()


@pytest.fixture(autouse=True)
def _fresh():
    trust.clear_cache()
    world_model_stub.clear()


# --- the three acceptance cases from the brief ----------------------------------------------------------------------
def test_store_d_is_unsafe(base):
    tv = vet(f"{base}/storelab/store-d/", "t1")
    assert tv["verdict"] == "unsafe" and tv["checked"]
    assert any("copied" in r for r in tv["reasons"])


def test_store_c_is_safe(base):
    tv = vet(f"{base}/storelab/store-c/", "t1")
    assert tv["verdict"] == "safe", tv["reasons"]  # the victim of copied photos is never flagged
    assert tv["findings"] == []  # passing checks don't stream as (red) panel rows
    assert safe_verdict(tv).status == "good" and safe_verdict(tv).headline == "Looks safe"


def test_audiodealz_is_caution_or_unsafe_with_the_demo_reasons(base):
    tv = vet(f"{base}/storelab/audiodealz/", "t1")
    assert tv["verdict"] in {"caution", "unsafe"}
    text = " ".join(tv["reasons"])
    assert "6 days ago" in text and "No address or company record" in text and "copied" in text


@pytest.mark.parametrize("row", [r for r in csv.DictReader(open(ROOT / "bench" / "labels.csv")) if r["page"].startswith("trust/")],
                         ids=lambda r: r["page"].split("/")[1])
def test_bench_store(base, row):
    tv = vet(f"{base}/bench/{row['page']}", "bench")
    assert int(tv["verdict"] != "safe") == int(row["expected"]), (row["note"], tv["reasons"])


# --- findings ------------------------------------------------------------------------------------------------------
def test_findings_follow_the_contract_and_reach_the_world_model(base):
    tv = vet(f"{base}/storelab/store-d/", "t2")
    assert tv["findings"] and all(isinstance(f, Finding) and f.agent == "trust" and f.evidence for f in tv["findings"])
    assert {f.status for f in tv["findings"]} <= {"proposed", "unverified"}
    assert len(world_model_stub.read_findings("t2")) == len(tv["findings"])


def test_rerun_does_not_duplicate_findings(base):
    vet(f"{base}/storelab/store-d/", "t3")
    vet(f"{base}/storelab/store-d/", "t3")  # cached path
    assert len(world_model_stub.read_findings("t3")) == len({f.type for f in world_model_stub.read_findings("t3")})


def test_store_that_does_not_answer_is_caution_not_a_crash():
    tv = vet("http://127.0.0.1:9/storelab/gone/", "t4")  # nothing listens on port 9
    assert tv["verdict"] == "caution" and not tv["checked"]
    assert tv["reasons"][0].startswith("Couldn't check")


def test_vet_is_fast_once_warm(base):
    vet(f"{base}/storelab/store-c/", "warm", use_cache=False)
    tv = vet(f"{base}/storelab/store-d/", "t5", use_cache=False)
    assert tv["elapsed_ms"] < 2000


# --- real store pages: read only -----------------------------------------------------------------------------------
def test_real_page_fetches_nothing(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the Trust Agent must not fetch anything from a real store")
    monkeypatch.setattr(httpx.AsyncClient, "get", boom)
    page = PageFacts(url="https://shop.example/item", host="shop.example", https=True)
    tv = vet(page.url, "t6", page)
    assert tv["verdict"] == "caution" and not tv["checked"]
    assert all(f.status == "unverified" for f in tv["findings"])
    assert safe_verdict(tv).status == "unknown"


def test_real_page_without_https_is_flagged():
    page = PageFacts(url="http://shop.example/item", host="shop.example", https=False)
    tv = vet(page.url, "t7", page)
    assert any(f.type == "no_https" and f.status == "proposed" for f in tv["findings"])
    assert safe_verdict(tv).status == "bad"


# --- the rules -----------------------------------------------------------------------------------------------------
def _s(strength, flagged=True):
    return Signal("x", strength, flagged, True, "r")


@pytest.mark.parametrize("signals,expected", [
    ([], "safe"),
    ([_s("weak")], "safe"),
    ([_s("weak"), _s("weak")], "caution"),
    ([_s("strong")], "caution"),
    ([_s("strong"), _s("strong")], "unsafe"),
    ([_s("strong"), _s("weak"), _s("weak")], "unsafe"),
    ([_s("strong", flagged=False), _s("weak")], "safe"),
])
def test_decide(signals, expected):
    assert decide(signals) == expected


def test_model_never_changes_the_verdict(base, monkeypatch):
    from backend.llm import meterless
    monkeypatch.setenv("TRUST_LLM_SUMMARY", "true")
    monkeypatch.setattr(meterless, "configured", lambda lane="fast": True)
    monkeypatch.setattr(meterless, "complete", lambda prompt, lane="fast", max_tokens=64: "Totally safe store!")
    tv = vet(f"{base}/storelab/store-d/", "t8")
    assert tv["verdict"] == "unsafe"  # wrong line count from the model: rule text kept
    assert any("copied" in r for r in tv["reasons"])


# --- the API -------------------------------------------------------------------------------------------------------
def test_vet_endpoint(base):
    client = TestClient(stub_server.app)
    r = client.post("/internal/trust/vet", json={"store_url": f"{base}/storelab/store-d/"})
    body = r.json()
    assert r.status_code == 200 and body["verdict"] == "unsafe" and body["findings"][0]["agent"] == "trust"


def test_agent_trust_real_replaces_the_scripted_safe_section(base, monkeypatch):
    monkeypatch.setenv("AGENT_TRUST", "real")
    monkeypatch.setattr(stub_server, "DEMO_DELAY", 0)
    client = TestClient(stub_server.app)
    assert client.get("/health").json()["agent_trust"] == "real"
    cid = client.post("/check", json={"url": f"{base}/storelab/audiodealz/"}).json()["check_id"]
    with client.stream("GET", f"/check/{cid}/events") as r:
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    trust_rows = [e["finding"] for e in events if e["kind"] == "finding" and e["finding"]["agent"] == "trust"]
    assert {f["model"] for f in trust_rows} == {"rules (no model)"}  # real agent, not the script
    card = events[-1]["card"]
    assert card["safe"]["status"] == "bad" and card["safe"]["headline"] == "Risky"
    assert card["tricks"] == 3 and card["best_price"]["total"] == 229  # the rest of the demo is unchanged
