"""Temporary Finding model matching the frozen finding format.
 
Delete this file once backend/contracts.py lands, then change imports from
`backend._finding_stub` to `backend.contracts`.
"""
from __future__ import annotations
 
from datetime import datetime, timezone
from typing import Literal, Optional
 
from pydantic import BaseModel, Field
 
Status = Literal["proposed", "challenged", "confirmed", "rejected", "unverified"]
EvidenceKind = Literal["screenshot", "log", "sql", "price_row", "url", "selector"]
 
 
class Evidence(BaseModel):
    kind: EvidenceKind
    ref: str
 
 
class Finding(BaseModel):
    check_id: str
    agent: str
    type: str
    status: Status = "proposed"
    summary: str
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    model: Optional[str] = None
    challenged_by: Optional[str] = None
    rounds: int = Field(default=0, ge=0)
    ts: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
 
