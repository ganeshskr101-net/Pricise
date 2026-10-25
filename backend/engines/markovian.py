"""Markovian client: runs the wishlist watch's bounded chains on the engine sidecar (backend/engines/world_model_ts/
markovian/, the Meterless Markovian reference engine behind markovian/chains.ts).

SidecarChain is a backend.watch.Chain. The watch owns the checkpoints and the decisions; this only hands each chunk
(built from checkpoint facts) to the engine, which bounds the prompts and counts tokens. So a sidecar outage can never
lose a checkpoint: chunk() is called after the checkpoint is saved, and it NEVER raises. When the sidecar can't be
reached (or WORLD_MODEL_URL is not loopback), the rest of the chain runs locally: same days, same checkpoints, same
alerts, only without the engine's prompts and accounting. ONE warning per outage; the next chain tries the sidecar
again after RETRY_AFTER_S.

Each chain's engine prompts are kept in .prompts (step, estimated tokens) and its summary in .runs, for the measured run
(bench/run_watch.py). No prompt text is logged.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Optional

from backend.engines import world_model

log = logging.getLogger("pricise.markovian")

TIMEOUT_S = 5.0
RETRY_AFTER_S = 30.0
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # no proxy, even if HTTP_PROXY is set
_warned = False
_down_until = 0.0


class SidecarChain:
    def __init__(self) -> None:
        self.chain_id: Optional[str] = None
        self.prompts: list[dict] = []  # {chain, step, prompt_tokens, token_label, prompt}: the engine's prompts
        self.runs: list[dict] = []     # the engine's summary of each finished chain
        self.local_chunks = 0          # chunks that ran without the engine
        self._chains = 0

    # watch.Chain

    def start(self, goal: str, carryover: str) -> None:
        if self.chain_id:  # a chain left open (an earlier advance_to stopped): free its slot now
            self._call("DELETE", f"/markovian/chains/{self.chain_id}")
        self.chain_id = None
        self._chains += 1
        reply = self._call("POST", "/markovian/chains", {"goal": goal, "carryover": carryover})
        if reply is not None:
            self.chain_id = reply["chain_id"]
            self._record(reply)

    def chunk(self, text: str, done: bool) -> None:
        if self.chain_id is None:
            self.local_chunks += 1
            return
        reply = self._call("POST", f"/markovian/chains/{self.chain_id}/chunks", {"text": text})
        if reply is None:  # the sidecar went away mid-chain: the rest of this chain runs locally
            self.chain_id = None
            self.local_chunks += 1
            return
        self._record(reply)

    # internals

    def _record(self, reply: dict) -> None:
        if reply.get("done"):
            self.runs.append({k: reply.get(k) for k in ("status", "chunks", "chunk_tokens", "total_tokens_used",
                                                         "efficiency_percent", "token_label")})
            self.chain_id = None
        else:
            self.prompts.append({"chain": self._chains, "step": reply["step"], "prompt_tokens": reply["prompt_tokens"],
                                 "token_label": reply["token_label"], "prompt": reply["prompt"]})

    def _call(self, method: str, path: str, body: Optional[dict] = None) -> Optional[dict]:
        """The JSON reply, or None (sidecar down, refused, or not usable). Never raises."""
        url = _ready()
        if url is None:
            return None
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url + path, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with _OPENER.open(req, timeout=TIMEOUT_S) as resp:
                reply = json.loads(resp.read())
        except urllib.error.HTTPError as err:
            if method == "DELETE":
                return None
            why = "too many open chains" if err.code == 429 else f"HTTP {err.code}"
            if 400 <= err.code < 500 and err.code != 429:
                log.warning("markovian sidecar refused a chunk (%s); this chain continues locally", why)
                return None
            _down(why)
            return None
        except (OSError, ValueError) as err:  # refused, timeout, bad JSON
            _down(type(err).__name__)
            return None
        _reachable()
        return reply if isinstance(reply, dict) else None


def _ready() -> Optional[str]:
    url = world_model.base_url()
    if url is None:
        _warn_once("WORLD_MODEL_URL is not a loopback address")
        return None
    return None if time.monotonic() < _down_until else url


def _down(why: str) -> None:
    global _down_until
    _down_until = time.monotonic() + RETRY_AFTER_S
    _warn_once(why)


def _warn_once(why: str) -> None:
    global _warned
    if not _warned:
        _warned = True
        log.warning("markovian sidecar not used (%s); the watch runs its chunks locally, checkpoints are unaffected", why)


def _reachable() -> None:
    global _warned
    _warned = False  # the next outage warns again
