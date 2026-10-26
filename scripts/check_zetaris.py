"""Read-only Zetaris connection check: runs SELECT 1 and SHOW LIGHTNING DATABASES through backend/data/zetaris.py.

Usage: .venv/bin/python scripts/check_zetaris.py      (prints results, never the API key; exit 1 on failure)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.data.zetaris import ZetarisError, run_sql  # noqa: E402


def main() -> int:
    for sql in ("SELECT 1 AS one", "SHOW LIGHTNING DATABASES"):
        t0 = time.monotonic()
        try:
            rows = run_sql(sql)
        except ZetarisError as e:
            print(f"FAIL  {sql}\n      {e}", file=sys.stderr)
            return 1
        print(f"OK    {sql}  ({len(rows)} row(s), {time.monotonic() - t0:.1f}s)")
        for row in rows:
            print("      " + "  ".join(f"{k}={v}" for k, v in row.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
