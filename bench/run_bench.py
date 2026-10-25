"""Full bench: runs every page in bench/labels.csv through the real orchestrator path and scores it against the labels.

Run from the repo root:
  python bench/run_bench.py            full mode: hidden-browser run-ahead and timer test on, model calls allowed
  python bench/run_bench.py --sample   sample mode: Chromium only reads the pages; run-ahead, timer test and model calls off

Each page runs once, one at a time: the real content script (extension/content/content.js) reads it, then
orchestrator.run_check runs it with AGENT_ORCH=on and AGENT_DEAL=real (World Model sidecar optional). A trick counts as
caught only when a finding of the labelled type is "confirmed" and has evidence. A type whose agent isn't connected is
"n/a" with its page count, never 0. Results go to bench/results.json (--out to change), traces to bench/out/traces/.
Measured numbers only: targets are printed for reference and never change a result. TIMER_GAP_S defaults to 3 here.
Chromium: PLAYWRIGHT_CHROMIUM_PATH if set, else Playwright's own (backend/tools/browser.py). If it can't start, nothing is scored.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import statistics
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LABELS = ROOT / "bench" / "labels.csv"
RESULTS = ROOT / "bench" / "results.json"
TRACES = ROOT / "bench" / "out" / "traces"

DEAL_TYPES = ("fake_discount", "resetting_timer", "drip_fees", "prechecked_addon")
SCAM, LOOKALIKE, CLEAN = "scam_store", "lookalike_listing", "clean_control"
PLANNED = {"fake_discount": 7, "resetting_timer": 6, "drip_fees": 7, "prechecked_addon": 6, SCAM: 7, LOOKALIKE: 7, CLEAN: 10}
PLANNED_TOTAL = 50
TARGETS = {"trick_detection": ">= 85%", "false_alarms": "<= 10%", "exact_match": ">= 90%", "scam_detection": ">= 85%",
           "median_s": "<= 10 s"}  # reference only: never used to compute or change a result
NOT_CONNECTED = {SCAM: "agent not connected", LOOKALIKE: "agent not connected"}  # Trust and Price Agents not merged yet
SAMPLE_OFF = {"resetting_timer": "hidden browser off", "drip_fees": "hidden browser off"}  # no timer test, no run-ahead
MODES = {"full": "hidden-browser run-ahead and timer test on; Nightshift model calls allowed; price history from the fixture",
         "sample": "Chromium reads the pages only; run-ahead, timer test and model calls off; price history from the fixture"}
CONFIRMED_NOTE = "confirmed = Deal Agent confirmed it with evidence; the Skeptic only checked evidence (no rechecker connected)"
FALSE_ALARM_NOTE = ("false alarm = a confirmed fake_discount, resetting_timer, drip_fees or prechecked_addon finding on a clean "
                    "page (expected=0 or clean_control); trust/no_https and not_connected findings do not count")
TRICK_NOTE = ("trick detection = planted pages (expected=1) only; honest controls (expected=0) are scored in false alarms; "
              "every other page is listed as excluded with its reason")
TIMER_NOTE = ("timer pages take longer in full mode: the timer test makes 3 fresh visits TIMER_GAP_S seconds apart; "
              "the median is page load (page.goto) to the last final card, one page at a time")
METRICS = ("trick_detection", "false_alarms", "scam_detection")  # every labelled page is scored in one of these, or excluded
PENDING = "pending"  # backend.agents.deal.PENDING: a "still checking" placeholder row, replaced by the tool's real finding


# ----------------------------------------------------------------------------- scoring (pure: no browser, no network)


def load_labels(path: Path = LABELS) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return [{**r, "expected": int(r["expected"])} for r in csv.DictReader(fh)]


def na_reasons(mode: str) -> dict[str, str]:
    """Page type -> why it can't be scored in this mode."""
    return {**NOT_CONNECTED, **(SAMPLE_OFF if mode == "sample" else {})}


def is_clean(row: dict) -> bool:
    return row["trick"] == CLEAN or (row["trick"] in DEAL_TYPES and row["expected"] == 0)


def final_findings(findings: Iterable) -> list[dict]:
    """The last version of each finding in a check's stream (same identity as world_model.finding_key), placeholders left out."""
    latest: dict[tuple, dict] = {}
    for f in findings:
        if f.model == PENDING:
            continue
        key = (f.agent, f.type, tuple(sorted(f"{e.kind}:{e.ref}" for e in f.evidence)))
        latest[key] = {"agent": f.agent, "type": f.type, "status": f.status, "evidence": len(f.evidence), "summary": f.summary}
    return list(latest.values())


def page_outcome(row: dict, findings: list[dict], card: Optional[dict] = None, read_s: Optional[float] = None,
                 check_s: Optional[float] = None, error: Optional[str] = None) -> dict:
    """One page's result. caught: a finding of the labelled type is confirmed AND has evidence.
    deal_flags: the Deal trick types confirmed on the page (what a false alarm counts)."""
    trick = row["trick"]
    match = [f for f in findings if f["type"] == trick]
    caught = any(f["status"] == "confirmed" and f["evidence"] > 0 for f in match)
    flags = sorted({f["type"] for f in findings if f["type"] in DEAL_TYPES and f["status"] == "confirmed"})
    reason = ""
    if error:
        reason = f"page not checked: {error}"
    elif row["expected"] and not caught:
        reason = "; ".join(f"{f['type']} {f['status']}, evidence {f['evidence']}: {f['summary']}" for f in match) \
            or f"no {trick} finding"
    elif is_clean(row) and flags:
        reason = "flagged on a clean page: " + ", ".join(flags)
    timed = read_s is not None and check_s is not None
    return {"page": row["page"], "trick": trick, "expected": row["expected"], "caught": caught, "deal_flags": flags,
            "findings": [{k: f[k] for k in ("agent", "type", "status", "evidence")} for f in findings],
            "card": card, "load_to_card_s": read_s + check_s if timed else None, "read_s": read_s, "check_s": check_s,
            "reason": reason, "error": error}


def rate(k: int, n: int) -> dict:
    return {"k": k, "n": n, "rate": k / n if n else None}


def fmt_rate(r: dict) -> str:
    """k/n and the percent, cut (never rounded up) to one decimal."""
    if not r["n"]:
        return f"{r['k']}/{r['n']} (no pages to score)"
    tenths = 1000 * r["k"] // r["n"]
    return f"{r['k']}/{r['n']} = {tenths // 10}.{tenths % 10}%"


def median(values: Iterable[Optional[float]]) -> Optional[float]:
    xs = [v for v in values if v is not None]
    return statistics.median(xs) if xs else None


def _na(t: str, reason: str, outcomes: list[dict]) -> dict:
    return {"status": "n/a", "reason": reason, "pages": sum(o["trick"] == t for o in outcomes), "planned": PLANNED.get(t)}


def score(outcomes: list[dict], mode: str) -> dict:
    na = na_reasons(mode)
    per_type = {}
    for t in dict.fromkeys([*PLANNED, *(o["trick"] for o in outcomes)]):
        rows = [o for o in outcomes if o["trick"] == t]
        if t in na:
            per_type[t] = _na(t, na[t], outcomes)
        elif t in DEAL_TYPES:
            planted = [o for o in rows if o["expected"] == 1]
            per_type[t] = {"status": "scored", "pages": len(rows), "planned": PLANNED[t], "planted": len(planted),
                           "honest": len(rows) - len(planted),  # honest controls: scored in false_alarms
                           "detection": rate(sum(o["caught"] for o in planted), len(planted))}
        elif t == CLEAN:
            per_type[t] = {"status": "clean pages (see false_alarms)", "pages": len(rows), "planned": PLANNED[t]}
        elif t == SCAM:  # Trust Agent connected: a scam store is caught when the card's "safe" verdict is caution or bad
            per_type[t] = {"status": "scored", "pages": len(rows), "planned": PLANNED[t],
                           "detection": rate(sum((o["card"] or {}).get("safe") in ("caution", "bad") for o in rows), len(rows))}
        else:
            per_type[t] = _na(t, "no scorer for this page type", outcomes)
    clean = [o for o in outcomes if is_clean(o)]
    scored_clean = [o for o in clean if o["trick"] not in na]
    false_alarms = {**rate(sum(bool(o["deal_flags"]) for o in scored_clean), len(scored_clean)),
                    "clean_pages": len(clean), "planned": PLANNED[CLEAN],
                    "left_out": [o["page"] for o in clean if o["trick"] in na]}
    exact_match = per_type[LOOKALIKE] if per_type[LOOKALIKE]["status"] == "n/a" else \
        _na(LOOKALIKE, "no scorer for look-alikes yet", outcomes)
    scam = per_type[SCAM] if per_type[SCAM]["status"] == "n/a" else {**per_type[SCAM]["detection"], "status": "scored"}
    timing = {"median_load_to_card_s": median(o["load_to_card_s"] for o in outcomes),
              "median_read_s": median(o["read_s"] for o in outcomes),
              "median_check_s": median(o["check_s"] for o in outcomes),
              "pages_timed": sum(o["load_to_card_s"] is not None for o in outcomes)}
    na_types = [{"type": t, **{k: v[k] for k in ("reason", "pages", "planned")}} for t, v in per_type.items() if v["status"] == "n/a"]
    return {"per_type": per_type, "false_alarms": false_alarms, "exact_match": exact_match, "scam_detection": scam,
            "timing": timing, "na_types": na_types}


def scored_in(o: dict, per_type: dict) -> tuple[Optional[str], Optional[str]]:
    """(metric that scores this page, None) or (None, why no metric does). Same rules as score()."""
    t = per_type[o["trick"]]
    if t["status"] == "n/a":
        return None, f"n/a ({t['reason']})"
    if o["trick"] in DEAL_TYPES and o["expected"] == 1:
        return "trick_detection", None
    if is_clean(o):
        return "false_alarms", None
    if o["trick"] == SCAM:
        return "scam_detection", None
    return None, "no metric for this label"


def build_results(mode: str, outcomes: list[dict], settings: Optional[dict] = None, date: Optional[str] = None) -> dict:
    scored = score(outcomes, mode)
    pages = []
    for o in outcomes:
        metric, why = scored_in(o, scored["per_type"])
        pages.append({**o, "scored_in": metric, "excluded_reason": why})
    counts = Counter(p["scored_in"] for p in pages if p["scored_in"])
    accounting = {"labelled": len(pages), **{m: counts.get(m, 0) for m in METRICS},
                  "excluded": [{"page": p["page"], "trick": p["trick"], "expected": p["expected"], "reason": p["excluded_reason"]}
                               for p in pages if not p["scored_in"]]}
    return {"mode": mode, "mode_detail": MODES[mode],
            "date": date or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "notes": [CONFIRMED_NOTE, TRICK_NOTE, FALSE_ALARM_NOTE, TIMER_NOTE], "settings": settings or {},
            "pages_labelled": len(outcomes), "pages_planned": PLANNED_TOTAL, "accounting": accounting, **scored,
            "targets_reference_only": TARGETS, "pages": pages}


def write_results(results: dict, path: Path = RESULTS) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return path


def _secs(x: Optional[float]) -> str:
    return "-" if x is None else f"{x:.2f}"


def render(res: dict) -> str:
    fa, tm = res["false_alarms"], res["timing"]
    out = [f"MODE: {res['mode'].upper()} ({res['mode_detail']})"]
    out += [f"n/a: {n['type']}: n/a ({n['reason']}), {n['pages']} pages (plan expected {n['planned'] if n['planned'] is not None else '-'})"
            for n in res["na_types"]]
    left = f"; left out (type n/a): {', '.join(fa['left_out'])}" if fa["left_out"] else ""
    acc = res["accounting"]
    out += [f"{fa['clean_pages']} clean pages, plan expected {fa['planned']}; scored {fa['n']}{left}",
            f"{res['pages_labelled']} labelled pages, plan expected {res['pages_planned']}",
            f"Page accounting: {acc['labelled']} labelled = " + " + ".join(f"{acc[m]} in {m}" for m in METRICS)
            + f" + {len(acc['excluded'])} excluded",
            "Excluded pages: " + ("none" if not acc["excluded"] else ""),
            *(f"  {x['page']} ({x['trick']}, expected {x['expected']}): {x['reason']}" for x in acc["excluded"]),
            f"Date: {res['date']}  Settings: " + ", ".join(f"{k}={v}" for k, v in res["settings"].items())]
    out += [f"Note: {n}" for n in res["notes"]]
    rows = [("Metric", "Pages (plan)", "Result", "Target (reference only)")]
    for t, v in res["per_type"].items():
        if t in (CLEAN, LOOKALIKE):  # clean pages: the false-alarm row; look-alikes: the exact-match row
            continue
        result = fmt_rate(v["detection"]) if v["status"] == "scored" else f"n/a ({v['reason']})"
        pages = f"{v['pages']} ({v['planned']})" + (f": {v['planted']} planted, {v['honest']} honest" if "planted" in v else "")
        rows.append((f"Trick detection: {t}" if t != SCAM else f"Scam detection: {t}", pages,
                     result, TARGETS["scam_detection" if t == SCAM else "trick_detection"]))
    rows.append(("False alarms (clean pages)", f"{fa['clean_pages']} ({fa['planned']})", fmt_rate(fa), TARGETS["false_alarms"]))
    em = res["exact_match"]
    rows.append(("Exact-match accuracy (look-alikes)", f"{em['pages']} ({em['planned']})",
                 f"n/a ({em['reason']})" if em["status"] == "n/a" else fmt_rate(em), TARGETS["exact_match"]))
    rows.append(("Median s, page load -> final card", f"{tm['pages_timed']} timed",
                 f"{_secs(tm['median_load_to_card_s'])} s (read {_secs(tm['median_read_s'])}, check {_secs(tm['median_check_s'])})",
                 TARGETS["median_s"]))
    out += ["", *_table(rows), "", "Per page"]
    page_rows = [("Page", "Trick", "Exp", "Scored in", "Caught", "Deal flags", "Card", "Load->card s", "Reason")]
    for p in res["pages"]:
        card = f"{p['card']['tone']}/{p['card']['tricks']}" if p["card"] else "-"
        reason = p["reason"] if p["scored_in"] else p["excluded_reason"]
        page_rows.append((p["page"], p["trick"], str(p["expected"]), p["scored_in"] or "EXCLUDED", "yes" if p["caught"] else "no",
                          ",".join(p["deal_flags"]) or "-", card, _secs(p["load_to_card_s"]), reason or "-"))
    return "\n".join(out + _table(page_rows))


def _table(rows: list[tuple]) -> list[str]:
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]) - 1)]
    return ["  ".join([*(c.ljust(w) for c, w in zip(r, widths)), r[-1]]) for r in rows]


# ----------------------------------------------------------------------------- runner (headless Chromium, localhost only)


async def check_page(base: str, row: dict) -> dict:
    """Read one bench page with the real content script, then run it through the orchestrator. Never raises."""
    from backend import orchestrator, orchestrator_stub, stub_server
    from backend.contracts import PageFacts
    from backend.tests.content_reader import CONTENT_JS, FAKE_CHROME
    from backend.tools.browser import chromium

    url = f"{base}/bench/{row['page']}"
    try:
        async with chromium() as browser:  # the shared browser warm() started
            tab = await browser.new_page(viewport={"width": 1400, "height": 1000})
            try:
                await tab.add_init_script(FAKE_CHROME)
                t0 = time.perf_counter()
                await tab.goto(url, wait_until="load")
                await tab.add_script_tag(content=CONTENT_JS.read_text(encoding="utf-8"))
                facts = PageFacts(**await tab.evaluate("window.__priciseRead()"))
            finally:
                await tab.close()
        t_read = time.perf_counter()
        cid = f"bench-{Path(row['page']).stem}-{uuid.uuid4().hex[:6]}"
        chk = {"check_id": cid, "url": url, "demo": False, "page": facts.model_dump(), "started": time.time(), "findings": [],
               "final_card": None, "fresh": True, "evidence_data": stub_server.real_evidence(facts)}
        done: set[str] = set()
        await orchestrator.clean_page(chk)
        seen, card, t_card = [], None, None
        async for ev in orchestrator.run_check(chk, orchestrator_stub.stub_agents(chk, done), None,
                                               build_card=orchestrator_stub.card_builder(chk, done),
                                               agent_timeout_s=orchestrator_stub._deal_timeout_s(), traces_dir=TRACES):
            if ev["kind"] == "finding":
                seen.append(ev["finding"])
            else:
                card, t_card = ev["card"], time.perf_counter()
    except Exception as err:  # the type only: a message could hold a path or a URL
        return page_outcome(row, [], error=type(err).__name__)
    if card is None:
        return page_outcome(row, final_findings(seen), error="no final card")
    return page_outcome(row, final_findings(seen), {"tone": card.tone, "tricks": card.tricks, "safe": card.safe.status},
                        read_s=t_read - t0, check_s=t_card - t_read)


def _sample_mode(deal) -> None:
    """Run-ahead and timer test off (their types are n/a in sample mode) and no model calls at all."""
    from backend.llm import meterless
    from backend.tools.common import ToolResult

    async def off(*_args, **_kw) -> ToolResult:
        return ToolResult()

    def no_model(*_args, **_kw):
        raise meterless.MeterlessError("sample mode: model calls are off")

    deal.check_runahead = deal.check_timer = off
    meterless.complete = meterless.fast = meterless.deep = no_model


def _sidecar_state(world_model) -> str:
    if world_model.base_url() is None:
        return "not used (WORLD_MODEL_URL is not loopback)"
    return "not reachable, findings deduplicated locally" if world_model._down_until else "reachable"


async def run(mode: str, out: Path) -> int:
    os.environ["AGENT_ORCH"] = "on"
    os.environ["AGENT_DEAL"] = "real"
    os.environ.setdefault("TIMER_GAP_S", "3")
    import logging

    from backend import stub_server  # noqa: F401  loads .env (shell values win) and the pricise log handler
    from backend.agents import deal
    from backend.engines import world_model
    from backend.tests.content_reader import serve_repo
    from backend.tools import browser

    logging.getLogger("pricise").setLevel(logging.WARNING)
    if mode == "sample":
        _sample_mode(deal)
    try:
        await browser.warm()
    except Exception as err:
        print(f"STOPPED: the hidden browser (Chromium) could not start ({type(err).__name__}). Nothing was scored and "
              "no results were written. Set PLAYWRIGHT_CHROMIUM_PATH to a Chromium executable, or run "
              "`python -m playwright install chromium`.", file=sys.stderr)
        return 2
    rows = load_labels()
    server, base = serve_repo()
    outcomes = []
    try:
        for i, row in enumerate(rows, 1):
            print(f"[{i}/{len(rows)}] {row['page']}", file=sys.stderr, flush=True)
            outcomes.append(await check_page(base, row))
    finally:
        server.shutdown()
        await browser.shutdown()
    settings = {"TIMER_GAP_S": os.environ["TIMER_GAP_S"], "sidecar": _sidecar_state(world_model), "pages": "one at a time"}
    results = build_results(mode, outcomes, settings)
    print(render(results))
    path = write_results(results, out)
    print(f"\nSaved {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name}")
    return 0


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Run every labelled bench page through the real pipeline and score it.")
    ap.add_argument("--sample", action="store_true", help="no run-ahead, timer test or model calls (Chromium reads pages only)")
    ap.add_argument("--out", type=Path, default=RESULTS, help="where to write results.json (default bench/results.json)")
    return ap.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    sys.exit(asyncio.run(run("sample" if args.sample else "full", args.out)))
