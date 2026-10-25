"""POST /internal/trust/vet: how the Price Agent (and later the orchestrator) asks the Trust Agent about a store.

    POST /internal/trust/vet {"store_url": "http://localhost:8787/storelab/store-d/", "check_id": "optional"}
    -> {"verdict": "safe|caution|unsafe", "reasons": [...], "findings": [Finding...], "store", "store_url", "checked", "elapsed_ms"}

Included in backend/stub_server.py; the real orchestrator includes the same router. In-process callers can simply
`await backend.agents.trust.assess_store(url, check_id)`.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.agents.trust import assess_store

router = APIRouter(prefix="/internal/trust", tags=["trust"])


class VetRequest(BaseModel):
    store_url: str
    check_id: str = Field(default_factory=lambda: "vet-" + uuid.uuid4().hex[:8])


@router.post("/vet")
async def vet(req: VetRequest) -> dict:
    tv = await assess_store(req.store_url, req.check_id)
    return {**tv, "findings": [f.model_dump() for f in tv["findings"]]}
