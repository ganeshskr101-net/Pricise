"""Pre-ticked add-ons: the extension already found checked boxes that add cost; turn each one into a confirmed finding.

Read only. The selector lets the extension outline the add-on, and "Untick" (user-pressed) uses the same selector.
"""
from __future__ import annotations

from backend.contracts import PageFacts
from backend.tools.common import ToolResult, finding, money, parse_money


def check_addons(check_id: str, page: PageFacts) -> ToolResult:
    out = ToolResult()
    seen: set[str] = set()
    for s in page.signals:
        if s.type != "prechecked_addon" or (s.selector and s.selector in seen):
            continue
        if s.selector:
            seen.add(s.selector)
        amount = s.amount if s.amount is not None else parse_money(s.text)
        label = f"Pre-ticked {money(amount)}" if amount else "Pre-ticked add-on"
        text = " ".join(s.text.split())[:70]
        cost = f"An add-on costing {money(amount)}" if amount else "An add-on"
        evidence = [{"kind": "selector", "ref": s.selector, "label": label}] if s.selector else []
        evidence.append({"kind": "text", "ref": text})
        # Confirmed when it costs money; a pre-ticked box with no price shown (e.g. "send me offers") is only proposed.
        out.findings.append(finding(check_id, "prechecked_addon", "confirmed" if amount else "proposed",
                                    f"{cost} is ticked for you: \"{text}\".", evidence,
                                    confidence=0.97 if amount else 0.5))
        if amount:
            out.evidence_data.setdefault("addons", []).append({"label": text, "amount": amount})
    return out
