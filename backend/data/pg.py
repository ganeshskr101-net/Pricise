"""Direct connection to the Pricise Postgres (docker-compose.pricise.yml), used for seeding and for writes.

Reads go through Zetaris (backend/data/zetaris.py). Writes (wishlist, findings) come here: Zetaris can't UPDATE a JDBC
datasource and its INSERT ignores column defaults (see backend/data/repo.py).
Reads PRICISE_PG_HOST, PRICISE_PG_PORT, PRICISE_PG_DB, PRICISE_PG_USER, PRICISE_PG_PASSWORD from the environment or .env.
Never logs the password.
"""
from __future__ import annotations

import os

from backend.env import load_dotenv

TIMEOUT_S = 8
NAMES = ("PRICISE_PG_HOST", "PRICISE_PG_PORT", "PRICISE_PG_DB", "PRICISE_PG_USER", "PRICISE_PG_PASSWORD")


class PgError(RuntimeError):
    """Pricise Postgres not configured, not installed or not reachable."""


def configured() -> bool:
    load_dotenv()
    return all(os.environ.get(n) for n in NAMES)


def connect():
    """A psycopg connection (use as a context manager: commits on success). Raises PgError."""
    if not configured():
        raise PgError("Pricise Postgres not configured: set PRICISE_PG_* in .env (see .env.example)")
    try:
        import psycopg
    except ImportError:
        raise PgError("psycopg not installed: run scripts/setup.sh") from None
    try:
        return psycopg.connect(host=os.environ["PRICISE_PG_HOST"], port=int(os.environ["PRICISE_PG_PORT"]),
                               dbname=os.environ["PRICISE_PG_DB"], user=os.environ["PRICISE_PG_USER"],
                               password=os.environ["PRICISE_PG_PASSWORD"], connect_timeout=TIMEOUT_S,
                               options=f"-c statement_timeout={TIMEOUT_S * 1000}")
    except psycopg.Error as e:
        raise PgError(f"Cannot reach the Pricise Postgres at {os.environ['PRICISE_PG_HOST']}:{os.environ['PRICISE_PG_PORT']} "
                      f"({type(e).__name__}). Start it: docker compose -f docker-compose.pricise.yml --env-file .env up -d") from None
