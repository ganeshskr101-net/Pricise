"""One live Google Shopping search through SerpApi, saved as a test fixture (no key in it). Read only: no store site is loaded.

Usage: .venv/bin/python scripts/capture_serpapi.py "JBL Flip 6 Blue" backend/tests/fixtures/serpapi/jbl_flip_6_blue.json
The key comes from .env and is never printed; the saved file is checked for it before writing.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.env import load_dotenv  # noqa: E402
from backend.tools.shopping import SERPAPI_URL  # noqa: E402


def main(query: str, out: str) -> int:
    load_dotenv()
    key = os.environ.get("SERPAPI_KEY", "")
    r = httpx.get(SERPAPI_URL, timeout=15, params={"engine": "google_shopping", "q": query, "gl": "us", "hl": "en", "api_key": key})
    print(f"HTTP {r.status_code}")
    data = r.json()
    keep = {"search_parameters": {k: v for k, v in (data.get("search_parameters") or {}).items() if k != "api_key"},
            "shopping_results": data.get("shopping_results") or []}
    text = json.dumps(keep, indent=1)
    if key and key in text:
        raise SystemExit("key found in the response; not saved")
    Path(out).write_text(text + "\n")
    print(f"saved {out}: {len(keep['shopping_results'])} results")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))


def sellers(token: str, out: str) -> int:
    """The sellers list (google_immersive_product) of one search result, saved the same way."""
    load_dotenv()
    key = os.environ.get("SERPAPI_KEY", "")
    r = httpx.get(SERPAPI_URL, timeout=15, params={"engine": "google_immersive_product", "page_token": token, "api_key": key})
    print(f"HTTP {r.status_code}")
    pr = r.json().get("product_results") or {}
    keep = {"product_results": {"title": pr.get("title"), "stores": pr.get("stores") or []}}
    text = json.dumps(keep, indent=1)
    if key and key in text:
        raise SystemExit("key found in the response; not saved")
    Path(out).write_text(text + "\n")
    print(f"saved {out}: {len(keep['product_results']['stores'])} stores")
    return 0
