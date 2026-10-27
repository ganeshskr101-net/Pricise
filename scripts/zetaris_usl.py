"""Run the Pricise USL setup (docs/zetaris/pricise_usl.sql) and the saved queries against Zetaris, one statement at a time.

    .venv/bin/python scripts/zetaris_usl.py show                 # every statement, numbered, key masked (runs nothing)
    .venv/bin/python scripts/zetaris_usl.py run 3                # run statement 3 only
    .venv/bin/python scripts/zetaris_usl.py run 3 7              # run statements 3..7, stopping at the first error
    .venv/bin/python scripts/zetaris_usl.py query docs/zetaris/queries/usl.sql   # read-only queries -> results JSON

<SERPAPI_KEY> is replaced from .env only in the request sent to Zetaris; it is never printed or saved (errors are masked
too, backend/data/zetaris.redact). Setup statements get 90 s (the product's own queries keep their 8 s limit).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.data import zetaris  # noqa: E402
from backend.env import load_dotenv  # noqa: E402

SQL_FILE = ROOT / "docs" / "zetaris" / "pricise_usl.sql"
RESULTS = ROOT / "docs" / "zetaris" / "results"
KEY = "<SERPAPI_KEY>"
SETUP_TIMEOUT_S = 90


def statements(text: str) -> list[str]:
    """Statements in file order: ";"-terminated, or a "-- @one" ... "-- @end" block. Comment lines are left out."""
    out, buf, block = [], [], False
    for line in text.splitlines():
        s = line.strip()
        if s == "-- @one":
            block, buf = True, []
            continue
        if s == "-- @end":
            out.append("\n".join(buf).strip())
            block, buf = False, []
            continue
        if block:
            buf.append(line)
            continue
        if not s or s.startswith("--") or s == ";":
            continue
        buf.append(line)
        if s.endswith(";"):
            out.append("\n".join(buf).strip().rstrip(";").strip())
            buf = []
    return out


def named_queries(text: str) -> list[tuple[str, str]]:
    """A query file: "-- name: <id>" then one SELECT ending with ";"."""
    out, name, buf = [], None, []
    for line in text.splitlines():
        m = re.match(r"\s*--\s*name:\s*(\S+)", line)
        if m:
            name, buf = m[1], []
            continue
        if name and line.strip() and not line.strip().startswith("--"):
            buf.append(line)
            if line.strip().endswith(";"):
                out.append((name, "\n".join(buf).strip().rstrip(";")))
                name, buf = None, []
    return out


def masked(sql: str) -> str:
    return zetaris.redact(sql).replace(KEY, "<SERPAPI_KEY masked>")


def _with_key(sql: str) -> str:
    if KEY not in sql:
        return sql
    load_dotenv()
    key = os.environ.get("SERPAPI_KEY", "")
    if not key or "replace" in key.lower():
        raise SystemExit("SERPAPI_KEY is not set in .env")
    return sql.replace(KEY, key)


def show() -> None:
    for i, sql in enumerate(statements(SQL_FILE.read_text()), 1):
        print(f"-- [{i}]\n{masked(sql)};\n")


def run(first: int, last: int) -> int:
    stmts = statements(SQL_FILE.read_text())
    for i in range(first, last + 1):
        sql = stmts[i - 1]
        head = " ".join(masked(sql).split())[:110]
        t0 = time.time()
        try:
            rows = zetaris.run_sql(_with_key(sql), timeout=SETUP_TIMEOUT_S, keep_semicolons=sql.startswith("COMPILE USL"))
        except zetaris.ZetarisError as e:
            print(f"[{i}] FAILED after {time.time() - t0:.1f}s: {head}\n    {zetaris.redact(str(e))[:600]}")
            return 1
        print(f"[{i}] ok in {time.time() - t0:.1f}s ({len(rows)} rows): {head}")
        for r in rows[:5]:
            print("    ", zetaris.redact(json.dumps(r))[:300])
    return 0


def query(path: str) -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, sql in named_queries(Path(path).read_text()):
        t0 = time.time()
        try:
            rows = zetaris.run_sql(sql, timeout=SETUP_TIMEOUT_S)
            out[name] = {"sql": sql, "seconds": round(time.time() - t0, 1), "rows": rows}
            print(f"-- {name}: {len(rows)} rows in {time.time() - t0:.1f}s")
            for r in rows[:12]:
                print("   ", zetaris.redact(json.dumps(r))[:240])
        except zetaris.ZetarisError as e:
            out[name] = {"sql": sql, "error": zetaris.redact(str(e))[:600]}
            print(f"-- {name}: FAILED {zetaris.redact(str(e))[:400]}")
    target = RESULTS / (Path(path).stem + ".json")
    text = zetaris.redact(json.dumps(out, indent=1))
    target.write_text(text + "\n")
    print(f"saved {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "show":
        show()
    elif args[0] == "run":
        a = int(args[1])
        raise SystemExit(run(a, int(args[2]) if len(args) > 2 else a))
    elif args[0] == "query":
        raise SystemExit(query(args[1]))
    else:
        raise SystemExit(__doc__)
