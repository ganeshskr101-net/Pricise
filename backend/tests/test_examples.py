"""The four submission examples: each input replays its own recorded run in sample mode (no agent, search or model call),
and example 4, Store Lab's AudioDealz, is labelled everywhere as a synthetic test store made up by us."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import recordings, stub_server

ROOT = Path(__file__).resolve().parents[2]


def _run(client, data):
    cid = client.post("/check?fresh=1", json={"url": data["url"], "page": data["page"]}).json()["check_id"]
    with client.stream("GET", f"/check/{cid}/events") as r:
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    return [e["card"] for e in events if e["kind"] == "final_card"][-1], [e for e in events if e["kind"] == "finding"]


@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_every_example_replays_its_recording_in_sample_mode(n, monkeypatch):
    monkeypatch.setenv("SAMPLE_MODE", "true")
    data = json.loads((ROOT / "input_examples" / f"example_{n}.json").read_text())
    rec = recordings.match(data["url"])
    assert rec is not None, f"example {n} has no recording in samples/"
    expected = json.loads((ROOT / "output_examples" / f"example_{n}_result.json").read_text())
    assert (ROOT / "output_examples" / f"example_{n}_trace.jsonl").exists()
    card, findings = _run(TestClient(stub_server.app), data)
    assert card["title"] == expected["final_card"]["title"]
    assert card["best_price"]["headline"] == expected["final_card"]["best_price"]["headline"]
    assert len(findings) == len(expected["findings"])
    assert card["subtitle"].endswith(recordings.label(rec))


def test_example_4_is_the_synthetic_store_with_the_red_verdict():
    data = json.loads((ROOT / "input_examples" / "example_4.json").read_text())
    out = json.loads((ROOT / "output_examples" / "example_4_result.json").read_text())
    assert "/storelab/audiodealz" in data["url"]
    for d in (data, out):
        assert d["_note"].startswith("SYNTHETIC TEST STORE, made up by us")
    card = out["final_card"]
    assert card["title"] == "Don't buy here" and "3 tricks" in card["subtitle"] and "real total $191" in card["subtitle"]
    # recorded with the real agents, not the scripted demo: the hidden browser and the Skeptic's model are in it
    models = {f.get("model") for f in out["findings"]}
    assert "hidden browser run-ahead + rules" in models and not any("stub" in (m or "") for m in models)
    rec = recordings.match(data["url"])
    assert recordings.label(rec).startswith("recorded from the real agents on a synthetic test store")
    assert "Synthetic test store (made up by us): shows the red verdict" in (ROOT / "README.md").read_text()
