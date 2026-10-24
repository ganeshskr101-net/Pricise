"""Small helpers shared by the Deal Agent tools: finding builder, money parsing, and the result shape every tool returns."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from backend.contracts import Evidence, Finding

RULES = "rules (no model)"
PRICE_RE = re.compile(r"(-|−)?\s?[$€£₹]\s?(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)")


@dataclass
class ToolResult:
    """What a tool hands back to the agent: findings for the panel + data for the evidence page (chk["evidence_data"])."""
    findings: list[Finding] = field(default_factory=list)
    evidence_data: dict = field(default_factory=dict)


def finding(check_id: str, type_: str, status: str, summary: str, evidence: Optional[list[dict]] = None,
            confidence: float = 0.5, model: str = RULES, **kw) -> Finding:
    return Finding(check_id=check_id, agent="deal", type=type_, status=status, summary=summary,
                   evidence=[Evidence(**e) for e in (evidence or [])], confidence=confidence, model=model,
                   ts=time.time(), **kw)


def parse_money(text: Optional[str]) -> Optional[float]:
    """First price in the text, e.g. "Add for $4.99" -> 4.99, "-$5.00" -> -5.0."""
    m = PRICE_RE.search(text or "")
    if not m:
        return None
    value = float(m.group(2).replace(",", ""))
    return -value if m.group(1) else value


def money(v: Optional[float]) -> str:
    return "unknown" if v is None else f"${v:,.2f}"


def is_storelab(url: str) -> bool:
    """Store Lab mode (hidden browser allowed) only for our own made-up stores served from this laptop."""
    u = urlparse(url)
    local = (u.hostname or "") in {"localhost", "127.0.0.1"}
    return local and ("/storelab/" in u.path or "/bench/" in u.path)
