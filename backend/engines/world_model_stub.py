"""In-memory stand-in for the World Model (backend/engines/world_model.py, Abel) so the Trust Agent can write findings today.

Same interface Trust uses: write_finding(finding) and read_findings(check_id). Idempotent: the same (check_id, agent, type)
written twice replaces, never duplicates. Delete this file once Abel's world_model.py lands.
"""
from __future__ import annotations

from backend.contracts import Finding

_store: dict[tuple[str, str, str], Finding] = {}


def write_finding(finding: Finding) -> None:
    _store[(finding.check_id, finding.agent, finding.type)] = finding


def read_findings(check_id: str) -> list[Finding]:
    return [f for (cid, _, _), f in _store.items() if cid == check_id]


def clear() -> None:
    _store.clear()
