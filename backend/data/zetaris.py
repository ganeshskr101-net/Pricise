"""Zetaris REST client. Every Pricise read of Zetaris goes through run_sql().

POST {ZETARIS_REST_URL}/sql-editor/sqls/run with Bearer ZETARIS_API_KEY, X-Org-ID and a fresh UUID X-Request-ID.
Zetaris Lightning SQL only, one statement per call, no trailing semicolon. Reads ZETARIS_REST_URL, ZETARIS_ORG_ID and
ZETARIS_API_KEY from the environment or .env. Never logs or puts the key in an error message.

Zetaris returns {"headers": [...], "data": [[...]], ...} with every value as a string; run_sql gives back a list of
dicts keyed by header, values unchanged (callers convert types).
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
import uuid

from backend.env import load_dotenv

TIMEOUT_S = 8
DEFAULT_LIMIT = 1000


class ZetarisError(RuntimeError):
    """Any Zetaris failure: not configured, unreachable, timeout, HTTP error or unexpected reply."""


def _env(name: str) -> str:
    load_dotenv()
    v = os.environ.get(name, "")
    return "" if "replace" in v.lower() else v  # .env.example placeholders count as missing


def configured() -> bool:
    return all(_env(n) for n in ("ZETARIS_REST_URL", "ZETARIS_ORG_ID", "ZETARIS_API_KEY"))


def use_zetaris() -> bool:
    """DATA_SOURCE=zetaris reads Pricise data through Zetaris; anything else (default "fixture") uses backend/data/catalog.py."""
    load_dotenv()
    return os.environ.get("DATA_SOURCE", "fixture").strip().lower() == "zetaris"


def datasource() -> str:
    """The Zetaris datasource holding the Pricise tables (scripts/register_zetaris.py)."""
    return _env("ZETARIS_DATASOURCE") or "pricise_pg"


SAFE_LITERAL = re.compile(r"[A-Za-z0-9 ._:/-]*")


def lit(value: str) -> str:
    """A SQL string literal for values we built ourselves (keys, paths). Anything else is refused, so page text never reaches SQL."""
    if not SAFE_LITERAL.fullmatch(value):
        raise ValueError("unsafe value for a SQL literal")
    return f"'{value}'"


def _request(sql: str, limit: int) -> urllib.request.Request:
    body = json.dumps({"queryId": str(uuid.uuid4()), "sql": sql, "source": "SqlEditor", "limit": limit}).encode()
    return urllib.request.Request(
        _env("ZETARIS_REST_URL").rstrip("/") + "/sql-editor/sqls/run", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {_env('ZETARIS_API_KEY')}",
                 "X-Org-ID": _env("ZETARIS_ORG_ID"),
                 "X-Request-ID": str(uuid.uuid4())})


KEY_IN_URL = re.compile(r"(api_key=)[^&\s\"']+", re.I)


def redact(text: str) -> str:
    """A REST table's endpoint can carry an API key (api_key=...), and Zetaris repeats the endpoint in its errors."""
    return KEY_IN_URL.sub(r"\1***", text or "")


def _http_error(e: urllib.error.HTTPError) -> ZetarisError:
    detail = redact(" ".join(e.read(300).decode(errors="replace").split()))
    hint = {400: "bad request: check ZETARIS_ORG_ID and the SQL (Lightning SQL, one statement, no trailing ';')",
            401: "unauthorised: ZETARIS_API_KEY is wrong or expired (create a new one in the Zetaris GUI)",
            403: "forbidden: this key's user lacks the privilege for that statement"}.get(e.code, "request failed")
    return ZetarisError(f"Zetaris HTTP {e.code}, {hint}. {detail}".strip())


def run_sql(sql: str, limit: int = DEFAULT_LIMIT, timeout: float = TIMEOUT_S, keep_semicolons: bool = False
            ) -> list[dict[str, str]]:
    """Run one Lightning SQL statement and return its rows as dicts keyed by column name. Raises ZetarisError.
    timeout: 8 s for the product; setup scripts (scripts/zetaris_usl.py) allow longer for CREATE / ACTIVATE."""
    if not configured():
        raise ZetarisError("Zetaris not configured: set ZETARIS_REST_URL, ZETARIS_ORG_ID and ZETARIS_API_KEY in .env")
    sql = sql.strip() if keep_semicolons else sql.strip().rstrip(";").strip()  # COMPILE USL DDL keeps its ");" endings
    try:
        with urllib.request.urlopen(_request(sql, limit), timeout=timeout) as r:
            out = json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        raise _http_error(e) from None
    except TimeoutError:
        raise ZetarisError(f"Zetaris did not answer within {timeout:g}s") from None
    except (urllib.error.URLError, OSError) as e:
        raise ZetarisError(f"Cannot reach Zetaris at {_env('ZETARIS_REST_URL')}: {getattr(e, 'reason', e)}") from None
    except json.JSONDecodeError:
        raise ZetarisError("Zetaris replied with something that is not JSON") from None
    if not isinstance(out, dict) or not isinstance(out.get("data", []), list):
        raise ZetarisError(redact(f"Unexpected Zetaris reply shape (expected headers + data): {str(out)[:200]}"))
    headers = out.get("headers") or []
    return [dict(zip(headers, row)) for row in out.get("data") or []]
