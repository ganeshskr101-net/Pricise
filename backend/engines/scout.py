"""Scout guard for untrusted text: page fields and model inputs are checked before any agent or model sees them.

The engine is Pricise's Scout Intent implementation in the engine sidecar (backend/engines/world_model_ts/scout/, the same
Node process as the World Model). Its injection patterns are data (world_model_ts/scout/packs/*.json), and this module
runs the SAME patterns locally:

- AGENT_ORCH=on and the sidecar answers: each verdict is the worse of the sidecar's and the local one (never weaker).
- AGENT_ORCH off, sidecar down or WORLD_MODEL_URL not loopback: local rules only, and ONE warning for the outage. The
  check is never skipped and nothing here raises. After a failure, new calls skip the sidecar for RETRY_AFTER_S.
- Sends only the texts to check. Replies and logs carry pattern ids and offsets, never the text, keys or tokens.
- Long texts are split into overlapping pieces, so every request stays under the sidecar's 64 KB body limit.

guard_page(page) is the ingest guard (backend/orchestrator.py): blocked fields are replaced by REMOVED, warned fields are
kept and flagged. The last gate before a model call is backend/llm/meterless.py, which calls check_texts().
"""
from __future__ import annotations

import copy
import json
import logging
import re
import time
import unicodedata
import urllib.request
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Sequence

from backend.engines import world_model

log = logging.getLogger("pricise.scout")

PACK_DIR = Path(__file__).resolve().parent / "world_model_ts" / "scout" / "packs"
PACKS = ("default", "adversarial-tight", "pricise")  # same list as server.ts
MODEL = "Scout guard (rules, no model call)"
REMOVED = "[removed by Pricise: text aimed at AI assistants]"
TIMEOUT_S = 2.0
RETRY_AFTER_S = 30.0
PIECE_CHARS = 8000
OVERLAP_CHARS = 300  # a pattern that crosses a piece boundary is still inside one piece
MAX_TEXTS = 64  # per request, as server.ts
MAX_BATCH_BYTES = 56 * 1024  # server.ts refuses bodies over 64 KB
_LEVELS = ("pass", "warn", "block")
_ZERO_WIDTH = re.compile("[​-‍⁠﻿­]")
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # no proxy, even if HTTP_PROXY is set
_warned = False
_down_until = 0.0


@dataclass(frozen=True)
class Verdict:
    level: str  # pass | warn | block
    ids: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    source: str = "local"  # local | sidecar

    @property
    def blocked(self) -> bool:
        return self.level == "block"


# ----------------------------------------------------------------------------- local rules (same data as guard.ts)


@lru_cache(maxsize=1)
def _rules() -> tuple[tuple[str, str, re.Pattern, str], ...]:
    """(id, kind, regex, level) for every pattern in PACKS; escalated warns become blocks, as in guard.ts."""
    packs = [json.loads((PACK_DIR / f"{name}.json").read_text(encoding="utf-8")) for name in PACKS]
    escalate = {pid for p in packs for pid in p.get("escalate", [])}
    out = []
    for p in packs:
        for r in p.get("patterns", []):
            flags = re.I | (re.M if "m" in r.get("flags", "") else 0)
            out.append((r["id"], r["kind"], re.compile(r["pattern"], flags), "block" if r["id"] in escalate else r["severity"]))
    return tuple(out)


def normalize(text: str) -> str:
    """What the patterns see: NFKC, zero-width characters removed, runs of spaces collapsed (as guard.ts)."""
    return re.sub(r"[ \t]+", " ", _ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", text)))


def local_check(text: str) -> Verdict:
    t = normalize(text or "")
    hits = [(pid, kind, level) for pid, kind, rx, level in _rules() if rx.search(t)]
    return Verdict(_worst(level for *_, level in hits), tuple(h[0] for h in hits), tuple(dict.fromkeys(h[1] for h in hits)))


def _worst(levels) -> str:
    return max(levels, key=_LEVELS.index, default="pass")


def _worse(a: Verdict, b: Verdict) -> Verdict:
    level = _worst((a.level, b.level))
    return Verdict(level, tuple(dict.fromkeys(a.ids + b.ids)), tuple(dict.fromkeys(a.kinds + b.kinds)), "sidecar")


# ----------------------------------------------------------------------------- public API


def check_text(text: str) -> Verdict:
    return check_texts([text])[0]


def check_texts(texts: Sequence[str]) -> list[Verdict]:
    """One verdict per text. Never raises, never skips: without the sidecar the local rules decide."""
    texts = [t if isinstance(t, str) else str(t or "") for t in texts]
    local = [local_check(t) for t in texts]
    url = _sidecar_url()
    if url is None or not texts:
        return local
    remote = _remote(url, texts)
    if remote is None:
        return local
    return [_worse(lo, re_) for lo, re_ in zip(local, remote)]


# ----------------------------------------------------------------------------- page ingest guard


@dataclass
class PageHit:
    field: str  # e.g. "signals[2].text"
    level: str
    kinds: tuple[str, ...]
    selector: Optional[str] = None


@dataclass
class PageGuard:
    page: Optional[dict]
    hits: list[PageHit] = field(default_factory=list)

    @property
    def removed(self) -> list[PageHit]:
        return [h for h in self.hits if h.level == "block"]


def page_fields(page: dict) -> list[tuple[str, tuple, str, Optional[str]]]:
    """(name, path, text, selector) for every text field that came from the store page."""
    out = []
    if isinstance(page.get("title"), str):
        out.append(("title", ("title",), page["title"], None))
    product = page.get("product") if isinstance(page.get("product"), dict) else {}
    for key in ("name", "brand", "model", "sku"):
        if isinstance(product.get(key), str):
            out.append((f"product.{key}", ("product", key), product[key], None))
    for i, s in enumerate(page.get("signals") or []):
        if isinstance(s, dict) and isinstance(s.get("text"), str):
            out.append((f"signals[{i}].text", ("signals", i, "text"), s["text"], s.get("selector")))
    return [f for f in out if f[2].strip()]


def guard_page(page: Optional[dict]) -> PageGuard:
    """A copy of the page with blocked texts replaced by REMOVED, plus one hit per blocked or warned field."""
    if not isinstance(page, dict):
        return PageGuard(page)
    fields = page_fields(page)
    if not fields:
        return PageGuard(page)
    clean = copy.deepcopy(page)
    hits = []
    for (name, path, _text, selector), v in zip(fields, check_texts([f[2] for f in fields])):
        if v.level == "pass":
            continue
        hits.append(PageHit(name, v.level, v.kinds, selector))
        if v.blocked:
            _set(clean, path, REMOVED)
    return PageGuard(clean, hits)


def _set(obj: Any, path: tuple, value: str) -> None:
    for key in path[:-1]:
        obj = obj[key]
    obj[path[-1]] = value


# ----------------------------------------------------------------------------- sidecar


def _sidecar_url() -> Optional[str]:
    """The sidecar to ask, or None (local rules only). Warns once when the sidecar is not used for a new reason."""
    if not world_model.enabled():
        _warn_once("AGENT_ORCH is off")
        return None
    url = world_model.base_url()
    if url is None:
        _warn_once("WORLD_MODEL_URL is not a loopback address")
        return None
    return url if time.monotonic() >= _down_until else None


def pieces(text: str) -> list[str]:
    out, i = [], 0
    while True:
        out.append(text[i:i + PIECE_CHARS])
        if i + PIECE_CHARS >= len(text):
            return out
        i += PIECE_CHARS - OVERLAP_CHARS


def _batches(items: list[str]) -> list[list[str]]:
    out: list[list[str]] = [[]]
    size = 0
    for item in items:
        n = len(json.dumps(item, ensure_ascii=False).encode("utf-8")) + 1
        if out[-1] and (size + n > MAX_BATCH_BYTES or len(out[-1]) >= MAX_TEXTS):
            out.append([])
            size = 0
        out[-1].append(item)
        size += n
    return out


def _remote(url: str, texts: list[str]) -> Optional[list[Verdict]]:
    owner, flat = [], []
    for i, t in enumerate(texts):
        for p in pieces(t):
            owner.append(i)
            flat.append(p)
    results: list[dict] = []
    try:
        for batch in _batches(flat):
            reply = _call(f"{url}/scout/guard", {"texts": batch, "surface": "page"})
            got = reply.get("results") if isinstance(reply, dict) else None
            if not isinstance(got, list) or len(got) != len(batch) or not all(_valid(r) for r in got):
                raise ValueError("unexpected scout reply")
            results.extend(got)
    except Exception as err:  # refused, timeout, HTTP error, bad JSON: local rules decide, the check goes on
        _down(type(err).__name__)
        return None
    _reachable()
    out = [Verdict("pass", source="sidecar") for _ in texts]
    for i, r in zip(owner, results):
        flags = r.get("flags") or []
        v = Verdict(r["level"], tuple(str(f.get("id")) for f in flags), tuple(str(f.get("kind")) for f in flags), "sidecar")
        out[i] = _worse(out[i], v)
    return out


def _valid(r: Any) -> bool:
    return isinstance(r, dict) and r.get("level") in _LEVELS and isinstance(r.get("flags", []), list) and all(
        isinstance(f, dict) for f in r.get("flags", []))


def _call(url: str, body: dict) -> Any:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
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
        log.warning("scout sidecar not used (%s); untrusted text is checked with the local rules", why)


def _reachable() -> None:
    global _warned
    _warned = False  # the next outage warns again
