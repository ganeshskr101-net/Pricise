"""Price Agent: answers "Best safe price?" by finding the exact same item (model + condition) at a trusted store, by true total.

Store Lab pages (localhost /storelab/): one Lightning SQL query through Zetaris (backend/data/repo.py:listings) returns every
Store Lab listing with its price on the demo day and its true total = price + shipping + checkout fees - card offers.
Explicit rules (no model) then decide, and every decision is a finding:
  - a different model in the same family (e.g. WH-1000XM4)  -> rejected: not the same item
  - a different condition (refurbished vs new)               -> rejected: not the same item
  - a store flagged as a scam (or risky) in the seeded data  -> rejected (the Trust Agent will replace these flags)
  - a store that is down                                     -> unverified, skipped
  - the cheapest remaining true total wins. Evidence cites the SQL.
Real sites: read only, no scraping of other stores: one "unverified" finding that the comparison covers our demo catalog only.
Never raises: if Zetaris fails it falls back to the in-memory catalog and says so; anything else becomes an unverified finding.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from backend.contracts import BestPrice, Evidence, Finding, PageFacts, Reason
from backend.data import catalog, repo
from backend.data.zetaris import ZetarisError
from backend.tools.common import is_storelab
from backend.tools.discount import store_key

log = logging.getLogger("pricise.price")
RULES = "rules (no model)"


@dataclass
class PriceResult:
    findings: list[Finding] = field(default_factory=list)
    best_price: BestPrice = field(default_factory=lambda: BestPrice(status="unknown", headline="Not checked"))
    alt: Optional[dict] = None       # safer choice for the verdict card: {"store", "total", "url"}
    stores: list[dict] = field(default_factory=list)  # evidence page rows: {"store", "total", "note", "status"}
    sql: Optional[str] = None
    source: str = ""


def _f(check_id: str, type_: str, status: str, summary: str, evidence: Optional[list[dict]] = None,
       confidence: float = 0.9, **kw) -> Finding:
    return Finding(check_id=check_id, agent="price", type=type_, status=status, summary=summary,
                   evidence=[Evidence(**e) for e in (evidence or [])], confidence=confidence, model=RULES,
                   ts=time.time(), **kw)


def money(v: float) -> str:
    return f"${v:,.0f}" if float(v).is_integer() else f"${v:,.2f}"


def _math(r: dict) -> str:
    """How the true total is made: "$255 minus a $26 card offer, free shipping"."""
    parts = [money(r["price"])]
    if r["shipping"]:
        parts.append(f"+ {money(r['shipping'])} shipping")
    if r["fees"]:
        parts.append(f"+ {money(r['fees'])} fees")
    text = " ".join(parts)
    if r["card_offer"]:
        text += f" minus a {money(r['card_offer'])} card offer"
    if not r["shipping"]:
        text += ", free shipping"
    return text


def _sql_ev(res: PriceResult, n: int) -> list[dict]:
    if not res.sql:
        return [{"kind": "log", "ref": f"catalog:{res.source}", "label": f"{n} listings from the Store Lab catalog ({res.source})"}]
    return [{"kind": "sql", "ref": res.sql, "label": f"Zetaris returned {n} Store Lab listings"}]


def _real_site(check_id: str, page: PageFacts) -> PriceResult:
    p = page.product
    res = PriceResult(source="none")
    res.findings.append(_f(check_id, "catalog_only", "unverified",
                           "Other-store comparison covers our demo catalog only, so other stores weren't checked for this page.",
                           confidence=0.0))
    reasons = []
    if p.name:
        reasons.append(Reason(text=f"Product: {p.name[:70]}", tone="unknown"))
    if p.price is not None:
        reasons.append(Reason(text=f"Price on this page: ${p.price:,.2f}", tone="unknown"))
    reasons.append(Reason(text="Other-store comparison covers our demo catalog only", tone="unknown"))
    res.best_price = BestPrice(status="unknown", headline="Demo catalog only", reasons=reasons)
    return res


def _load(day: int) -> tuple[list[dict], Optional[str], str]:
    try:
        return repo.listings(day)
    except (ZetarisError, ValueError) as e:
        log.warning("listings from Zetaris failed, using the catalog fixture: %s", e)
        return repo._catalog_listings(day), None, "fixture (Zetaris unavailable)"


def _identify(rows: list[dict], here: str, page: PageFacts) -> tuple[Optional[dict], Optional[dict]]:
    """(the page's own listing or None, the item to match: a row giving model_key/family/condition)."""
    keys = {catalog.key(x) for x in (page.product.sku, page.product.model) if x}
    keys.discard("")
    if not keys:  # no SKU/model read from the page: a Store Lab store with a single listing still identifies the item
        mine = [r for r in rows if r["store_id"] == here]
        return (mine[0], mine[0]) if len(mine) == 1 else (None, None)
    own = next((r for r in rows if r["store_id"] == here and keys & {r["sku_key"], r["model_key"]}), None)
    if own:
        return own, own
    other = next((r for r in rows if keys & {r["sku_key"], r["model_key"]}), None)
    return None, ({**other, "condition": "new"} if other else None)  # a store outside the catalog: assume new


def _judge(r: dict, item: dict) -> tuple[str, str, str]:
    """(status for the evidence table, finding status, short reason) for one candidate listing."""
    if r["model_key"] != item["model_key"]:
        return "rejected", "rejected", f"{r['model']} is a different model, not the same item"
    if r["condition"] != item["condition"]:
        return "rejected", "rejected", f"{r['condition']}, not the same item"
    if r["trust"] == "scam":
        return "rejected", "rejected", "flagged as a scam in our store data"
    if r["trust"] != "ok":
        return "rejected", "rejected", "not a trusted store (risk signals in our store data)"
    if r["status"] == "outage":
        return "skipped", "unverified", "site down during the check"
    return "ok", "", ""


def assess(check_id: str, url: str, page: PageFacts, day: int = 1) -> PriceResult:
    """The Price Agent's findings and "Best safe price" verdict for one page on demo `day` (1..9). Never raises."""
    if not is_storelab(url):
        return _real_site(check_id, page)
    try:
        return _assess_storelab(check_id, url, page, day)
    except Exception as e:  # never break a check
        log.exception("price agent failed")
        res = PriceResult(source="error")
        res.findings.append(_f(check_id, "best_price", "unverified",
                               f"Couldn't compare other stores ({type(e).__name__}).", confidence=0.0))
        res.best_price = BestPrice(status="unknown", headline="Couldn't compare",
                                   reasons=[Reason(text="Other-store comparison failed this time", tone="unknown")])
        return res


def _assess_storelab(check_id: str, url: str, page: PageFacts, day: int) -> PriceResult:
    rows, sql, source = _load(day)
    res = PriceResult(sql=sql, source=source)
    here = store_key(url, page).removeprefix("storelab/")
    own, item = _identify(rows, here, page)
    if not item:
        res.findings.append(_f(check_id, "catalog_match", "unverified",
                               "This item isn't in the Store Lab catalog, so other stores weren't compared.",
                               _sql_ev(res, len(rows)), confidence=0.0))
        res.best_price = BestPrice(status="unknown", headline="Not in catalog",
                                   reasons=[Reason(text="Other-store comparison covers our demo catalog only", tone="unknown")])
        return res

    family = [r for r in rows if r["family"] == item["family"] and r["store_id"] != here]
    same = [r for r in family if r["model_key"] == item["model_key"]]
    res.findings.append(_f(check_id, "catalog_match", "confirmed",
                           f"Same model ({item['model']}, {item['condition']}) found at {len(same)} other Store Lab "
                           f"store{'s' if len(same) != 1 else ''}" + (" in Zetaris." if sql else "."),
                           _sql_ev(res, len(rows)), confidence=0.95))
    ok, groups = [], {}
    for r in family:
        status, f_status, why = _judge(r, item)
        res.stores.append({"store": r["store"], "total": r["true_total"], "status": status,
                           "note": f"{why[0].upper()}{why[1:]}" if why else f"{_math(r)} · {r['condition']}"})
        if status == "ok":
            ok.append(r)
        else:
            groups.setdefault((why, f_status), []).append(r)
    skipped = []
    for (why, f_status), group in groups.items():
        if group[0]["model_key"] == item["model_key"]:
            skipped.append((group, why))
        if len(group) > 2:  # many stores with the same reason: one finding, not a wall of rows
            text = f"{len(group)} stores skipped ({', '.join(r['store'] for r in group)}): {why}."
        else:
            text = " ".join(f"{r['store']}, {money(r['true_total'])}: {why}." for r in group)
        res.findings.append(_f(check_id, "candidate", f_status, text, [{"kind": "url", "ref": r["url"]} for r in group],
                               confidence=0.9 if f_status == "rejected" else 0.0))

    best = min(ok, key=lambda r: (r["true_total"], r["store"])) if ok else None
    here_best = own is not None and own["trust"] == "ok" and own["status"] != "outage" and \
        (best is None or own["true_total"] <= best["true_total"])
    if here_best:
        best = own
    skipped_text = _and([f"{len(g)} stores ({_short(why)})" if len(g) > 2 else
                         _and([f"{r['store']} ({_short(why)})" for r in g]) for g, why in skipped])
    if not best:
        res.best_price = BestPrice(status="unknown", headline="No safe match",
                                   reasons=[Reason(text="No trusted store has the exact same item", tone="unknown")])
        return res

    for row in res.stores:
        if row["store"] == best["store"]:
            row["status"] = "chosen"
    if here_best:
        res.stores.insert(0, {"store": best["store"], "total": best["true_total"], "status": "chosen",
                              "note": f"This store · {_math(best)} · {best['condition']}"})
    price_row = {"kind": "price_row", "ref": f"listing:{best['store_id']}:{best['product_id']}:day{day}",
                 "label": f"{_math(best)} = {money(best['true_total'])}"}
    where = "This store already has the best safe price" if here_best else f"{best['store']}, {money(best['true_total'])}"
    res.findings.append(_f(check_id, "best_price", "confirmed",
                           f"{where}: exact match ({item['condition']}, same model), {_math(best)}."
                           + (f" Cheapest true total of {len(ok) + (1 if here_best else 0)} trusted stores." if ok else ""),
                           [{"kind": "url", "ref": best["url"]}, price_row] + _sql_ev(res, len(rows)), confidence=0.94))
    reasons = [Reason(text=f"Exact match: same model, {item['condition']}", tone="good", proof="stores"),
               Reason(text=_math(best), tone="good", proof="stores")]
    if skipped_text:
        reasons.append(Reason(text=f"Skipped {skipped_text}", tone="caution", proof="stores"))
    res.best_price = BestPrice(status="good", headline=f"{best['store']} · {money(best['true_total'])}", store=best["store"],
                               total=best["true_total"], url=best["url"], reasons=reasons)
    if not here_best:
        res.alt = {"store": best["store"], "total": best["true_total"], "url": best["url"]}
    rank = {"chosen": 0, "ok": 1, "rejected": 2, "skipped": 3}
    res.stores.sort(key=lambda s: (rank[s["status"]], s["total"] if s["total"] is not None else 1e9))
    return res


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else ""


def _short(why: str) -> str:
    return {"flagged as a scam in our store data": "scam", "site down during the check": "site down",
            "not a trusted store (risk signals in our store data)": "risky store"}.get(why, why.split(",")[0])


def best_for(model: str, condition: str = "new", day: int = 1) -> Optional[dict]:
    """The best safe listing for a model on demo `day` across all Store Lab stores (the wishlist watch uses this)."""
    rows, _, _ = _load(day)
    want = catalog.key(model)
    ok = [r for r in rows if r["model_key"] == want and r["condition"] == condition and r["trust"] == "ok"
          and r["status"] != "outage"]
    return min(ok, key=lambda r: (r["true_total"], r["store"])) if ok else None
