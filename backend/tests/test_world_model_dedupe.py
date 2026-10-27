"""World Model shared state (AGENT_ORCH=on): one finding per key, versions on change, clean fallback when the sidecar is down.

Most tests use a fake sidecar (a Python HTTP server on a random port with the same API as server.ts). The last test runs
the real server.ts and is skipped when Node or backend/engines/world_model_ts/node_modules is missing. No test ever uses
a real sidecar on 8788: WORLD_MODEL_URL always points at a port this file opened or knows is unused.
"""
import asyncio
import json
import logging
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from backend import orchestrator
from backend.contracts import BestPrice, Evidence, FinalCard, Finding, Verdict
from backend.engines import world_model

ENGINE_DIR = Path(__file__).resolve().parents[1] / "engines" / "world_model_ts"
EV = [Evidence(kind="selector", ref="#addon", label="Pre-ticked")]
RESETS = {"visits": [{"fresh_session": True, "reset": True}] * 3}
WARN_DOWN = "world model sidecar not used"
WARN_DIFF = "world model has"


def unused_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeSidecar:
    """Same API and rules as server.ts: content without ts decides "changed"; versions and statuses per key."""

    def __init__(self) -> None:
        self.bodies: list[bytes] = []
        self.gets = 0
        self.versions: dict[str, list[dict]] = {}  # key -> every stored version, in order
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def _send(self, body: dict) -> None:
                raw = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                fake.bodies.append(raw)
                body = json.loads(raw)
                self._send(fake.write(body["key"], body["finding"]))

            def do_GET(self):
                fake.gets += 1
                self._send({"findings": fake.rows(self.path.split("/")[2])})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def write(self, key: str, finding: dict) -> dict:
        versions = self.versions.setdefault(key, [])
        strip = lambda f: {k: v for k, v in f.items() if k != "ts"}  # noqa: E731
        changed = not versions or strip(versions[-1]) != strip(finding)
        if changed:
            versions.append(finding)
        return {"changed": changed, "version": len(versions), "entity_id": f"finding:ingest:{key}"}

    def rows(self, check_id: str) -> list[dict]:
        return [{"key": k, "version": len(v), "proposed_by": v[0]["agent"], "statuses": [x["status"] for x in v],
                 "finding": v[-1]} for k, v in self.versions.items() if v[-1]["check_id"] == check_id]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(orchestrator, "_RUNS", {})
    monkeypatch.setattr(orchestrator, "TRACES_DIR", tmp_path)
    monkeypatch.setattr(world_model, "_warned", False)
    monkeypatch.setattr(world_model, "_down_until", 0.0)
    monkeypatch.setenv("AGENT_ORCH", "on")
    monkeypatch.setenv("WORLD_MODEL_URL", f"http://127.0.0.1:{unused_port()}")


@pytest.fixture
def sidecar(monkeypatch):
    fake = FakeSidecar()
    monkeypatch.setenv("WORLD_MODEL_URL", fake.url)
    yield fake
    fake.close()


def make(type_="prechecked_addon", status="proposed", agent="page", cid="c1", **kw):
    return Finding(check_id=cid, agent=agent, type=type_, status=status, summary=f"{type_} {status}", evidence=EV,
                   ts=time.time(), **kw)


def agent(*findings):
    async def gen(_chk):
        for f in findings:
            await asyncio.sleep(0)
            yield f.model_copy(update={"ts": time.time()})  # a re-run gives a new ts; it must not count as a change

    return gen


def card(_chk, findings):
    v = Verdict(status="unknown", headline="")
    return FinalCard(check_id="c1", title=",".join(f.status for f in findings), subtitle="", tone="unknown",
                     safe=v, honest=v, best_price=BestPrice(status="unknown", headline=""))


def run(cid, agents, rechecker=None):
    async def collect():
        c = {"check_id": cid, "url": "http://localhost/storelab/x/"}
        return [e async for e in orchestrator.run_check(c, agents, rechecker, build_card=card)]

    return asyncio.run(collect())


def found(events):
    return [e["finding"] for e in events if e["kind"] == "finding"]


def trace(tmp_path, cid="c1"):
    return json.loads((tmp_path / f"{cid}.json").read_text(encoding="utf-8"))["findings"]


def warnings(caplog, text):
    return [r for r in caplog.records if r.levelno == logging.WARNING and text in r.getMessage()]


# the same finding written twice


def test_same_finding_twice_emits_one_event(sidecar, tmp_path):
    f = make()
    events = run("c1", [agent(f, f), agent(f)])  # twice in one agent, once more from a parallel agent
    assert len(found(events)) == 1
    assert len(sidecar.bodies) == 3  # every write reached the shared state ...
    key = world_model.finding_key(f)
    assert list(sidecar.versions) == [key] and len(sidecar.versions[key]) == 1  # ... as one entity, one version
    assert [r["statuses"] for r in trace(tmp_path)] == [["proposed"]]


def test_key_ignores_summary_and_ts_but_not_evidence():
    f = make()
    assert world_model.finding_key(f) == world_model.finding_key(f.model_copy(update={"summary": "reworded", "ts": 1.0}))
    other = f.model_copy(update={"evidence": [Evidence(kind="selector", ref="#other")]})
    assert world_model.finding_key(f) != world_model.finding_key(other)


# a status change updates the same finding


def test_status_change_is_a_new_version_of_the_same_entity(sidecar, tmp_path):
    events = run("c1", [agent(make(status="proposed"), make(status="confirmed"), make(status="confirmed"))])
    assert [f.status for f in found(events)] == ["proposed", "confirmed"]
    (versions,) = sidecar.versions.values()
    assert [v["status"] for v in versions] == ["proposed", "confirmed"]
    (row,) = trace(tmp_path)
    assert (row["statuses"], row["final_status"]) == (["proposed", "confirmed"], "confirmed")


def test_skeptic_steps_update_the_same_entity(sidecar, tmp_path):
    async def rechecker(_f):
        return RESETS

    timer = make("fake_timer", agent="deal", model="hidden browser")
    events = run("c1", [agent(timer)], rechecker)
    assert [f.status for f in found(events)] == ["proposed", "challenged", "confirmed"]
    (versions,) = sidecar.versions.values()
    assert [v["status"] for v in versions] == ["proposed", "challenged", "confirmed"]
    assert trace(tmp_path)[0]["statuses"] == ["proposed", "challenged", "confirmed"]


# the trace reads the shared state


def test_trace_reads_the_sidecar_and_warns_once_when_it_differs(sidecar, tmp_path, caplog):
    sidecar.write("0" * 64, make("drip_fees", agent="deal").model_dump())  # a finding only the shared state has
    run("c1", [agent(make())])
    assert sidecar.gets == 1
    assert [r["type"] for r in trace(tmp_path)] == ["drip_fees", "prechecked_addon"]
    assert len(warnings(caplog, WARN_DIFF)) == 1


def test_no_warning_when_sidecar_and_local_agree(sidecar, tmp_path, caplog):
    run("c1", [agent(make(), make(status="confirmed"))])
    assert sidecar.gets == 1 and warnings(caplog, WARN_DIFF) == []


# only finding JSON leaves the process


def test_sidecar_receives_only_finding_json(sidecar, monkeypatch):
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "tok-should-never-leave")
    run("c1", [agent(make(), make("drip_fees", agent="deal"))])
    assert sidecar.bodies
    for raw in sidecar.bodies:
        body = json.loads(raw)
        assert set(body) == {"key", "check_id", "finding"}
        assert set(body["finding"]) == set(Finding.model_fields)
        assert b"tok-should-never-leave" not in raw


def test_non_loopback_url_is_never_called(monkeypatch, caplog):
    monkeypatch.setenv("WORLD_MODEL_URL", "http://example.com:8788")
    f = make()
    assert len(found(run("c1", [agent(f, f)]))) == 1  # local dedupe still works
    assert len(warnings(caplog, WARN_DOWN)) == 1


# sidecar outage


def test_outage_falls_back_cleanly_with_one_warning(tmp_path, caplog):
    f = make()
    first = run("c1", [agent(f, f, make(status="confirmed"))])
    second = run("c2", [agent(make(cid="c2"))])
    assert [x.status for x in found(first)] == ["proposed", "confirmed"]  # local key map: no duplicate
    assert first[-1]["kind"] == "final_card" and second[-1]["kind"] == "final_card"
    assert len(found(second)) == 1
    assert trace(tmp_path)[0]["statuses"] == ["proposed", "confirmed"]  # trace from the local entries
    assert len(warnings(caplog, WARN_DOWN)) == 1


def test_after_an_outage_new_checks_skip_the_sidecar_until_retry(sidecar, monkeypatch):
    monkeypatch.setattr(world_model, "_down_until", time.monotonic() + 30)  # an outage just happened
    run("c1", [agent(make())])
    assert sidecar.bodies == []  # no wait on a dead port
    monkeypatch.setattr(world_model, "_down_until", time.monotonic() - 1)  # retry time reached
    run("c2", [agent(make(cid="c2"))])
    assert len(sidecar.bodies) == 1


def test_sidecar_dying_mid_check_falls_back(sidecar, tmp_path, caplog):
    f = make()

    async def dies(_chk):
        yield f
        sidecar.close()
        await asyncio.sleep(0)
        yield f.model_copy(update={"ts": time.time()})
        yield f.model_copy(update={"status": "confirmed"})

    events = run("c1", [dies])
    assert [x.status for x in found(events)] == ["proposed", "confirmed"]
    assert events[-1]["kind"] == "final_card"
    assert len(warnings(caplog, WARN_DOWN)) == 1


def test_agent_orch_unset_keeps_todays_behaviour(sidecar, monkeypatch):
    monkeypatch.delenv("AGENT_ORCH")
    f = make()
    assert len(found(run("c1", [agent(f, f)]))) == 2  # no dedupe, exactly as before
    assert sidecar.bodies == [] and sidecar.gets == 0


# the real server.ts (skipped without Node or node_modules)


class _SidecarProcess(subprocess.Popen):
    """tsx's CLI runs server.ts in a CHILD node process, so killing the CLI alone left the server answering (a "stopped"
    sidecar kept serving, and every test run leaked node processes). kill()/terminate() stop the whole process tree."""

    def __init__(self, *args, **kw):
        if os.name != "nt":
            kw["start_new_session"] = True  # own process group, so killpg reaches tsx's child
        super().__init__(*args, **kw)

    def kill(self) -> None:
        if self.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.pid)], capture_output=True)
        else:
            try:
                os.killpg(self.pid, 9)
            except ProcessLookupError:
                pass

    terminate = kill


def _start_real_sidecar(port: int, data_dir: Path | None = None, env_extra: dict | None = None) -> subprocess.Popen:
    """data_dir: where H-MEM keeps notes (HMEM_DATA_DIR); a fresh temp folder by default, never the repo's .pricise-data.
    env_extra: test-only settings such as MARKOVIAN_CHUNK_TIMEOUT_MS."""
    tsx = ENGINE_DIR / "node_modules" / "tsx" / "dist" / "cli.mjs"
    env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT") if k in os.environ}  # nothing else: no secrets in its env
    env["WORLD_MODEL_URL"] = f"http://127.0.0.1:{port}"
    env["HMEM_DATA_DIR"] = str(data_dir or tempfile.mkdtemp(prefix="hmem-test-"))
    env.update(env_extra or {})
    proc = _SidecarProcess([shutil.which("node"), str(tsx), "server.ts"], cwd=ENGINE_DIR, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                    f"http://127.0.0.1:{port}/health", timeout=1) as r:
                if json.loads(r.read()).get("ok"):
                    return proc
        except OSError:
            time.sleep(0.2)
    proc.kill()
    pytest.fail("real world model sidecar did not start in 15 s")


@pytest.mark.skipif(not shutil.which("node") or not (ENGINE_DIR / "node_modules" / "tsx").exists(),
                    reason="needs Node and: cd backend/engines/world_model_ts && npm install --ignore-scripts")
def test_real_sidecar_dedupes_and_versions(monkeypatch, tmp_path, caplog):
    port = unused_port()
    proc = _start_real_sidecar(port)
    try:
        monkeypatch.setenv("WORLD_MODEL_URL", f"http://127.0.0.1:{port}")
        f = make()
        events = run("c1", [agent(f, f, make(status="confirmed")), agent(f)])
        assert [x.status for x in found(events)] == ["proposed", "confirmed"]
        (row,) = trace(tmp_path)  # read back from the real engine
        assert row["statuses"] == ["proposed", "confirmed"] and row["final_status"] == "confirmed"
        assert warnings(caplog, WARN_DOWN) == [] and warnings(caplog, WARN_DIFF) == []
        req = urllib.request.Request(f"http://127.0.0.1:{port}/findings", method="POST",
                                     headers={"Content-Type": "application/json"},
                                     data=json.dumps({"key": "0" * 64, "check_id": "c1", "finding": f.model_dump(),
                                                      "token": "x"}).encode())
        with pytest.raises(urllib.error.HTTPError) as err:  # anything but finding JSON is refused
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=2)
        assert err.value.code == 400
    finally:
        proc.kill()
        proc.wait(timeout=5)
