"""Agent trace: one JSONL line per agent step in logs/<check_id>.jsonl, plus a short line on stdout (the "pricise.trace" log).

A step is each version of a finding as it streams (an agent proposes, the Skeptic or Trust challenges, confirms or rejects),
and the final verdict. A challenge is written as the CHALLENGER's step, with the challenged agent as target_agent, so
"skeptic reject -> price" and "trust reject -> price" read directly. Only summaries are written: never page HTML, keys or
tokens (findings never hold them; evidence refs are reduced to their kind and a short reference).
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

from backend.contracts import FinalCard, Finding

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
log = logging.getLogger("pricise.trace")
TABLE_RE = re.compile(r"\b(?:FROM|JOIN)\s+((?:[a-z0-9_]+\.)+[a-z_]+)", re.I)  # every table a query read
STAND_INS = ("rules (model unavailable)", "rules (hard rule overrides the model)")  # the Skeptic's model review fell back
SECRET_RE = re.compile(r"(nvapi-|bearer\s+|api[_-]?key|token=)[^\s\"']*", re.I)


def _path(check_id: str) -> Path:
    return LOGS_DIR / f"{re.sub(r'[^A-Za-z0-9_-]', '', check_id) or 'check'}.jsonl"


def path_for(check_id: str) -> Path:
    return _path(check_id)


def _clean(text: Optional[str], n: int = 200) -> str:
    return SECRET_RE.sub("[redacted]", " ".join(str(text or "").split()))[:n]


def _model(f: Finding) -> str:
    m = f.model or ""  # a Nemotron model, or "rules (model unavailable)" / "rules (hard rule overrides the model)"
    return m if "nemotron" in m.lower() or m in STAND_INS else "rules"


def _table(name: str) -> str:
    """pricise_pg.listings -> "listings"; lightning.metastore.pricise.pricise_usl.safe_price -> "pricise_usl.safe_price"."""
    parts = name.lower().split(".")
    return ".".join(parts[-2:]) if parts[0] == "lightning" else parts[-1]


def _tables(f: Finding) -> Optional[list[str]]:
    found = sorted({_table(t) for e in f.evidence if e.kind == "sql" for t in TABLE_RE.findall(e.ref)})
    return found or None


def _input(chk: dict, f: Finding) -> str:
    page = chk.get("page") or {}
    product = (page.get("product") or {}).get("name") or page.get("host") or chk.get("url")
    refs = [f"{e.kind}:{e.ref if e.kind != 'sql' else 'zetaris query'}" for e in f.evidence[:2]]
    return _clean(f"{product} · {f.type}" + (f" · {', '.join(refs)}" if refs else ""), 160)


def _write(check_id: str, row: dict) -> None:
    try:
        LOGS_DIR.mkdir(exist_ok=True)
        with _path(check_id).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as err:  # a trace must never break a check
        log.warning("trace not written: %s", type(err).__name__)


def finding(chk: dict, f: Finding) -> None:
    """One step: an agent's finding, or a challenger's decision about another agent's finding."""
    challenger = f.challenged_by if f.challenged_by and f.challenged_by != f.agent else None
    agent = challenger or f.agent
    if challenger:
        action = {"rejected": "reject", "confirmed": "confirm", "challenged": "challenge"}.get(f.status, "recheck")
        target = f.agent
    else:
        action = f"propose:{f.type}" if f.status == "proposed" else f"report:{f.type}"
        target = "skeptic" if f.type == "best_price" and f.status == "proposed" else None
    row = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(f.ts or time.time())) + "Z",
           "trace_id": chk["check_id"], "agent": agent, "action": action, "input_summary": _input(chk, f),
           "output_summary": _clean(f.summary), "target_agent": target, "confidence": round(f.confidence or 0.0, 2),
           "retry_count": f.rounds or 0, "status": f.status, "model": _model(f), "zetaris_table": _tables(f)}
    _write(chk["check_id"], row)
    log.info("trace %s %s %s %s%s | %s", chk["check_id"], agent, action, f.status, f" -> {target}" if target else "",
             row["output_summary"][:90])


def verdict(chk: dict, card: Optional[dict]) -> None:
    """The last step: the card the shopper sees."""
    if not card:
        return
    c = FinalCard(**card)
    row = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z", "trace_id": chk["check_id"],
           "agent": "orchestrator", "action": "verdict", "input_summary": _clean(chk.get("url"), 160),
           "output_summary": _clean(f"{c.title} · {c.subtitle} · best price: {c.best_price.headline}"),
           "target_agent": None, "confidence": None, "retry_count": 0, "status": c.tone, "model": "rules",
           "zetaris_table": None}
    _write(chk["check_id"], row)
    log.info("trace %s orchestrator verdict %s | %s", chk["check_id"], c.tone, row["output_summary"][:90])
