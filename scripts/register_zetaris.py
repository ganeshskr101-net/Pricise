"""Registers the Pricise Postgres in Zetaris as the JDBC datasource pricise_pg (getting-started recipe syntax:
CREATE DATASOURCE ... DESCRIBE BY ... OPTIONS (jdbcdriver, jdbcurl, username, password), then REGISTER DATASOURCE TABLES).

Usage:
  .venv/bin/python scripts/register_zetaris.py            dry run: read-only checks + prints the statements (password masked)
  .venv/bin/python scripts/register_zetaris.py --yes      runs the statements one at a time, then proves it with a SELECT
Zetaris reaches the database from inside Docker, so the JDBC host is PRICISE_PG_ZETARIS_HOST (host.docker.internal).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.data.zetaris import ZetarisError, run_sql  # noqa: E402
from backend.env import load_dotenv  # noqa: E402

MASK = "********"
TABLES = ("stores", "products", "listings", "price_history", "card_offers", "wishlist", "findings")
PROOF = ("SELECT s.name, l.condition, l.price, l.shipping FROM {ds}.listings l "
         "JOIN {ds}.stores s ON s.store_id = l.store_id WHERE l.product_id = 'sony-wh1000xm5' ORDER BY s.name")


def settings() -> dict[str, str]:
    load_dotenv()
    e = os.environ
    return {"ds": e.get("ZETARIS_DATASOURCE", "pricise_pg"), "host": e.get("PRICISE_PG_ZETARIS_HOST", "host.docker.internal"),
            "port": e.get("PRICISE_PG_PORT", "5433"), "db": e.get("PRICISE_PG_DB", "pricise"),
            "user": e.get("PRICISE_PG_USER", "pricise"), "password": e.get("PRICISE_PG_PASSWORD", "")}


def _options(s: dict[str, str], password: str) -> str:
    return (f'jdbcdriver "org.postgresql.Driver", jdbcurl "jdbc:postgresql://{s["host"]}:{s["port"]}/{s["db"]}", '
            f'username "{s["user"]}", password "{password}"')


def statements(s: dict[str, str], password: str) -> list[str]:
    return [f'CREATE DATASOURCE {s["ds"]} DESCRIBE BY "Pricise Store Lab synthetic data" OPTIONS ({_options(s, password)}, fetchsize "1000")',
            f'REGISTER DATASOURCE TABLES ({", ".join(repr(t) for t in TABLES)}) FROM {s["ds"]}']


def main(argv: list[str]) -> int:
    s = settings()
    if not s["password"]:
        print("FAIL  PRICISE_PG_PASSWORD is not set in .env", file=sys.stderr)
        return 1
    if '"' in s["password"]:
        print('FAIL  PRICISE_PG_PASSWORD must not contain a double quote', file=sys.stderr)
        return 1
    real, shown = statements(s, s["password"]), statements(s, MASK)
    try:
        existing = [r.get("name", "").lower() for r in run_sql("SHOW DATASOURCES")]
        print(f"Datasource {s['ds']}: {'ALREADY REGISTERED' if s['ds'].lower() in existing else 'not registered yet'}")
        tables = run_sql(f"LIST DATABASE TABLES OPTIONS ({_options(s, s['password'])})")
        print(f"Zetaris can reach the Pricise Postgres; it sees {len(tables)} table(s): "
              + ", ".join(sorted(str(next(iter(t.values()), "")) for t in tables)))
    except ZetarisError as e:
        print(f"FAIL  read-only check: {e}".replace(s["password"], MASK), file=sys.stderr)
        return 1
    if "--yes" not in argv:
        print("\nDry run. These statements would run, one at a time:")
        for i, sql in enumerate(shown, 1):
            print(f"  {i}. {sql}")
        print("\nRun them with --yes.")
        return 0
    for sql, label in zip(real, shown):
        print(f"RUN   {label}")
        try:
            run_sql(sql)
        except ZetarisError as e:
            msg = str(e).replace(s["password"], MASK)
            if "already exist" in msg.lower():
                print("      already exists, kept as is")
                continue
            print(f"FAIL  {msg}", file=sys.stderr)
            return 1
        print("      ok")
    proof = PROOF.format(ds=s["ds"])
    print(f"\nPROOF {proof}")
    try:
        for row in run_sql(proof):
            print("      " + "  ".join(f"{k}={v}" for k, v in row.items()))
    except ZetarisError as e:
        print(f"FAIL  {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
