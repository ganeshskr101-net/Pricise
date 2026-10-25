"""Wishlist watch: the long-running job. For each watched item it checks the price day by day, decides when a drop is
real, and sends ONE alert. Not wired into the backend yet (stub_server's /demo/clock and /wishlist are Phase 1B).

Decisions are rules, no model:
- An offer counts only if: the item has a watch rule (a target price; no target = watched, never alerted), the price is
  at or under the target, the offer is not held by Pricise (fake discount, unsafe store...), "new only" rules need
  condition new, "trusted only" rules need store_trust "safe".
- A drop is real after CONFIRM_DAYS checks in a row that count (a one-day glitch never alerts).
- One alert per item: the checkpoint records it as sent BEFORE advance_to() returns it (at-most-once: a crash in between
  loses that notification, never duplicates it; the item still shows the alert as sent).

Bounded steps: advance_to(day) processes the missed days one day per chunk, at most MAX_DAYS_PER_CHAIN chunks per chain;
a long gap (the computer was asleep, the demo clock jumped) becomes several chains. The checkpoint is saved after every
chunk, so a restart continues from the last saved day. Each chunk's carryover is rendered from the checkpoint only.

The checkpoint (.pricise-data/watch/checkpoint.json, git-ignored, WATCH_DATA_DIR to move it) holds structured facts
only: numbers, booleans, ids and enum values. It is written atomically (temp file, fsync, replace) and validated on
load and before every save; a string that is not an allowed id or enum is refused, so page text, model output, prompts
and secrets cannot get in. A bad checkpoint raises CheckpointError; it is never silently reset (reset() does that).
Prices come from a feed (today: made-up Store Lab prices in bench/watch/prices.json); an observation that fails
validation is dropped and counted, never stored.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol

ROOT = Path(__file__).resolve().parents[1]
FEED_FILE = ROOT / "bench" / "watch" / "prices.json"
MAX_DAYS_PER_CHAIN = 8
CONFIRM_DAYS = 2
MAX_DAY = 3650
MAX_ITEMS = 200
MAX_PRICE = 100_000.0
GOAL = "Watch the wishlist: check each item's price for each day, alert once when a drop is real."

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
KEYLIKE_RE = re.compile(r"^((sk|pk|rk)-|gh[pousr]-?[a-z0-9]{8}|xox[abprs]-|akia[0-9a-z]{12})")  # key formats ID_RE allows
CONDITIONS = ("new", "refurbished", "used")
TRUST = ("safe", "caution", "unsafe", "unknown")
ALERT_STATES = ("none", "sent")
ITEM_KEYS = {"target", "new_only", "trusted_only", "last_checked_day", "last_price", "store", "below_target_days", "alert"}
ALERT_KEYS = {"state", "day", "price"}
OBS_KEYS = {"price", "store", "condition", "store_trust", "held"}


class CheckpointError(ValueError):
    """The checkpoint on disk is not a valid watch checkpoint. Nothing is overwritten."""


class FeedError(ValueError):
    """The price feed cannot be read at all."""


# ----------------------------------------------------------------------------- data dir, rules


def data_dir() -> Path:
    return Path(os.environ.get("WATCH_DATA_DIR", "").strip() or ROOT / ".pricise-data" / "watch")


def checkpoint_file() -> Path:
    return data_dir() / "checkpoint.json"


def parse_rule(rule: Optional[str], target: Optional[float]) -> dict:
    """The watch rule as structured facts. The rule TEXT is never stored: only the target and two flags."""
    text = (rule or "").lower()
    if target is None:
        m = re.search(r"under\s*\$\s*(\d+(?:\.\d+)?)", text)
        target = float(m.group(1)) if m else None
    if target is not None and not (0 < float(target) <= MAX_PRICE):
        target = None
    return {"target": None if target is None else float(target), "new_only": bool(re.search(r"\bnew only\b", text)),
            "trusted_only": bool(re.search(r"\btrusted only\b", text))}


def counts(rule: dict, obs: Optional[dict]) -> bool:
    """True when this day's offer counts toward a real drop."""
    if obs is None or rule["target"] is None or obs["held"]:
        return False
    if obs["price"] > rule["target"]:
        return False
    if rule["new_only"] and obs["condition"] != "new":
        return False
    if rule["trusted_only"] and obs["store_trust"] != "safe":
        return False
    return True


# ----------------------------------------------------------------------------- validation (untrusted in, facts out)


def _num(v: Any, lo: float, hi: float) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi


def _store_ok(s: Any) -> bool:
    return isinstance(s, str) and bool(ID_RE.match(s)) and not KEYLIKE_RE.match(s)


def clean_observation(raw: Any) -> Optional[dict]:
    """A feed observation as facts, or None when any field is not a plain allowed value (page text, junk...)."""
    if not isinstance(raw, dict) or not OBS_KEYS <= raw.keys():
        return None
    ok = (_num(raw["price"], 0.01, MAX_PRICE) and _store_ok(raw["store"]) and raw["condition"] in CONDITIONS and raw["store_trust"] in TRUST and isinstance(raw["held"], bool))
    return {k: raw[k] for k in OBS_KEYS} if ok else None


def validate_checkpoint(cp: Any) -> None:
    """Raises CheckpointError unless cp has exactly the allowed shape and values."""
    def bad(why: str) -> CheckpointError:
        return CheckpointError(f"invalid watch checkpoint: {why}")

    if not isinstance(cp, dict) or set(cp) != {"version", "day", "items"} or cp["version"] != 1:
        raise bad("top level")
    if not (isinstance(cp["day"], int) and not isinstance(cp["day"], bool) and 0 <= cp["day"] <= MAX_DAY):
        raise bad("day")
    if not isinstance(cp["items"], dict) or len(cp["items"]) > MAX_ITEMS:
        raise bad("items")
    for iid, f in cp["items"].items():
        if not isinstance(iid, str) or not ID_RE.match(iid):
            raise bad("item id")
        if not isinstance(f, dict) or set(f) != ITEM_KEYS:
            raise bad(f"fields of {iid}")
        if not (f["target"] is None or _num(f["target"], 0.01, MAX_PRICE)):
            raise bad(f"target of {iid}")
        if not (isinstance(f["new_only"], bool) and isinstance(f["trusted_only"], bool)):
            raise bad(f"flags of {iid}")
        if not (isinstance(f["last_checked_day"], int) and 0 <= f["last_checked_day"] <= MAX_DAY):
            raise bad(f"last_checked_day of {iid}")
        if not (f["last_price"] is None or _num(f["last_price"], 0.01, MAX_PRICE)):
            raise bad(f"last_price of {iid}")
        if not (f["store"] is None or _store_ok(f["store"])):
            raise bad(f"store of {iid}")
        if not (isinstance(f["below_target_days"], int) and 0 <= f["below_target_days"] <= MAX_DAY):
            raise bad(f"below_target_days of {iid}")
        a = f["alert"]
        if not isinstance(a, dict) or set(a) != ALERT_KEYS or a["state"] not in ALERT_STATES:
            raise bad(f"alert of {iid}")
        if a["state"] == "none" and (a["day"] is not None or a["price"] is not None):
            raise bad(f"alert of {iid}")
        if a["state"] == "sent" and not (isinstance(a["day"], int) and _num(a["price"], 0.01, MAX_PRICE)):
            raise bad(f"alert of {iid}")


# ----------------------------------------------------------------------------- checkpoint I/O


def empty_checkpoint() -> dict:
    return {"version": 1, "day": 0, "items": {}}


def load() -> dict:
    try:
        raw = checkpoint_file().read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty_checkpoint()
    try:
        cp = json.loads(raw)
    except ValueError:
        raise CheckpointError("invalid watch checkpoint: not JSON") from None
    validate_checkpoint(cp)
    return cp


def save(cp: dict) -> None:
    """Atomic: a crash leaves the old checkpoint or the new one, never half of one. Raises on any write error."""
    validate_checkpoint(cp)
    path = checkpoint_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(cp, indent=2, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def reset() -> None:
    """Clear the checkpoint (demo retakes): progress and sent alerts are forgotten. The next sync_items() starts over."""
    for p in data_dir().glob("checkpoint.json*"):
        p.unlink(missing_ok=True)


# ----------------------------------------------------------------------------- items


def _new_item(rule: dict, day: int) -> dict:
    return {**rule, "last_checked_day": day, "last_price": None, "store": None, "below_target_days": 0,
            "alert": {"state": "none", "day": None, "price": None}}


def sync_items(items: list[dict]) -> dict:
    """Make the watch list match the wishlist: [{id, target?, rule?}]. New items start at today's day (no backfill);
    removed items are dropped; a changed rule restarts that item's drop count and allows a new alert."""
    cp = load()
    wanted = {}
    for it in items:
        iid = str(it.get("id", ""))
        if not ID_RE.match(iid):
            raise ValueError("wishlist item ids must be short lowercase ids")
        wanted[iid] = parse_rule(it.get("rule"), it.get("target"))
    if len(wanted) > MAX_ITEMS:
        raise ValueError(f"at most {MAX_ITEMS} watched items")
    out = {}
    for iid, rule in wanted.items():
        old = cp["items"].get(iid)
        if old is None:
            out[iid] = _new_item(rule, cp["day"])
        elif any(old[k] != rule[k] for k in rule):
            out[iid] = {**_new_item(rule, old["last_checked_day"]), "last_price": old["last_price"], "store": old["store"]}
        else:
            out[iid] = old
    cp["items"] = out
    save(cp)
    return cp


# ----------------------------------------------------------------------------- feed


def load_feed(path: Path = FEED_FILE) -> "Feed":
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise FeedError(f"price feed not readable ({type(err).__name__})") from None
    return Feed.from_runs(data.get("items", {}))


@dataclass
class Feed:
    """Day -> item -> observation (already validated). rejected = observations dropped by validation."""
    by_day: dict[int, dict[str, dict]] = field(default_factory=dict)
    rejected: int = 0

    @classmethod
    def from_runs(cls, items: dict) -> "Feed":
        feed = cls()
        for iid, runs in (items or {}).items():
            for run in runs if isinstance(runs, list) else []:
                obs = clean_observation(run) if isinstance(run, dict) else None
                lo, hi = (run.get("from"), run.get("to")) if isinstance(run, dict) else (None, None)
                if obs is None or not ID_RE.match(str(iid)) or not (isinstance(lo, int) and isinstance(hi, int)
                                                                   and 1 <= lo <= hi <= MAX_DAY):
                    feed.rejected += 1
                    continue
                for d in range(lo, hi + 1):
                    feed.by_day.setdefault(d, {})[iid] = obs
        return feed

    def get(self, iid: str, day: int) -> Optional[dict]:
        return self.by_day.get(day, {}).get(iid)


# ----------------------------------------------------------------------------- one day (one chunk)


def step_day(cp: dict, day: int, feed: Feed) -> list[dict]:
    """Process one day for every item, in place. Returns the alerts decided today (already marked sent in cp)."""
    alerts = []
    for iid, f in sorted(cp["items"].items()):
        if f["last_checked_day"] >= day:
            continue  # already processed (a resumed chunk never counts a day twice)
        obs = feed.get(iid, day)
        f["last_checked_day"] = day
        if obs is not None:
            f["last_price"], f["store"] = float(obs["price"]), obs["store"]
        f["below_target_days"] = f["below_target_days"] + 1 if counts(f, obs) else 0
        if f["alert"]["state"] == "none" and f["below_target_days"] >= CONFIRM_DAYS:
            f["alert"] = {"state": "sent", "day": day, "price": float(obs["price"])}
            alerts.append({"id": f"{iid}-d{day}", "item": iid, "day": day, "price": float(obs["price"]),
                           "store": obs["store"], "target": f["target"]})
    cp["day"] = max(cp["day"], day)
    return alerts


def _money(v: Optional[float]) -> str:
    return "-" if v is None else f"{v:.2f}"


def carryover(cp: dict, last_day: int) -> str:
    """The canonical COMPLETED / REMAINING / DECISIONS / CONTEXT block, from checkpoint facts only."""
    sent = [f"{iid} alert sent day {f['alert']['day']} at {f['alert']['price']:.2f}"
            for iid, f in sorted(cp["items"].items()) if f["alert"]["state"] == "sent"]
    ctx = "; ".join(
        f"{iid} t={_money(f['target'])}{' new' if f['new_only'] else ''}{' trusted' if f['trusted_only'] else ''}"
        f" last={_money(f['last_price'])}@{f['store'] or '-'} run={f['below_target_days']}"
        for iid, f in sorted(cp["items"].items()))
    remaining = f"days {cp['day'] + 1}-{last_day}" if cp["day"] < last_day else "none"
    return (f"COMPLETED: days 1-{cp['day']} checked\nREMAINING: {remaining}\n"
            f"DECISIONS: {'; '.join(sent) or 'no alert sent'}\nCONTEXT: {ctx or 'no items'}")


# ----------------------------------------------------------------------------- the chain (bounded chunks)


class Chain(Protocol):
    """Runs one bounded chain of chunks. step (iii) puts the sidecar's Markovian engine behind this; LocalChain is the
    no-sidecar version."""

    def start(self, goal: str, carryover: str) -> None: ...
    def chunk(self, text: str, done: bool) -> None: ...


class LocalChain:
    """No engine: counts chunks and keeps each chunk text for inspection (tests)."""

    def __init__(self) -> None:
        self.chains = 0
        self.chunks: list[str] = []

    def start(self, goal: str, carryover: str) -> None:
        self.chains += 1

    def chunk(self, text: str, done: bool) -> None:
        self.chunks.append(text)


@dataclass
class Result:
    alerts: list[dict]
    days: list[int]
    chains: int
    day: int


def advance_to(day: int, feed: Optional[Feed] = None, chain: Optional[Chain] = None) -> Result:
    """Catch up to `day`: every missed day, one day per chunk, at most MAX_DAYS_PER_CHAIN per chain, checkpoint saved
    after each chunk. A day at or before the last processed day does nothing (going back never re-sends an alert)."""
    if not (isinstance(day, int) and 0 <= day <= MAX_DAY):
        raise ValueError(f"day must be 0-{MAX_DAY}")
    feed = feed or load_feed()
    chain = chain or LocalChain()
    cp = load()
    alerts, days, chains = [], [], 0
    while cp["day"] < day:
        first, last = cp["day"] + 1, min(day, cp["day"] + MAX_DAYS_PER_CHAIN)
        chain.start(GOAL, carryover(cp, day))
        chains += 1
        for d in range(first, last + 1):
            today = step_day(cp, d, feed)
            save(cp)  # BEFORE the alerts leave this function: at-most-once
            alerts += today
            days.append(d)
            done = d == day
            chain.chunk(carryover(cp, day) + ("\n[TASK_COMPLETE]" if done else "\n[STATE_CHECKPOINT]"), done)
    return Result(alerts=alerts, days=days, chains=chains, day=cp["day"])


def status() -> dict:
    """What the wishlist panel can show: per item, the last price and whether the alert was sent."""
    cp = load()
    return {"day": cp["day"], "items": {iid: {"last_price": f["last_price"], "store": f["store"],
                                              "alert_sent": f["alert"]["state"] == "sent", "alert_day": f["alert"]["day"],
                                              "has_rule": f["target"] is not None}
                                        for iid, f in cp["items"].items()}}
