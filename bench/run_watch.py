"""Measured run for the wishlist watch: per-step prompt size, naive full-history baseline vs the Markovian engine.

Run from the repo root:  python bench/run_watch.py        (about a second; needs Node + backend/engines/world_model_ts/node_modules)

What it does:
- Starts its own engine sidecar on a free 127.0.0.1 port (stopped at the end) and runs the 30-day watch for the 4 demo
  wishlist items on the made-up Store Lab prices (bench/watch/prices.json), in a temp folder (no repo checkpoint).
- "after" (Markovian): the prompt the vendored engine builds for each day's chunk (goal + bounded carryover), as the
  sidecar returns it.
- "before" (naive): a baseline written for this measurement, NOT code that exists in Pricise: each day's prompt is the
  goal plus the full text of every earlier day's chunk. Both columns come from the SAME run, so the decisions and alerts
  are identical; only the prompt differs.
- Every number is estimated as ceil(chars / 4), the reference engine's own estimator. No model is called: the watch's
  decisions are rules. The numbers show the prompt size a model-in-the-loop version WOULD receive, not a saving that
  happens in Pricise today.
Prints the table and saves bench/out/watch.json (git-ignored).
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import watch  # noqa: E402
from backend.engines import markovian  # noqa: E402
from backend.tests.test_world_model_dedupe import ENGINE_DIR, _start_real_sidecar, unused_port  # noqa: E402

DAYS = 30
DEMO = [{"id": "sony", "target": 215, "rule": "Under $215 · new only"}, {"id": "kindle", "target": 120, "rule": "Under $120"},
        {"id": "shoes", "target": 90, "rule": "Under $90 · trusted only"}, {"id": "fryer", "target": 80, "rule": "Under $80"}]
LABEL = "estimated (chars/4)"
OUT = ROOT / "bench" / "out" / "watch.json"


def estimate(text: str) -> int:
    return math.ceil(len(text) / 4)  # same as the engine's estimateTokens()


class RecordingChain(markovian.SidecarChain):
    """Keeps each chunk's text too, for the naive baseline."""

    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []

    def chunk(self, text: str, done: bool) -> None:
        self.texts.append(text)
        super().chunk(text, done)


def naive_prompts(texts: list[str]) -> list[str]:
    """Day d's prompt: the goal + the full history of days 1..d-1 (what a history-resending model loop would send)."""
    return [f"{watch.GOAL}\n\n[FULL HISTORY]\n" + "\n\n".join(texts[:i]) + f"\n\nContinue with day {i + 1}."
            for i in range(len(texts))]


def main() -> int:
    if not (ENGINE_DIR / "node_modules" / "tsx").exists():
        print("needs: cd backend/engines/world_model_ts && npm install --ignore-scripts", file=sys.stderr)
        return 2
    port = unused_port()
    proc = _start_real_sidecar(port)
    try:
        os.environ["WORLD_MODEL_URL"] = f"http://127.0.0.1:{port}"
        os.environ["WATCH_DATA_DIR"] = tempfile.mkdtemp(prefix="watch-bench-")
        watch.sync_items(DEMO)
        chain = RecordingChain()
        result = watch.advance_to(DAYS, chain=chain)
    finally:
        proc.kill()
        proc.wait(timeout=5)

    if chain.local_chunks or len(chain.prompts) != DAYS:
        print(f"measurement invalid: {chain.local_chunks} chunks ran without the engine", file=sys.stderr)
        return 1
    mark = [p["prompt_tokens"] for p in chain.prompts]  # the engine's own estimate of the prompt it built
    if any(p["token_label"] != "estimated" for p in chain.prompts):
        print("measurement invalid: the engine labelled a number other than 'estimated'", file=sys.stderr)
        return 1
    naive = [estimate(p) for p in naive_prompts(chain.texts)]

    print(f"Wishlist watch, {DAYS} days, {len(DEMO)} items, made-up Store Lab prices. Prompt size per day, {LABEL}.")
    print("before = naive baseline written for this measurement (full history each day); after = Markovian engine.")
    print("Same run, same decisions; no model was called.\n")
    print("day | naive prompt | markovian prompt | naive cum. | markovian cum. | ratio (cum.)")
    print("----|--------------|------------------|------------|----------------|-------------")
    rows, ncum, mcum = [], 0, 0
    for day, (n, m) in enumerate(zip(naive, mark), start=1):
        ncum += n
        mcum += m
        rows.append({"day": day, "naive_prompt": n, "markovian_prompt": m, "naive_cum": ncum, "markovian_cum": mcum,
                     "ratio_cum": ncum / mcum})
        print(f"{day:3d} | {n:12d} | {m:16d} | {ncum:10d} | {mcum:14d} | {ncum / mcum:10.2f}x")
    saved = ncum - mcum
    print(f"\ntotals ({LABEL}): naive {ncum}, markovian {mcum}, difference {saved} "
          f"({100 * saved / ncum:.1f}% of naive)")
    print(f"flat check ({LABEL}): markovian prompt per day min {min(mark)}, max {max(mark)}; "
          f"naive prompt per day min {min(naive)}, max {max(naive)}")
    print(f"chains: {len(chain.runs)} ({', '.join(str(r['chunks']) for r in chain.runs)} chunks; "
          f"status {', '.join(r['status'] for r in chain.runs)})")
    alerts = ", ".join(f"{a['item']} day {a['day']} at {a['price']:.2f}" for a in result.alerts)
    print(f"alerts: {alerts}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"), "label": LABEL, "days": DAYS,
        "items": [i["id"] for i in DEMO], "prices": "made-up Store Lab fixtures (bench/watch/prices.json)",
        "before": "naive baseline written for this measurement: goal + full history of earlier chunks",
        "after": "prompts built by the vendored Markovian engine (goal + bounded carryover)",
        "model_calls": 0, "rows": rows, "totals": {"naive": ncum, "markovian": mcum, "difference": saved},
        "flat_check": {"markovian_min": min(mark), "markovian_max": max(mark), "naive_min": min(naive), "naive_max": max(naive)},
        "chains": chain.runs, "alerts": result.alerts,
    }, indent=2), encoding="utf-8")
    print(f"\nSaved {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
