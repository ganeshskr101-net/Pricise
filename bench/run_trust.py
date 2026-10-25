"""Trust Agent bench: vets every bench/trust store in bench/labels.csv (plus the three Store Lab demo stores) and reports accuracy.

Run from the repo root:  python bench/run_trust.py        (no browser needed; a few seconds; details in bench/out/trust.csv)
A store counts as "flagged" when the verdict is caution or unsafe. Labels were written before the agent was run.
"""
from __future__ import annotations

import asyncio
import csv
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.agents import trust  # noqa: E402
from backend.tests.content_reader import serve_repo  # noqa: E402

DEMO_EXPECTED = {"storelab/audiodealz": {"caution", "unsafe"}, "storelab/store-c": {"safe"}, "storelab/store-d": {"unsafe"}}


async def run() -> dict:
    rows = [r for r in csv.DictReader(open(ROOT / "bench" / "labels.csv")) if r["page"].startswith("trust/")]
    server, base = serve_repo()
    trust.clear_cache()  # start cold: the first vet pays for hashing the other stores' photos, like a fresh backend
    try:
        results = []
        for r in rows:
            tv = await trust.assess_store(f"{base}/bench/{r['page']}", f"bench-{Path(r['page']).parent.name}", use_cache=False)
            predicted = int(tv["verdict"] != "safe")
            results.append({**r, "verdict": tv["verdict"], "predicted": predicted, "ms": tv["elapsed_ms"],
                            "reasons": "; ".join(tv["reasons"])})
        demo = {}
        for folder, ok in DEMO_EXPECTED.items():
            tv = await trust.assess_store(f"{base}/{folder}/", "bench-demo", use_cache=False)
            demo[folder] = (tv["verdict"], tv["verdict"] in ok, tv["elapsed_ms"], tv["reasons"])
    finally:
        server.shutdown()
    return {"rows": results, "demo": demo}


def main() -> int:
    out = asyncio.run(run())
    rows, demo = out["rows"], out["demo"]
    (ROOT / "bench" / "out").mkdir(exist_ok=True)
    with open(ROOT / "bench" / "out" / "trust.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["page", "trick", "expected", "predicted", "verdict", "ms", "note", "reasons"])
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        mark = "ok  " if int(r["expected"]) == r["predicted"] else "MISS"
        print(f"{mark} {r['page']:<22} expected {'risky' if r['expected'] == '1' else 'safe ':<5}  got {r['verdict']:<8}"
              f"{r['ms']:>5} ms  {r['reasons']}")
    print()
    for folder, (verdict, ok, ms, reasons) in demo.items():
        print(f"{'ok  ' if ok else 'MISS'} {folder:<22} got {verdict:<8}{ms:>5} ms  {'; '.join(reasons)}")
    scams = [r for r in rows if r["expected"] == "1"]
    legit = [r for r in rows if r["expected"] == "0"]
    caught = sum(r["predicted"] for r in scams)
    alarms = sum(r["predicted"] for r in legit)
    times = [r["ms"] for r in rows]
    correct = caught + len(legit) - alarms
    print(f"\nrisky stores caught  {caught}/{len(scams)}\nfalse alarms         {alarms}/{len(legit)}\n"
          f"{'total':21s}{correct}/{len(rows)} = {correct / len(rows):.0%}\n"
          f"vet time             median {statistics.median(times):.0f} ms, max {max(times)} ms (first vet is cold)")
    return 0 if correct == len(rows) and all(v[1] for v in demo.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
