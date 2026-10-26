"""Shared finding state: the orchestrator writes every finding version to the World Model sidecar and reads them back.

The sidecar is backend/engines/world_model_ts/server.ts (the Meterless World Model reference engine on 127.0.0.1).

- Used only when AGENT_ORCH=on (backend/orchestrator.py). With it unset nothing here runs.
- finding_key(f): a finding's identity = sha256 of check_id, agent, type and the sorted evidence kind:ref (not the summary).
- Session.record() returns True when the finding is new or its content changed (ts ignored). The sidecar decides when it is
  reachable; a local key map decides when it isn't, so a re-run step never repeats a finding either way.
- Sidecar unreachable: log ONE warning for the outage and carry on locally; nothing here raises. New checks skip the
  sidecar for RETRY_AFTER_S after a failure (Windows takes ~2 s to refuse a closed port; a check must not wait each time).
- Sends only finding JSON (Finding.model_dump()), never keys or tokens. WORLD_MODEL_URL must be a loopback URL; proxies are
  never used, so findings cannot leave this machine.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Optional

from backend.contracts import Finding

log = logging.getLogger("pricise.world_model")

DEFAULT_URL = "http://127.0.0.1:8788"
TIMEOUT_S = 2.0
RETRY_AFTER_S = 30.0
_LOOPBACK = {"127.0.0.1", "localhost"}
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # no proxy, even if HTTP_PROXY is set
_warned = False
_down_until = 0.0  # time.monotonic() before which new checks don't try the sidecar


def enabled() -> bool:
    return os.environ.get("AGENT_ORCH", "off").lower() == "on"


def finding_key(f: Finding) -> str:
    refs = sorted(f"{e.kind}:{e.ref}" for e in f.evidence)
    return hashlib.sha256(json.dumps([f.check_id, f.agent, f.type, refs]).encode("utf-8")).hexdigest()


def base_url() -> Optional[str]:
    """WORLD_MODEL_URL (default http://127.0.0.1:8788), or None when it is not a loopback address."""
    url = os.environ.get("WORLD_MODEL_URL", "").strip() or DEFAULT_URL
    return url.rstrip("/") if urllib.parse.urlsplit(url).hostname in _LOOPBACK else None


def _content(f: Finding) -> str:
    return json.dumps(f.model_dump(exclude={"ts"}), sort_keys=True)


def _call(method: str, url: str, body: Optional[dict] = None) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with _OPENER.open(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read())


def _down(why: str) -> None:
    global _down_until
    _down_until = time.monotonic() + RETRY_AFTER_S
    _warn_once(why)


def _warn_once(why: str) -> None:
    global _warned
    if not _warned:
        _warned = True
        log.warning("world model sidecar not used (%s); findings are deduplicated locally", why)


def _reachable() -> None:
    global _warned
    _warned = False  # the next outage warns again


class Session:
    """One check's view of the shared state. Goes local for the rest of the check after the first sidecar failure."""

    def __init__(self, check_id: str) -> None:
        self.check_id = check_id
        self.url = base_url()
        self.online = self.url is not None and time.monotonic() >= _down_until
        self._local: dict[str, str] = {}
        if self.url is None:
            _warn_once("WORLD_MODEL_URL is not a loopback address")

    async def record(self, key: str, f: Finding) -> bool:
        """Write one version of a finding; True when it is new or changed."""
        content = _content(f)
        changed = self._local.get(key) != content
        self._local[key] = content
        if self.online:
            body = {"key": key, "check_id": self.check_id, "finding": f.model_dump()}
            reply = await self._request("POST", "/findings", body)
            if reply is not None:
                changed = bool(reply.get("changed"))
        return changed

    async def findings(self) -> Optional[list[dict]]:
        """Every finding of this check as the sidecar holds it, in arrival order; None when it isn't reachable."""
        if not self.online:
            return None
        reply = await self._request("GET", f"/checks/{urllib.parse.quote(self.check_id, safe='')}/findings")
        return list(reply["findings"]) if isinstance(reply, dict) and isinstance(reply.get("findings"), list) else None

    async def _request(self, method: str, path: str, body: Optional[dict] = None) -> Optional[dict]:
        try:
            reply = await asyncio.to_thread(_call, method, f"{self.url}{path}", body)
        except Exception as err:  # refused, timeout, HTTP error, bad JSON: never fail the check
            self.online = False
            _down(type(err).__name__)
            return None
        _reachable()
        return reply if isinstance(reply, dict) else None
