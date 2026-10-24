"""Orchestrator: fake agents, fake rechecker, fake card builder, tmp traces. No real network, browser or model."""
import asyncio
import json
import socket

import pytest
from fastapi.testclient import TestClient

from backend import orchestrator
from backend.contracts import BestPrice, Evidence, FinalCard, Finding, PageFacts, PageSignal, Verdict

EV = [Evidence(kind="screenshot", ref="/traces/x/visit1.png")]
RESETS = {"visits": [{"fresh_session": True, "reset": True}] * 3}
TWO_VISITS = {"visits": [{"fresh_session": True, "reset": True}] * 2}


def unused_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(orchestrator, "_RUNS", {})
    monkeypatch.setattr(orchestrator, "TRACES_DIR", tmp_path)
    monkeypatch.delenv("AGENT_ORCH", raising=False)  # a .env value must not change these tests; the hook test sets it
    monkeypatch.setenv("WORLD_MODEL_URL", f"http://127.0.0.1:{unused_port()}")  # never write into a real sidecar


def make(type_="product", status="confirmed", agent="page", evidence=None, **kw):
    return Finding(check_id="c1", agent=agent, type=type_, status=status, summary=f"{type_} {status}",
                   evidence=EV if evidence is None else evidence, **kw)


def agent(*steps, name=None):
    """An agent that yields each (delay, Finding) step; a step that is an exception is raised instead."""
    async def gen(_chk):
        for delay, item in steps:
            await asyncio.sleep(delay)
            if isinstance(item, BaseException):
                raise item
            yield item

    if name:
        gen.agent_name = name
    return gen


def card(_chk, findings):
    v = Verdict(status="unknown", headline="")
    return FinalCard(check_id="c1", title=",".join(f.status for f in findings), subtitle="", tone="unknown",
                     safe=v, honest=v, best_price=BestPrice(status="unknown", headline=""))


def chk(cid="c1"):
    return {"check_id": cid, "url": "http://localhost/storelab/x/"}


async def collect(c, agents, rechecker=None, **kw):
    kw.setdefault("build_card", card)
    return [e async for e in orchestrator.run_check(c, agents, rechecker, **kw)]


def run(c, agents, rechecker=None, **kw):
    return asyncio.run(collect(c, agents, rechecker, **kw))


def findings(events):
    return [e["finding"] for e in events if e["kind"] == "finding"]


def statuses(events, type_):
    return [f.status for f in findings(events) if f.type == type_]


def recheck(*answers):
    calls = []

    async def fn(f):
        calls.append(f.type)
        a = answers[min(len(calls), len(answers)) - 1]
        if isinstance(a, BaseException):
            raise a
        return a

    fn.calls = calls
    return fn


# streaming and cards

def test_findings_stream_in_arrival_order_and_card_after_each_agent():
    a = agent((0, make("a1")), (0.06, make("a2")))
    b = agent((0.03, make("b1")))
    events = run(chk(), [a, b])
    kinds = [e["kind"] if e["kind"] == "final_card" else e["finding"].type for e in events]
    assert kinds == ["a1", "b1", "final_card", "a2", "final_card"]


def test_card_gets_latest_findings_and_chk_is_updated():
    c = chk()
    events = run(c, [agent((0, make("resetting_timer", "proposed")))], recheck(RESETS))
    assert events[-1]["kind"] == "final_card" and events[-1]["card"].title == "confirmed"
    assert [d["status"] for d in c["findings"]] == ["proposed", "challenged", "confirmed"]
    assert c["final_card"]["title"] == "confirmed"


def test_no_agents_still_ends_with_card():
    events = run(chk(), [])
    assert [e["kind"] for e in events] == ["final_card"]


# failing agents

def test_failing_agent_becomes_unverified_and_others_arrive():
    bad = agent((0, make("x1", agent="deal")), (0.01, RuntimeError("secret-key-in-url")), name="deal")
    good = agent((0.03, make("t1", agent="trust")))
    fs = findings(run(chk(), [bad, good]))
    assert [f.type for f in fs] == ["x1", "agent_failed", "t1"]
    failed = fs[1]
    assert (failed.status, failed.agent) == ("unverified", "deal")
    assert "RuntimeError" in failed.summary and "secret" not in failed.summary
    assert "the rest of the check continues" in failed.summary


def test_slow_agent_times_out_and_unknown_name_becomes_page():
    slow = agent((5, make("never")), name="fancy")
    events = run(chk(), [slow, agent((0, make("p1")))], agent_timeout_s=0.05)
    failed = next(f for f in findings(events) if f.type == "agent_failed")
    assert failed.agent == "page" and "fancy" in failed.summary and "longer than 0.05 s" in failed.summary
    assert events[-1]["kind"] == "final_card"


# Skeptic

def test_challenged_then_confirmed():
    rc = recheck(RESETS)
    events = run(chk(), [agent((0, make("fake_timer", "proposed", agent="deal")))], rc)
    assert statuses(events, "fake_timer") == ["proposed", "challenged", "confirmed"]
    last = findings(events)[-1]
    assert (last.challenged_by, last.rounds) == ("skeptic", 1) and rc.calls == ["fake_timer"]


def test_challenged_then_rejected():
    inputs = {"match": {"model": True, "variant": True, "condition": False, "warranty": True}, "trust_verdict": "safe"}
    events = run(chk(), [agent((0, make("best_price", "proposed", agent="price")))], recheck(inputs))
    assert statuses(events, "best_price") == ["proposed", "challenged", "rejected"]


def test_second_round_only_when_unverified_and_cap_holds():
    rc = recheck(TWO_VISITS)
    events = run(chk(), [agent((0, make("resetting_timer", "proposed")))], rc)
    assert statuses(events, "resetting_timer") == ["proposed", "challenged", "unverified", "challenged", "unverified"]
    assert findings(events)[-1].rounds == 2 and len(rc.calls) == 2


def test_unverified_then_confirmed_in_round_two():
    events = run(chk(), [agent((0, make("resetting_timer", "proposed")))], recheck(TWO_VISITS, RESETS))
    assert statuses(events, "resetting_timer")[-1] == "confirmed"


def test_finding_at_round_two_goes_unverified_without_looping():
    events = run(chk(), [agent((0, make("resetting_timer", "proposed", rounds=2)))], recheck(RESETS))
    assert statuses(events, "resetting_timer") == ["proposed", "challenged", "unverified"]
    assert findings(events)[-1].rounds == 2


def test_no_rechecker_keeps_status():
    events = run(chk(), [agent((0, make("fake_discount", "confirmed")))])
    assert statuses(events, "fake_discount") == ["confirmed"]


@pytest.mark.parametrize("answer", [None, {}, {"visits_typo": []}, RuntimeError("down")])
def test_missing_inputs_restore_status(answer):
    events = run(chk(), [agent((0, make("resetting_timer", "confirmed")))], recheck(answer))
    assert statuses(events, "resetting_timer") == ["confirmed", "challenged", "confirmed"]
    assert findings(events)[-1].rounds == 0


def test_missing_evidence_rejected_even_without_rechecker():
    events = run(chk(), [agent((0, make("fake_discount", "proposed", evidence=[])))])
    assert statuses(events, "fake_discount") == ["proposed", "challenged", "rejected"]


def test_already_decided_by_skeptic_and_other_types_skipped():
    rc = recheck(RESETS)
    done = make("best_price", "confirmed", agent="skeptic", challenged_by="skeptic", rounds=2)
    events = run(chk(), [agent((0, done), (0, make("drip_fees", "confirmed")))], rc)
    assert [f.status for f in findings(events)] == ["confirmed", "confirmed"] and rc.calls == []


# run once, replay

def test_second_stream_replays_without_rerunning():
    calls = []

    def counted(_chk):
        calls.append(1)
        return agent((0.02, make("a1")))(_chk)

    async def both():
        c = chk()
        first, concurrent = await asyncio.gather(collect(c, [counted]), collect(c, [counted]))
        later = await collect(c, [counted])
        return first, concurrent, later

    first, concurrent, later = asyncio.run(both())
    assert len(calls) == 1
    assert [e["kind"] for e in first] == ["finding", "final_card"]
    assert concurrent == first and later == first


# trace

def test_trace_file_written(tmp_path):
    a = agent((0, make("fake_timer", "proposed", agent="deal", model="hidden browser")))
    run(chk("abc123"), [a, agent((0, RuntimeError("x")), name="trust")], recheck(RESETS))
    trace = json.loads((tmp_path / "abc123.json").read_text(encoding="utf-8"))
    rows = {r["type"]: r for r in trace["findings"]}
    timer = rows["fake_timer"]
    assert (timer["proposed_by"], timer["challenged_by"], timer["model"]) == ("deal", "skeptic", "hidden browser")
    assert (timer["rounds"], timer["final_status"]) == (1, "confirmed")
    assert timer["statuses"] == ["proposed", "challenged", "confirmed"]
    assert timer["evidence"][0]["ref"] == "/traces/x/visit1.png"
    assert rows["agent_failed"]["final_status"] == "unverified"


# stub_server hook

def _sse(client, cid):
    with client.stream("GET", f"/check/{cid}/events") as r:
        return [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]


def test_stub_hook_streams_through_orchestrator_and_replays(monkeypatch, tmp_path):
    from backend import stub_server

    monkeypatch.setenv("AGENT_ORCH", "on")
    client = TestClient(stub_server.app)
    page = PageFacts(url="https://shop.example/item", host="shop.example",
                     signals=[PageSignal(type="prechecked_addon", selector="#a", text="Protection $4.99", amount=4.99)])
    cid = client.post("/check", json={"url": page.url, "page": page.model_dump()}).json()["check_id"]
    events = _sse(client, cid)
    assert events[-1]["kind"] == "final_card" and events[-1]["card"]["tricks"] == 1
    assert any(e["kind"] == "finding" and e["finding"]["type"] == "prechecked_addon" for e in events)
    assert _sse(client, cid) == events
    assert (tmp_path / f"{cid}.json").exists()
