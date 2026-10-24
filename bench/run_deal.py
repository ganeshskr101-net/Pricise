"""Deal Agent bench: runs the agent on every bench/deal page in bench/labels.csv and reports accuracy per trick.

Run from the repo root:  python bench/run_deal.py        (TIMER_GAP_S defaults to 3 here; results in bench/out/deal.csv)
A page counts as "flagged" when the agent returns a CONFIRMED finding of the labelled trick type.
"""
from __future__ import annotations

import asyncio
import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TIMER_GAP_S", "3")

from backend.agents.deal import assess_deal  # noqa: E402
from backend.contracts import PageFacts  # noqa: E402
from backend.tests.content_reader import read_page_facts, serve_repo  # noqa: E402

CONCURRENCY = 4


async def run_one(base: str, row: dict, sem: asyncio.Semaphore) -> dict:
    async with sem:
        url = f"{base}/bench/{row['page']}"
        page = PageFacts(**await read_page_facts(url))
        found = await assess_deal(f"bench-{Path(row['page']).stem}", url, page)
        hit = [f for f in found if f.type == row["trick"] and f.status == "confirmed"]
        return {**row, "predicted": int(bool(hit)), "summary": hit[0].summary if hit else ""}


async def main() -> int:
    rows = list(csv.DictReader(open(ROOT / "bench" / "labels.csv")))
    rows = [r for r in rows if r["page"].startswith("deal/")]
    server, base = serve_repo()
    try:
        sem = asyncio.Semaphore(CONCURRENCY)
        results = await asyncio.gather(*(run_one(base, r, sem) for r in rows))
    finally:
        server.shutdown()
    out = ROOT / "bench" / "out"
    out.mkdir(exist_ok=True)
    with open(out / "deal.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["page", "trick", "expected", "predicted", "note", "summary"])
        w.writeheader()
        w.writerows(results)
    per = defaultdict(lambda: [0, 0])
    for r in results:
        per[r["trick"]][0] += int(r["expected"]) == r["predicted"]
        per[r["trick"]][1] += 1
        if int(r["expected"]) != r["predicted"]:
            print(f"MISS {r['page']}: expected {r['expected']}, got {r['predicted']} ({r['note']})")
    for trick, (ok, n) in per.items():
        print(f"{trick:18s} {ok}/{n}")
    ok = sum(v[0] for v in per.values())
    print(f"{'total':18s} {ok}/{len(results)} = {ok / len(results):.0%}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
