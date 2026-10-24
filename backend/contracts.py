"""Shared data shapes for Pricise. Abel owns this file; change it only with the team's agreement."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Status = Literal["proposed", "challenged", "confirmed", "rejected", "unverified"]
EvidenceKind = Literal["screenshot", "log", "sql", "price_row", "url", "selector", "text"]
Tone = Literal["good", "caution", "bad", "unknown"]


class Evidence(BaseModel):
    kind: EvidenceKind
    ref: str
    label: Optional[str] = None  # short text drawn next to an outlined element ("" = outline only)
    placement: Optional[str] = None  # "right" to draw the label beside the element


class Finding(BaseModel):
    check_id: str
    agent: Literal["trust", "deal", "price", "skeptic", "page"]
    type: str  # e.g. fake_discount, resetting_timer, drip_fees, prechecked_addon, scarcity, domain_age
    status: Status
    summary: str
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = 0.5
    model: Optional[str] = None
    challenged_by: Optional[str] = None
    rounds: int = 0
    ts: float = 0.0


class Reason(BaseModel):
    text: str
    tone: Tone = "unknown"
    proof: Optional[str] = None  # which evidence view to open: chart, replay, fees, whois, stores


class Verdict(BaseModel):
    status: Tone
    headline: str
    reasons: list[Reason] = Field(default_factory=list)


class BestPrice(Verdict):
    store: Optional[str] = None
    total: Optional[float] = None
    url: Optional[str] = None


class FinalCard(BaseModel):
    check_id: str
    title: str  # e.g. "Don't buy here"
    subtitle: str
    tone: Tone
    safe: Verdict
    honest: Verdict
    best_price: BestPrice
    tricks: int = 0
    elapsed_s: float = 0.0
    alt: Optional[dict] = None  # safer choice, e.g. {"store": "Store C", "total": 229, "url": "..."}


class PageProduct(BaseModel):
    name: Optional[str] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    was_price: Optional[float] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    sku: Optional[str] = None
    image: Optional[str] = None
    source: Optional[str] = None  # json-ld, meta, visible


class PageSignal(BaseModel):
    type: str
    selector: Optional[str] = None
    text: str = ""
    amount: Optional[float] = None
    detail: Optional[dict] = None


class PageFacts(BaseModel):
    url: str
    host: str = ""
    title: str = ""
    https: bool = True
    product: PageProduct = Field(default_factory=PageProduct)
    signals: list[PageSignal] = Field(default_factory=list)


class CheckRequest(BaseModel):
    url: str
    page: Optional[PageFacts] = None


class WishlistItem(BaseModel):
    id: Optional[str] = None
    name: str
    url: Optional[str] = None
    rule: str = ""
    target: Optional[float] = None
    best_price: Optional[float] = None
    best_store: Optional[str] = None
    status: str = "Watching"
    tone: Tone = "unknown"
