"""Domain age for a REAL store from public registration data (RDAP), never from the store's own site.

GET https://rdap.org/domain/<registrable domain> (rdap.org redirects to the registry, e.g. Verisign for .com) and read the
"registration" event. One lookup per domain per day (cached in memory). Read only, with a timeout; any failure gives None,
and the Trust Agent then says it couldn't check. TRUST_RDAP=off turns lookups off (the tests do; no network in tests).
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date
from typing import Optional

import httpx

log = logging.getLogger("pricise.trust")
RDAP_URL = "https://rdap.org/domain/{}"
TIMEOUT_S = min(float(os.getenv("TRUST_HTTP_TIMEOUT", "3")) + 2, 8.0)  # registry redirects take a little longer
CACHE_S = 86400
# Public suffixes with two labels that are common for shops (no dependency on a full public-suffix list).
TWO_LABEL_SUFFIXES = {"co.uk", "org.uk", "ac.uk", "com.au", "net.au", "co.nz", "co.jp", "co.in", "com.br", "com.mx",
                      "co.za", "com.sg", "com.tr", "com.ar", "co.kr", "com.cn", "com.hk", "com.tw"}

_cache: dict[str, tuple[float, Optional[date]]] = {}


def enabled() -> bool:
    return os.getenv("TRUST_RDAP", "on").strip().lower() not in ("off", "0", "false", "no")


def registrable(host: str) -> str:
    """www.amazon.com -> amazon.com; shop.example.co.uk -> example.co.uk."""
    labels = [x for x in (host or "").lower().split(":")[0].split(".") if x]
    if len(labels) >= 3 and ".".join(labels[-2:]) in TWO_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def rdap_url(domain: str) -> str:
    return RDAP_URL.format(domain)


async def registered_on(client: httpx.AsyncClient, domain: str) -> Optional[date]:
    """The domain's registration date, or None (lookup off, failed, or no registration event). Never raises."""
    if not enabled() or not domain or "." not in domain:
        return None
    hit = _cache.get(domain)
    if hit and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]
    try:
        r = await client.get(rdap_url(domain), timeout=TIMEOUT_S, follow_redirects=True,
                             headers={"Accept": "application/rdap+json"})
        data = r.json() if r.status_code == 200 else {}
    except Exception as err:  # noqa: BLE001 - network, JSON, or a test that forbids fetching: "couldn't check"
        log.info("rdap %s failed: %s", domain, type(err).__name__)
        return None
    found = None
    for ev in data.get("events") or []:
        if isinstance(ev, dict) and ev.get("eventAction") == "registration":
            try:
                found = date.fromisoformat(str(ev.get("eventDate"))[:10])
            except ValueError:
                pass
    _cache[domain] = (time.monotonic(), found)
    return found


def clear_cache() -> None:
    _cache.clear()
