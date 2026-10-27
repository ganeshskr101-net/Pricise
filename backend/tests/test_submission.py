"""Submission pieces: the agent trace (one JSONL line per step, challenger -> target, Zetaris table, nothing secret),
SAMPLE_MODE (no external calls), and run.py. No live calls."""
import json
import os

import pytest
from fastapi.testclient import TestClient

from backend import agent_trace, stub_server
from backend.contracts import Evidence, Finding


@pytest.fixture
def logs(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_trace, "LOGS_DIR", tmp_path)
    return tmp_path


def _rows(logs, cid):
    return [json.loads(line) for line in (logs / f"{cid}.jsonl").read_text().splitlines()]


def test_trace_line_shape_challenger_and_zetaris_table(logs):
    chk = {"check_id": "t1", "url": "http://x", "page": {"host": "x", "product": {"name": "Sony WH-1000XM5"}}}
    f = Finding(check_id="t1", agent="price", type="best_price", status="rejected", challenged_by="skeptic", rounds=1,
                summary="Store B, $199: refurbished. token=abc123 Bearer sk-secret", confidence=0.9,
                model="nvidia/nemotron-3-ultra-550b-a55b",
                evidence=[Evidence(kind="sql", ref="SELECT * FROM pricise_pg.listings l JOIN pricise_pg.stores s ON 1=1")])
    agent_trace.finding(chk, f)
    [row] = _rows(logs, "t1")
    assert list(row) == ["timestamp", "trace_id", "agent", "action", "input_summary", "output_summary", "target_agent",
                         "confidence", "retry_count", "status", "model", "zetaris_table"]
    assert (row["agent"], row["action"], row["target_agent"], row["retry_count"]) == ("skeptic", "reject", "price", 1)
    assert row["model"] == "nvidia/nemotron-3-ultra-550b-a55b" and row["zetaris_table"] == ["listings", "stores"]
    assert "abc123" not in json.dumps(row) and "sk-secret" not in json.dumps(row)


def test_audiodealz_trace_shows_skeptic_and_trust_rejections(logs, monkeypatch):
    monkeypatch.setattr(stub_server, "DEMO_DELAY", 0)
    client = TestClient(stub_server.app)
    cid = client.post("/check", json={"url": "http://localhost:8787/storelab/audiodealz/"}).json()["check_id"]
    with client.stream("GET", f"/check/{cid}/events") as r:
        list(r.iter_lines())
    rows = _rows(logs, cid)
    assert any(r["agent"] == "skeptic" and r["action"] == "reject" and r["target_agent"] == "price"
               and r["output_summary"].startswith("Store B") for r in rows)
    assert any(r["agent"] == "trust" and r["action"] == "reject" and r["target_agent"] == "price"
               and r["output_summary"].startswith("Store D") for r in rows)
    assert rows[-1]["agent"] == "orchestrator" and rows[-1]["action"] == "verdict"


def test_sample_mode_forces_stub_and_no_external_calls(monkeypatch):
    from backend.env import SAMPLE_BLANKED, SAMPLE_OVERRIDES, apply_sample_mode
    from backend.llm import meterless
    from backend.tools import shopping

    for k in ("AGENT_DEAL", "AGENT_TRUST", "AGENT_PRICE", "AGENT_ORCH", "DATA_SOURCE", "TRUST_RDAP", *SAMPLE_BLANKED):
        monkeypatch.setenv(k, "real" if k.startswith("AGENT") else "on" if k == "TRUST_RDAP" else "zetaris" if k == "DATA_SOURCE" else "set")
    monkeypatch.setenv("SAMPLE_MODE", "true")
    assert apply_sample_mode() is True
    assert all(os.environ[k] == v for k, v in SAMPLE_OVERRIDES.items())
    assert not meterless.configured("fast") and not meterless.configured("deep") and not shopping.configured()
    assert TestClient(stub_server.app).get("/health").json()["mode"] == "sample"
    monkeypatch.setenv("SAMPLE_MODE", "false")
    assert apply_sample_mode() is False


def test_run_py_prints_the_verdict_and_trace(monkeypatch, capsys, logs):
    import run

    monkeypatch.setattr(stub_server, "DEMO_DELAY", 0)
    client = TestClient(stub_server.app)
    monkeypatch.setattr(run, "backend_up", lambda base: client.get("/health").json())
    monkeypatch.setattr(run, "_post", lambda url, body, timeout=30: client.post(url.split("8787", 1)[1], json=body).json())

    def events(base, url, page):
        cid = client.post("/check", json={"url": url, "page": page}).json()["check_id"]
        with client.stream("GET", f"/check/{cid}/events") as r:
            evs = [json.loads(l[6:]) for l in r.iter_lines() if l.startswith("data: ")]
        return cid, evs[-1]["card"], [e["finding"] for e in evs if e["kind"] == "finding"]

    monkeypatch.setattr(run, "run_check", events)
    assert run.main(["--input", "input_examples/example_2.json"]) == 0  # the saved Kindle page (Amazon)
    out = capsys.readouterr().out
    assert "VERDICT  " in out and "Agent trace:" in out and ".jsonl" in out
