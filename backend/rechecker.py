"""Rechecker for the Skeptic's best_price rule (AGENT_ORCH=on with AGENT_PRICE=real), plus the adapter and card choice.

The Price Agent (Josh, backend/agents/price.py) emits ONE confirmed best_price finding, for its winner, with the evidence
ref listing:<store_id>:<product_id>:day<n>. adapt() adds the exact-match ref this module reads:

    store=<url>;model=0|1;variant=1;condition=0|1;warranty=1;price=<true total>

Only model and condition are checked against data. The catalog has no variant or warranty fields, so both are written
as 1 and the finding says "variant and warranty not in data, assumed equal". Any agent may emit best_price findings in
this shape, one per candidate; the card takes the cheapest confirmed one (best_price_on_card).

Rechecker: the flags from the ref plus a fresh store-safety verdict, for skeptic.check_best_price:
- AGENT_TRUST=real: trust.assess_store(url, f"{check_id}:recheck:{store}") (cached 5 min, so a store the Price Agent
  already vetted costs nothing). A store the Trust Agent could not check gives no verdict.
- AGENT_TRUST=stub: the seeded catalog flag (ok -> safe, risky -> caution, scam -> unsafe), like the Price Agent's stub mode.
The Skeptic blocks unsafe and an unchecked store; caution passes the rule, but the Price Agent never proposes a caution
store (with use_trust=True it rejects caution), so in practice only safe stores win. A best_price without the ref gets
no inputs and keeps its status. PriceResult.alt is the winner itself, not a runner-up: with the Price Agent's single
finding, a rejected winner leaves "no runner-up".
"""
from __future__ import annotations

import os
import re
from typing import Optional
from urllib.parse import urlsplit

from backend.agents import skeptic
from backend.contracts import BestPrice, Evidence, Finding, PageFacts, Reason

ASSUMED = "variant and warranty not in data, assumed equal"
REF_RE = re.compile(r"store=(?P<store>[^;]+);model=(?P<model>[01]);variant=(?P<variant>[01]);condition=(?P<condition>[01]);"
                    r"warranty=(?P<warranty>[01]);price=(?P<price>\d+(?:\.\d+)?)")
LISTING_RE = re.compile(r"listing:(?P<store>[^:]+):(?P<product>[^:]+):day(?P<day>\d+)")
SEEDED = {"ok": "safe", "risky": "caution", "scam": "unsafe"}
NO_RUNNER_UP = "No runner-up: the Price Agent proposes one store only"


def exact_ref(store_url: str, model: bool, condition: bool, price: float) -> str:
    total = f"{price:.2f}".rstrip("0").rstrip(".")
    return f"store={store_url};model={int(model)};variant=1;condition={int(condition)};warranty=1;price={total}"


def parse_ref(f: Finding) -> Optional[dict]:
    """{"store", "match": {model, variant, condition, warranty}, "price", "ref"} from the exact-match ref, or None."""
    for e in f.evidence:
        m = REF_RE.fullmatch(e.ref) if e.kind == "price_row" else None
        if m:
            return {"store": m["store"], "price": float(m["price"]), "ref": e.ref,
                    "match": {k: m[k] == "1" for k in ("model", "variant", "condition", "warranty")}}
    return None


# ----------------------------------------------------------------------------- adapter (Price Agent -> exact-match ref)


def _rows(source: str, day: int) -> Optional[list[dict]]:
    """The listings again, from the same source as the Price Agent's run; None if that source can't answer now."""
    from backend.data import repo
    from backend.data.zetaris import ZetarisError

    if source == "zetaris":
        try:
            rows, _, now = repo.listings(day)
        except (ZetarisError, ValueError):
            return None
        return rows if now == "zetaris" else None
    return repo._catalog_listings(day) if source.startswith("fixture") else None


def _josh_ref(f: Finding, source: str, page: PageFacts, url: str) -> Optional[str]:
    listing = next((m for e in f.evidence if e.kind == "price_row" and (m := LISTING_RE.fullmatch(e.ref))), None)
    store_url = next((e.ref for e in f.evidence if e.kind == "url"), None)
    rows = _rows(source, int(listing["day"])) if listing and store_url else None
    if not rows:
        return None
    from backend.agents import price
    from backend.tools.discount import store_key

    _own, item = price._identify(rows, store_key(url, page).removeprefix("storelab/"), page)
    row = next((r for r in rows if (r["store_id"], r["product_id"]) == (listing["store"], listing["product"])), None)
    if not item or not row:
        return None
    return exact_ref(store_url, row["model_key"] == item["model_key"], row["condition"] == item["condition"], row["true_total"])


def adapt(res, page: PageFacts, url: str) -> list[Finding]:
    """The Price Agent's findings as it made them, except a confirmed best_price without the exact-match ref: that one
    gets the ref (and the ASSUMED note) when its listing can be read again. Blocking in Zetaris mode (one query)."""
    out = []
    for f in res.findings:
        ref = None
        if f.type == "best_price" and f.status == "confirmed" and parse_ref(f) is None:
            ref = _josh_ref(f, res.source, page, url)
        if ref:
            f = f.model_copy(update={"summary": f"{f.summary} Model and condition checked against data; {ASSUMED}.",
                                     "evidence": [*f.evidence, Evidence(kind="price_row", ref=ref, label="Exact match")]})
        out.append(f)
    return out


# ----------------------------------------------------------------------------- rechecker


def store_id(store_url: str) -> str:
    m = re.search(r"/(?:storelab|bench)/([^/]+)", urlsplit(store_url).path)
    return m[1] if m else (urlsplit(store_url).hostname or "store")


def _seeded(store_url: str) -> Optional[str]:
    from backend.data import catalog

    flag = next((s["trust"] for s in catalog.rows()["stores"] if s["store_id"] == store_id(store_url)), None)
    return SEEDED.get(flag)


async def store_verdict(store_url: str, check_id: str) -> Optional[str]:
    """safe | caution | unsafe, or None when nobody could check the store."""
    if os.environ.get("AGENT_TRUST", "stub").lower() != "real":
        return _seeded(store_url)
    from backend.agents import trust

    tv = await trust.assess_store(store_url, f"{check_id}:recheck:{store_id(store_url)}")
    return tv["verdict"] if tv["checked"] else None


class Rechecker:
    """orchestrator.Rechecker for one check, best_price only. What it saw goes to chk["rechecks"] (by ref) for the card."""
    types = frozenset({"best_price"})

    def __init__(self, chk: dict) -> None:
        self.chk = chk

    async def __call__(self, f: Finding) -> Optional[dict]:
        ref = parse_ref(f)
        if ref is None:
            return None
        verdict = await store_verdict(ref["store"], f.check_id)
        inputs = {"match": ref["match"], "trust_verdict": verdict}
        reason = skeptic.check_best_price(f, ref["match"], verdict).reason
        self.chk.setdefault("rechecks", {})[ref["ref"]] = {**inputs, "reason": reason}
        return inputs


# ----------------------------------------------------------------------------- the card's best price


def _name(f: Finding, ref: dict) -> str:
    """The Price Agent's wording "<Store>, $<total>: ..." gives the store name; else the store id from the URL."""
    head = f.summary.split(", $", 1)
    return head[0] if len(head) == 2 and head[0] else store_id(ref["store"])


def _money(v: float) -> str:
    return f"${v:,.0f}" if float(v).is_integer() else f"${v:,.2f}"


def best_price_on_card(chk: dict, findings: list[Finding], best: BestPrice, alt: Optional[dict]
                       ) -> tuple[BestPrice, Optional[dict]]:
    """The card's best price from best_price findings with the exact-match ref that the Skeptic confirmed, cheapest true
    total ("Checking…" while any is undecided: they arrive "confirmed" by their agent). The Price Agent's own verdict
    stays when it names the same store and total (its reasons are richer). Without such findings (real site, not in
    catalog, couldn't compare) the Price Agent's verdict stays as it is."""
    cands = [(f, r) for f in findings if f.type == "best_price" and (r := parse_ref(f))]
    if not cands:
        return best, alt
    decided = [(f, r) for f, r in cands if f.challenged_by == "skeptic" and f.status != "challenged"]
    if len(decided) < len(cands):
        return BestPrice(status="unknown", headline="Checking the best price…"), None
    ok = [(f, r) for f, r in decided if f.status == "confirmed" and f.rounds >= 1]  # rounds 0: recheck failed, not checked
    if ok:
        f, r = min(ok, key=lambda x: (x[1]["price"], x[1]["store"]))
        if best.status == "good" and best.url == r["store"] and best.total == r["price"]:
            return best, alt
        name = _name(f, r)
        here = store_id(r["store"]) == store_id(chk.get("url") or "")
        reasons = [Reason(text="Same model and condition (variant and warranty not in data)", tone="good", proof="stores"),
                   Reason(text=f"{_money(r['price'])} true total, checked by the Skeptic", tone="good", proof="stores")]
        return (BestPrice(status="good", headline=f"{name} · {_money(r['price'])}", store=name, total=r["price"],
                          url=r["store"], reasons=reasons),
                None if here else {"store": name, "total": r["price"], "url": r["store"]})
    seen = chk.get("rechecks") or {}
    reasons = []
    for f, r in cands:
        why = seen.get(r["ref"], {}).get("reason", "rejected") if f.status == "rejected" else "couldn't verify"
        reasons.append(Reason(text=f"{_name(f, r)}, {_money(r['price'])}: not accepted by the Skeptic ({why})", tone="caution",
                              proof="stores"))
    reasons.append(Reason(text=NO_RUNNER_UP if len(cands) == 1 else "No runner-up left", tone="unknown"))
    return BestPrice(status="unknown", headline="No safe match", reasons=reasons), None
