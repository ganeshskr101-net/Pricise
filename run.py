"""Run one Pricise check from a JSON input file and print the verdict and the agent trace file.

    python run.py                       # the JBL Flip 6 on Amazon (input_examples/example_1.json)
    python run.py --input input_examples/example_2.json

The input is {"url": ..., "page": <PageFacts the extension read from a real product page>}. If a Pricise backend already answers at
BACKEND_URL (default http://localhost:8787), the check runs there (in the mode it was started with). Otherwise this
script starts one in this process on port 8787 for the run (sample mode when .env says SAMPLE_MODE=true, the default for a
fresh clone), and stops it when the check is done. Nothing is left running.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _get(url: str, timeout: float = 3):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def _post(url: str, body: dict, timeout: float = 30):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def backend_up(base: str) -> dict | None:
    try:
        return _get(base + "/health")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def start_backend(port: int):
    """uvicorn in a background thread of this process; returns (server, thread)."""
    import uvicorn

    from backend.stub_server import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            return server, thread
        time.sleep(0.1)
    raise SystemExit(f"Could not start the backend on port {port} (is something else using it?)")


def run_check(base: str, url: str, page: dict | None) -> tuple[str, dict, list[dict]]:
    cid = _post(base + "/check?fresh=1", {"url": url, "page": page})["check_id"]
    with urllib.request.urlopen(f"{base}/check/{cid}/events", timeout=240) as r:
        raw = r.read().decode()
    events = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: ")]
    card = [e["card"] for e in events if e["kind"] == "final_card"][-1]
    return cid, card, [e["finding"] for e in events if e["kind"] == "finding"]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Run one Pricise check from a JSON input file.")
    ap.add_argument("--input", default=str(ROOT / "input_examples" / "example_1.json"),
                    help="input JSON: {\"url\": ..., \"page\": PageFacts or null} (default: the JBL Flip 6 example)")
    ap.add_argument("--save", help="optional: write the final card and findings to this JSON file")
    args = ap.parse_args(argv)
    data = json.loads(Path(args.input).read_text())
    base = os.environ.get("BACKEND_URL", "http://localhost:8787").rstrip("/")

    from backend.env import load_dotenv
    load_dotenv()
    health, started = backend_up(base), None
    if health is None:
        started = start_backend(int(base.rsplit(":", 1)[-1]) if base.count(":") == 2 else 8787)
        health = backend_up(base)
    print(f"Backend {base} · mode: {health.get('mode')} · deal={health.get('agent_deal')} trust={health.get('agent_trust')} "
          f"price={health.get('agent_price')} orch={health.get('agent_orch')} data={health.get('data_source')}"
          f"{' (started for this run)' if started else ''}")
    try:
        t0 = time.time()
        cid, card, findings = run_check(base, data["url"], data.get("page"))
    finally:
        if started:
            started[0].should_exit = True
            started[1].join(timeout=10)
    from backend.agent_trace import path_for

    print(f"\nVERDICT  {card['title']} · {card['subtitle']}  ({time.time() - t0:.1f}s)")
    print(f"  Shop safe?        {card['safe']['headline']}")
    print(f"  Honest deal?      {card['honest']['headline']}")
    print(f"  Best safe price   {card['best_price']['headline']}")
    if card.get("alt") and card["alt"].get("store"):
        print(f"  Better option     {card['alt']['store']} · ${card['alt']['total']}")
    trace = path_for(cid)
    shown = trace.relative_to(ROOT) if trace.is_relative_to(ROOT) else trace
    print(f"\nAgent trace: {shown} ({len(findings)} finding events)")
    if args.save:
        Path(args.save).write_text(json.dumps({"check_id": cid, "url": data["url"], "final_card": card, "findings": findings},
                                              indent=1))
        print(f"Saved: {args.save}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
