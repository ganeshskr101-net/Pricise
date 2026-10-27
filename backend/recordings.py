"""Recorded real runs, for sample mode. A recording is one finished full-mode check of a REAL product page: the PageFacts
the extension read in the shopper's Chrome, every finding as it streamed (including the Skeptic's decisions and the model
wording they carried), the final card and the evidence-page data (with the Google Shopping offers the Price Agent kept and
dropped). Nothing in it is typed in by hand.

    record(base, check_id, name)   GET <base>/check/<id> from a running full-mode backend -> samples/<name>.json
    match(url)                     the recording for a page URL (Amazon: by ASIN; else the URL without query or fragment)

With SAMPLE_MODE=true the backend replays a matching recording instead of running agents (no Zetaris, SerpApi, RDAP or
model call), and labels the card "recorded from a real run on <date>". Pages without a recording get the scripted stub.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"
ASIN_RE = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})")


def page_key(url: str) -> str:
    u = urlsplit(url or "")
    host = (u.hostname or "").removeprefix("www.")
    m = ASIN_RE.search(u.path)
    return f"{host}/dp/{m[1]}" if m and host.startswith("amazon.") else f"{host}{u.path.rstrip('/')}"


def load_all() -> list[dict]:
    out = []
    for path in sorted(SAMPLES_DIR.glob("*.json")):
        try:
            out.append({**json.loads(path.read_text()), "file": path.name})
        except (OSError, ValueError):
            continue
    return out


def match(url: str) -> Optional[dict]:
    key = page_key(url)
    return next((r for r in load_all() if page_key(r.get("url", "")) == key), None)


def label(rec: dict) -> str:
    from backend.tools.common import is_storelab

    when = time.strftime("%-d %b %Y", time.strptime(rec["recorded_at"][:10], "%Y-%m-%d"))
    if is_storelab(rec.get("url", "")):  # Store Lab is made up by us: say so, even though the agents ran for real
        return f"recorded from the real agents on a synthetic test store on {when}"
    return f"recorded from a real run on {when}"


def from_check(chk: dict, name: str) -> dict:
    """A recording from GET /check/<id>: the page, the findings in the order they streamed, the final card."""
    if not chk.get("final_card"):
        raise ValueError("that check has not finished")
    events = [{"kind": "finding", "finding": f} for f in chk.get("findings") or []]
    events.append({"kind": "final_card", "card": chk["final_card"]})
    return {"name": name, "recorded_at": time.strftime("%Y-%m-%d"), "url": chk["url"], "page": chk["page"],
            "events": events, "evidence_data": chk.get("evidence_data") or {},
            "note": "Recorded from a real full-mode check (agents, Google Shopping via SerpApi, Nemotron wording). "
                    "Replayed as is with SAMPLE_MODE=true."}


def record(base: str, check_id: str, name: str) -> Path:
    with urllib.request.urlopen(f"{base.rstrip('/')}/check/{check_id}", timeout=10) as r:
        chk = json.loads(r.read())
    rec = from_check(chk, name)
    SAMPLES_DIR.mkdir(exist_ok=True)
    path = SAMPLES_DIR / f"{re.sub(r'[^a-z0-9_-]', '_', name.lower())}.json"
    path.write_text(json.dumps(rec, indent=1) + "\n")
    return path
