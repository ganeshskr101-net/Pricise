"""Shared pieces for the Trust Agent tools: the Signal type, HTTP helpers with timeouts, and Store Lab store discovery.

A Store Lab store is a folder served by the backend (e.g. /storelab/store-c/) holding the files Trust reads:
  about.json      {"name", "domain", "domain_created": "YYYY-MM-DD", "as_of": "YYYY-MM-DD", "address", "company_number"}
  returns.html    returns-policy page (missing = no policy)
  listings.json   [{"listing_id", "product_id", "image_url", "price"}]   (image_url may be relative to the store)
  reviews.json    [{"product_id", "date": "YYYY-MM-DD", "rating", "text"}]
A dynamic Store Lab server may serve /returns, /api/listings, /api/reviews instead; both are tried.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

ROOT = Path(__file__).resolve().parents[2]
HTTP_TIMEOUT_S = min(float(os.getenv("TRUST_HTTP_TIMEOUT", "3")), 8.0)  # AGENTS.md: max 8 s per network call

LISTINGS_PATHS = ("/listings.json", "/api/listings")
REVIEWS_PATHS = ("/reviews.json", "/api/reviews")
RETURNS_PATHS = ("/returns.html", "/returns")

Strength = Literal["strong", "weak"]


@dataclass
class Signal:
    """One trust check. `flagged` = a warning sign was found; `verified` = the check could run at all."""
    name: str            # finding type, e.g. "domain_age"
    strength: Strength
    flagged: bool
    verified: bool
    reason: str          # one plain-English sentence for the panel
    evidence: list[dict[str, Any]] = field(default_factory=list)  # contracts.Evidence dicts
    confidence: float = 0.9


def as_of_date(about: Optional[dict] = None) -> date:
    """'Today' for domain age. Store Lab stores pin it in about.json ("as_of", the date of the made-up WHOIS snapshot) so the
    demo always says "6 days"; TRUST_AS_OF=YYYY-MM-DD overrides; otherwise the real date."""
    for raw in (os.getenv("TRUST_AS_OF"), (about or {}).get("as_of")):
        if raw:
            try:
                return date.fromisoformat(str(raw)[:10])
            except ValueError:
                pass
    return datetime.now(timezone.utc).date()


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT_S, follow_redirects=True)


async def get_json(client: httpx.AsyncClient, url: str) -> Any | None:
    """GET a JSON document; None on any network error, non-200 or bad JSON. Never raises."""
    try:
        r = await client.get(url, timeout=HTTP_TIMEOUT_S)
        return r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


async def get_text(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        r = await client.get(url, timeout=HTTP_TIMEOUT_S)
        return r.text if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


async def get_bytes(client: httpx.AsyncClient, url: str) -> bytes | None:
    try:
        r = await client.get(url, timeout=HTTP_TIMEOUT_S)
        return r.content if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


async def first_json(client: httpx.AsyncClient, base: str, paths: tuple[str, ...]) -> tuple[Any | None, str]:
    """The first of `paths` under `base` that returns JSON, and its URL (the first path's URL if none do)."""
    for p in paths:
        data = await get_json(client, base + p)
        if data is not None:
            return data, base + p
    return None, base + paths[0]


async def first_text(client: httpx.AsyncClient, base: str, paths: tuple[str, ...]) -> tuple[str | None, str]:
    for p in paths:
        text = await get_text(client, base + p)
        if text is not None:
            return text, base + p
    return None, base + paths[0]


def candidate_bases(url: str) -> list[str]:
    """Path prefixes of `url`, longest first, so the store root is found whatever page the check starts from:
    http://h/storelab/store-c/ -> [http://h/storelab/store-c, http://h/storelab, http://h]."""
    parts = urlsplit(url)
    segs = [s for s in parts.path.split("/") if s]
    if segs and "." in segs[-1]:  # drop a file name like index.html
        segs = segs[:-1]
    return [urlunsplit((parts.scheme, parts.netloc, "/" + "/".join(segs[:n]) if n else "", "", ""))
            for n in range(len(segs), -1, -1)]


async def find_store_base(client: httpx.AsyncClient, url: str) -> tuple[str, dict] | None:
    """Find the store root by locating its about.json. Returns (base_url, about) or None if the store doesn't answer."""
    for base in candidate_bases(url):
        about = await get_json(client, base + "/about.json")
        if isinstance(about, dict):
            return base, about
    return None


def sibling_stores(base: str) -> list[str]:
    """Other stores in the same folder as this one, found on disk (the backend serves the repo's storelab/ and bench/
    folders at the same paths). http://h/storelab/store-d -> [http://h/storelab/audiodealz, http://h/storelab/store-c, ...]."""
    parts = urlsplit(base)
    segs = [s for s in parts.path.split("/") if s]
    if len(segs) < 2:
        return []
    parent = ROOT.joinpath(*segs[:-1])
    if not parent.is_dir() or ROOT not in parent.resolve().parents:
        return []
    prefix = urlunsplit((parts.scheme, parts.netloc, "/" + "/".join(segs[:-1]), "", ""))
    return [f"{prefix}/{d.name}" for d in sorted(parent.iterdir())
            if d.is_dir() and (d / "about.json").exists() and d.name != segs[-1]]


def resolve(base: str, ref: str) -> str:
    """Resolve an image or link reference against the store root."""
    return ref if ref.startswith(("http://", "https://")) else urljoin(base.rstrip("/") + "/", ref.lstrip("/"))
