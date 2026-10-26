"""Fills the Pricise Postgres with the SYNTHETIC Store Lab catalog (backend/data/catalog.py). Safe to re-run: it drops and
recreates the Pricise tables (only in the Pricise Postgres on :5433, never in Zetaris's own postgres), so wishlist additions
and stored findings are reset too.

Usage: .venv/bin/python scripts/seed.py            (start the database first: docker compose -f docker-compose.pricise.yml --env-file .env up -d)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.data import catalog  # noqa: E402
from backend.data.pg import PgError, connect  # noqa: E402

SCHEMA = ROOT / "backend" / "data" / "schema.sql"
ORDER = ["stores", "products", "listings", "price_history", "card_offers", "wishlist", "findings"]  # parents first


def seed(conn) -> dict[str, int]:
    data = catalog.rows()
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS " + ", ".join(reversed(ORDER)) + " CASCADE")
        cur.execute(SCHEMA.read_text())
        for table in ORDER:
            rows = data[table]
            if not rows:
                continue
            cols = list(rows[0])
            sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
            cur.executemany(sql, [[r[c] for c in cols] for r in rows])
        counts = {}
        for table in ORDER:
            cur.execute(f"SELECT count(*) FROM {table}")
            counts[table] = cur.fetchone()[0]
    return counts


def main() -> int:
    try:
        with connect() as conn:
            counts = seed(conn)
    except PgError as e:
        print(f"FAIL  {e}", file=sys.stderr)
        return 1
    print("Seeded the Pricise Postgres with SYNTHETIC Store Lab data:")
    for table, n in counts.items():
        print(f"  {table:<14} {n:>5} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
