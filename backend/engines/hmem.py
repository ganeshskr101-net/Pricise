"""H-MEM client: the shopper's notes (preferences, card or offer names, trusted or blocked stores), kept by the engine
sidecar (backend/engines/world_model_ts/hmem/, the Meterless H-MEM reference engine behind hmem/pricise.ts).

Privacy rules, enforced here AND again in the sidecar:
- Only notes the user typed are stored (origin "user_typed"). Nothing in Pricise writes page text or model output here.
- policy_problem() rejects card numbers, card security codes, keys, tokens, passwords, any value from .env or from a
  secret-named environment variable, long or multi-line text (prompts), and any text the Scout guard flags (pass only).
  A rejection raises NoteRejected with the rule that failed; the text is never logged.
- delete_note() and forget_all() really remove: the sidecar drops the note from disk and rebuilds its engine; the local
  outbox and snapshot drop it too.
- Storage is local only: <repo>/.pricise-data/memory/ (HMEM_DATA_DIR to move it), git-ignored.

Sidecar down (or WORLD_MODEL_URL not loopback): a note is never lost silently. Writes go to outbox.jsonl (fsynced) and
are replayed in order, by id (so never twice), on the next call that reaches the sidecar. list_notes() then answers from
the last snapshot plus the outbox; query() matches keywords locally. ONE warning per outage.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

from backend.engines import scout, world_model

log = logging.getLogger("pricise.hmem")

ROOT = Path(__file__).resolve().parents[2]
KINDS = ("preference", "card_offer", "store")
TRUST = ("trusted", "blocked")
ORIGINS = ("user_typed",)  # Scout Phase 1B adds user-approved notes
MAX_TEXT = 200
TIMEOUT_S = 2.0
RETRY_AFTER_S = 30.0
ID_RE = re.compile(r"^n-[0-9a-f]{16}$")
STORE_RE = re.compile(r"^(?=.{3,253}$)[a-z0-9-]+(\.[a-z0-9-]+)+$")
_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL", re.I)
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # no proxy, even if HTTP_PROXY is set
_warned = False
_down_until = 0.0

# Same rules as hmem/pricise.ts policy().
_SECRET_RES = (
    ("api key", re.compile(r"\b(sk|pk|rk)[-_][A-Za-z0-9_-]{12,}")),
    ("api key", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("api key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("api key", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("token", re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{12,}", re.I)),
    ("secret", re.compile(r"\b(password|passwd|pwd|secret|token|api[ _-]?key)\s*[:=]\s*\S{4,}", re.I)),
    ("card security code", re.compile(r"\b(cvv|cvc|cvv2|cid|security code)\b\W{0,3}\d{3,4}\b", re.I)),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)


class NoteRejected(ValueError):
    """The note breaks a privacy rule. The message names the rule, never the text."""


# ----------------------------------------------------------------------------- policy


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


def _high_entropy(word: str) -> bool:
    if len(word) < 24 or not re.fullmatch(r"[A-Za-z0-9_+/=-]+", word):
        return False
    classes = sum(bool(re.search(p, word)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]"))
    return classes >= 3 or (classes >= 2 and len(word) >= 32)


def _env_file() -> Path:
    return ROOT / ".env"


def _secret_values() -> list[str]:
    """Values that must never be stored: every .env value and every secret-named environment variable (6+ chars)."""
    values = []
    try:
        for line in _env_file().read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                values.append(line.split("=", 1)[1].split(" #", 1)[0].strip().strip("'\""))
    except OSError:
        pass
    values += [v for k, v in os.environ.items() if _SECRET_NAME.search(k)]
    return [v for v in values if len(v) >= 6]


def policy_problem(text: Any) -> Optional[str]:
    """Why this text may not be stored, or None."""
    if not isinstance(text, str) or not text.strip():
        return "text is empty"
    if len(text) > MAX_TEXT:
        return f"text is longer than {MAX_TEXT} characters"
    if re.search(r"[\r\n\x00-\x1f\x7f]", text):
        return "text must be one line without control characters"
    for m in re.finditer(r"\d(?:[ -]?\d){12,18}", text):
        if _luhn(re.sub(r"\D", "", m.group(0))):
            return "looks like a card number"
    if re.search(r"\d{12,}", re.sub(r"[ -]", "", text)):
        return "contains a long number (card or account numbers are not stored)"
    for what, rx in _SECRET_RES:
        if rx.search(text):
            return f"looks like a {what}"
    if any(_high_entropy(w) for w in text.split()):
        return "looks like a key or token"
    if any(v in text for v in _secret_values()):
        return "contains a value from .env or a secret setting"
    level = scout.check_text(text).level
    if level != "pass":
        return f"the Scout guard flagged this text ({level})"
    return None


def check_note(note: dict) -> None:
    """Raises NoteRejected unless the note is acceptable (same rules as the sidecar's badNote())."""
    if note.get("origin") not in ORIGINS:
        raise NoteRejected("only notes the user typed are stored (origin user_typed)")
    if note.get("kind") not in KINDS:
        raise NoteRejected("kind must be preference, card_offer or store")
    if note["kind"] == "store":
        if not isinstance(note.get("store"), str) or not STORE_RE.match(note["store"]):
            raise NoteRejected("a store note needs a store domain")
        if note.get("trust") not in TRUST:
            raise NoteRejected("a store note needs trust: trusted or blocked")
    elif note.get("store") is not None or note.get("trust") is not None:
        raise NoteRejected("store and trust are for store notes only")
    problem = policy_problem(note.get("text"))
    if problem:
        raise NoteRejected(problem)


# ----------------------------------------------------------------------------- API


def add_note(kind: str, text: str, *, store: Optional[str] = None, trust: Optional[str] = None,
             origin: str = "user_typed") -> dict:
    """Store one note the user typed. Returns the note ("pending": True while it waits in the outbox)."""
    note = {"id": "n-" + secrets.token_hex(8), "kind": kind, "text": text, "origin": origin,
            **({"store": store.lower() if isinstance(store, str) else store, "trust": trust} if kind == "store" else {})}
    if kind != "store" and (store is not None or trust is not None):
        note.update(store=store, trust=trust)  # so check_note() rejects it
    check_note(note)
    url = _ready()
    if url is not None:
        reply = _request("PUT", f"{url}/hmem/notes/{note['id']}", note)
        if reply is not None:
            _refresh_snapshot(url)
            return reply["note"]
    _append_outbox({"op": "put", "note": note})
    return {**note, "pending": True}


def list_notes() -> list[dict]:
    url = _ready()
    if url is not None:
        reply = _request("GET", f"{url}/hmem/notes")
        if reply is not None:
            _write_json(_snapshot_file(), {"notes": reply["notes"]})
            return reply["notes"]
    return _local_view()


def delete_note(note_id: str) -> bool:
    if not ID_RE.match(str(note_id)):
        return False
    known = any(n["id"] == note_id for n in _local_view())
    _drop_local(note_id)  # the text leaves the outbox and the snapshot now, whatever happens next
    url = _ready()
    if url is not None:
        reply = _request("DELETE", f"{url}/hmem/notes/{note_id}")
        if reply is not None:
            return bool(reply["deleted"]) or known
    _append_outbox({"op": "delete", "id": note_id})
    return known


def forget_all() -> int:
    """Delete every note. Returns how many were known locally or in the sidecar."""
    known = len(_local_view())
    for f in (_outbox_file(), _snapshot_file()):
        f.unlink(missing_ok=True)
    url = _ready()
    if url is not None:
        reply = _request("POST", f"{url}/hmem/forget", {})
        if reply is not None:
            return max(int(reply["deleted"]), known)
    _append_outbox({"op": "forget"})
    return known


def query(text: str, top: int = 5) -> list[dict]:
    """The notes most related to text: [{note, relevance}]. Sidecar down: keyword overlap on the local view."""
    url = _ready()
    if url is not None:
        reply = _request("POST", f"{url}/hmem/query", {"query": text[:500], "top": max(1, min(top, 20))})
        if reply is not None:
            return reply["results"]
    words = set(re.findall(r"[a-z0-9]{2,}", text.lower()))
    scored = []
    for n in _local_view():
        overlap = len(words & set(re.findall(r"[a-z0-9]{2,}", n["text"].lower())))
        if overlap:
            scored.append({"note": n, "relevance": overlap / math.sqrt(len(words) or 1)})
    return sorted(scored, key=lambda r: -r["relevance"])[:top]


# ----------------------------------------------------------------------------- sidecar


def _ready() -> Optional[str]:
    """The sidecar URL when it should be tried, after replaying the outbox; None = use the local fallback."""
    url = world_model.base_url()
    if url is None:
        _warn_once("WORLD_MODEL_URL is not a loopback address")
        return None
    if time.monotonic() < _down_until:
        return None
    return url if _replay(url) else None


def _request(method: str, url: str, body: Optional[dict] = None) -> Optional[dict]:
    """The JSON reply, or None when the sidecar can't be reached. A refusal (4xx) raises NoteRejected."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with _OPENER.open(req, timeout=TIMEOUT_S) as resp:
            reply = json.loads(resp.read())
    except urllib.error.HTTPError as err:
        if 400 <= err.code < 500:
            try:
                why = json.loads(err.read()).get("error", "")
            except ValueError:
                why = ""
            raise NoteRejected(f"the memory sidecar refused it: {why or err.code}") from None
        _down(f"HTTP {err.code}")
        return None
    except (OSError, ValueError) as err:  # refused, timeout, bad JSON
        _down(type(err).__name__)
        return None
    _reachable()
    return reply if isinstance(reply, dict) else None


def _replay(url: str) -> bool:
    """Send the outbox to the sidecar in order. True when it is empty afterwards (the sidecar is usable)."""
    ops = _read_outbox()
    if not ops:
        return True
    for i, op in enumerate(ops):
        try:
            if op["op"] == "put":
                ok = _request("PUT", f"{url}/hmem/notes/{op['note']['id']}", op["note"]) is not None
            elif op["op"] == "delete":
                ok = _request("DELETE", f"{url}/hmem/notes/{op['id']}") is not None
            else:
                ok = _request("POST", f"{url}/hmem/forget", {}) is not None
        except NoteRejected as err:  # e.g. the sidecar's rules are stricter: drop that note, say so once
            log.warning("memory: a waiting note was not stored by the sidecar (%s)", str(err)[:120])
            ok = True
        if not ok:
            _write_outbox(ops[i:])
            return False
    _outbox_file().unlink(missing_ok=True)
    _refresh_snapshot(url)
    return True


def _refresh_snapshot(url: str) -> None:
    reply = _request("GET", f"{url}/hmem/notes")
    if reply is not None:
        _write_json(_snapshot_file(), {"notes": reply["notes"]})


def _down(why: str) -> None:
    global _down_until
    _down_until = time.monotonic() + RETRY_AFTER_S
    _warn_once(why)


def _warn_once(why: str) -> None:
    global _warned
    if not _warned:
        _warned = True
        log.warning("memory sidecar not used (%s); notes are kept in the local outbox and sent when it is back", why)


def _reachable() -> None:
    global _warned
    _warned = False  # the next outage warns again


# ----------------------------------------------------------------------------- local files (outbox, snapshot)


def data_dir() -> Path:
    return Path(os.environ.get("HMEM_DATA_DIR", "").strip() or ROOT / ".pricise-data" / "memory")


def _outbox_file() -> Path:
    return data_dir() / "outbox.jsonl"


def _snapshot_file() -> Path:
    return data_dir() / "snapshot.json"


def _read_outbox() -> list[dict]:
    try:
        lines = _outbox_file().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [json.loads(line) for line in lines if line.strip()]


def _append_outbox(op: dict) -> None:
    path = _outbox_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(op) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _write_outbox(ops: list[dict]) -> None:
    if ops:
        _write_atomic(_outbox_file(), "".join(json.dumps(op) + "\n" for op in ops))
    else:
        _outbox_file().unlink(missing_ok=True)


def _write_json(path: Path, data: dict) -> None:
    _write_atomic(path, json.dumps(data, indent=2) + "\n")


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _local_view() -> list[dict]:
    """The snapshot with the outbox applied: what the user has, as far as this process knows."""
    try:
        notes = {n["id"]: n for n in json.loads(_snapshot_file().read_text(encoding="utf-8"))["notes"]}
    except (OSError, ValueError, KeyError):
        notes = {}
    for op in _read_outbox():
        if op["op"] == "put":
            notes[op["note"]["id"]] = {**op["note"], "pending": True}
        elif op["op"] == "delete":
            notes.pop(op["id"], None)
        else:
            notes.clear()
    return list(notes.values())


def _drop_local(note_id: str) -> None:
    ops = _read_outbox()
    kept = [op for op in ops if not (op["op"] == "put" and op["note"]["id"] == note_id)]
    if kept != ops:
        _write_outbox(kept)
    try:
        snap = json.loads(_snapshot_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if any(n["id"] == note_id for n in snap.get("notes", [])):
        _write_json(_snapshot_file(), {"notes": [n for n in snap["notes"] if n["id"] != note_id]})
