"""Save a finished check of a real product page as a sample-mode recording (samples/<name>.json).

    .venv/bin/python scripts/record_run.py --latest jbl_flip6_amazon     # the newest finished check on a real page
    .venv/bin/python scripts/record_run.py --check <check_id> kindle_amazon

Run it while the full-mode backend (scripts/run.sh --real) is still up, right after the check finished in the side panel.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import recordings  # noqa: E402


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("name")
    ap.add_argument("--check")
    ap.add_argument("--latest", action="store_true")
    ap.add_argument("--base", default="http://localhost:8787")
    ap.add_argument("--example", type=int, help="also write input_examples/example_N.json (the url + PageFacts read in Chrome)")
    a = ap.parse_args(argv)
    cid = a.check
    if a.latest or not cid:
        with urllib.request.urlopen(f"{a.base}/checks/recent", timeout=5) as r:
            recent = [c for c in json.loads(r.read())["checks"] if c["finished"] and c["real"]]
        if not recent:
            raise SystemExit("No finished check of a real page on that backend yet.")
        cid = recent[0]["check_id"]
        print(f"newest finished real-page check: {cid} {recent[0]['url']}")
    path = recordings.record(a.base, cid, a.name)
    print(f"saved {path.relative_to(ROOT)}")
    if a.example:
        for out in write_example(json.loads(path.read_text()), cid, a.example):
            print(f"saved {out.relative_to(ROOT)}")
    return 0


def write_example(rec: dict, cid: str, n: int) -> list[Path]:
    """input_examples/example_N.json (the url + PageFacts read in Chrome), output_examples/example_N_result.json (the
    findings as they streamed and the final card) and example_N_trace.jsonl (the agent trace, logs/<check_id>.jsonl)."""
    (ROOT / "input_examples").mkdir(exist_ok=True)
    (ROOT / "output_examples").mkdir(exist_ok=True)
    inp = ROOT / "input_examples" / f"example_{n}.json"
    inp.write_text(json.dumps({"url": rec["url"], "page": rec["page"]}, indent=1) + "\n")
    res = ROOT / "output_examples" / f"example_{n}_result.json"
    findings = [e["finding"] for e in rec["events"] if e["kind"] == "finding"]
    card = next(e["card"] for e in rec["events"] if e["kind"] == "final_card")
    res.write_text(json.dumps({"check_id": cid, "url": rec["url"], "recorded_at": rec["recorded_at"], "final_card": card,
                               "findings": findings, "online": (rec.get("evidence_data") or {}).get("online")}, indent=1) + "\n")
    out = [inp, res]
    log = ROOT / "logs" / f"{cid}.jsonl"
    if log.exists():
        trace = ROOT / "output_examples" / f"example_{n}_trace.jsonl"
        trace.write_text(log.read_text())
        out.append(trace)
    else:
        print(f"no agent trace at {log.relative_to(ROOT)}: example_{n}_trace.jsonl not written")
    return out


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
