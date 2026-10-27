"""Price Agent: answers "Best safe price?" by finding the exact same item (model + condition) at a trusted store, by true total.

Store Lab pages (localhost /storelab/): one Lightning SQL query through Zetaris (backend/data/repo.py:listings) returns every
Store Lab listing with its price on the demo day and its true total = price + shipping + checkout fees - card offers.
Explicit rules (no model) then decide, and every decision is a finding:
  - a different model in the same family (e.g. WH-1000XM4)  -> rejected: not the same item
  - a different condition (refurbished vs new)               -> rejected: not the same item
  - store safety, with use_trust=True (AGENT_TRUST=real): the Trust Agent vets every candidate store
      unsafe -> rejected (challenged_by "trust") · caution -> rejected ("be careful") · didn't answer -> unverified, skipped
    with use_trust=False (AGENT_TRUST=stub): the seeded flags in the catalog (scam / risky -> rejected) are the fallback
  - a store that is down                                     -> unverified, skipped
  - the cheapest remaining true total wins. Evidence cites the SQL.
propose_lookalikes=True (the orchestrator, where the Skeptic re-checks every best_price): a cheaper safe listing of a
different model or condition is not rejected here but proposed as its own best_price finding (status "proposed", with the
exact-match ref backend/rechecker.py reads), so the Skeptic decides "same item?". The winner is unchanged.
Real sites: read only, no scraping of other stores: one "unverified" finding that the comparison covers our demo catalog only.
Never raises: if Zetaris fails it falls back to the in-memory catalog and says so; anything else becomes an unverified finding.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from backend.contracts import BestPrice, Evidence, Finding, PageFacts, Reason
from backend.data import catalog, repo
from backend.data.zetaris import ZetarisError
from backend.tools.common import is_storelab
from backend.tools.discount import store_key

log = logging.getLogger("pricise.price")
RULES = "rules (no model)"
TRUST_REASONS_SHOWN = 2


@dataclass
class PriceResult:
    findings: list[Finding] = field(default_factory=list)
    best_price: BestPrice = field(default_factory=lambda: BestPrice(status="unknown", headline="Not checked"))
    alt: Optional[dict] = None       # safer choice for the verdict card: {"store", "total", "url"}
    stores: list[dict] = field(default_factory=list)  # evidence page rows: {"store", "total", "note", "status"}
    sql: Optional[str] = None
    source: str = ""
    trust: str = "seeded"            # where store safety came from: "trust agent" or "seeded" (catalog flags)


@dataclass
class Judgement:
    status: str               # evidence table: ok | rejected | skipped
    finding_status: str = ""  # rejected | unverified ("" when ok)
    why: str = ""             # finding text after "Store X, $Y: "
    short: str = ""           # for "Skipped Store D (short), ..."
    by: Optional[str] = None  # challenged_by
    lookalike: bool = False   # a different model or condition: "same item?" is the Skeptic's call in propose mode


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


def _at_origin(store_url: str, page_url: str) -> str:
    """The catalog's store URL served by the same backend as the page (so Trust vets, and "Go" links, hit the running server)."""
    s, p = urlsplit(store_url), urlsplit(page_url)
    return urlunsplit((p.scheme, p.netloc, s.path, s.query, ""))


def _ago(seen_at: float, now: Optional[float] = None) -> str:
    days = int(((now or time.time()) - seen_at) // 86400)
    return "today" if days <= 0 else "yesterday" if days == 1 else f"{days} days ago"


def _real_site(check_id: str, page: PageFacts, url: str = "") -> PriceResult:
    """Real store pages: no other store is loaded (no scraping). The only comparison is with prices the shopper saw on
    other real store pages Pricise checked (price observations, read through Zetaris), same model and condition."""
    p = page.product
    res = PriceResult(source="none")
    host = (page.host or urlsplit(url).netloc).lower().removeprefix("www.")
    model_key = catalog.key(p.model or "")
    reasons = [Reason(text=f"Product: {p.name[:70]}", tone="unknown")] if p.name else []
    rows = []
    if model_key:
        try:
            rows, res.sql, res.source = repo.other_observations(model_key, repo.condition_of(p.name), host)
        except Exception as e:  # PgError / ValueError: say we couldn't look, never fail the check
            log.warning("price observations not read: %s", e)
    if not rows:
        why = ("No other store prices seen yet for this model" if model_key
               else "This page shows no model number, so prices on other stores can't be matched")
        res.findings.append(_f(check_id, "catalog_only", "unverified",
                               f"{why}. Use Compare prices to search other stores.", confidence=0.0))
        reasons.append(Reason(text=f"{why}; use Compare prices to search other stores", tone="unknown"))
        res.best_price = BestPrice(status="unknown", headline="Compare", reasons=reasons)  # the Compare prices links follow
        _online(check_id, page, host, res)
        return res
    o = rows[0]
    diff = (p.price - o["price"]) if p.price is not None else None
    vs = (f", {money(diff)} less than here" if diff and diff > 0.005
          else ", this page is cheaper" if diff is not None and diff < -0.005 else "")
    text = f"You saw this for {money(o['price'])} at {o['host']} {_ago(o['seen_at'])}{vs}. That price may have changed since."
    ev = ([{"kind": "sql", "ref": res.sql, "label": f"{len(rows)} price(s) seen on other stores ({res.source})"}] if res.sql
          else [{"kind": "log", "ref": f"observations:{res.source}", "label": f"{len(rows)} price(s) seen on other stores"}])
    res.findings.append(_f(check_id, "seen_elsewhere", "confirmed", text, ev + [{"kind": "url", "ref": o["url"]}],
                           confidence=0.7))
    reasons.append(Reason(text=text.split(". ")[0], tone="caution" if diff and diff > 0.005 else "good", proof="stores"))
    reasons.append(Reason(text=f"Seen on a page Pricise checked; {o['host']} was not re-checked now", tone="unknown"))
    cheaper = diff is not None and diff > 0.005
    res.best_price = BestPrice(status="caution" if cheaper else "good",
                               headline=f"Seen for {money(o['price'])} at {o['host']}" if cheaper else "Cheapest you've seen",
                               reasons=reasons)
    res.stores = [{"store": r["host"], "total": r["price"], "status": "ok", "note": f"Seen {_ago(r['seen_at'])}"}
                  for r in rows[:5]]
    _online(check_id, page, host, res)
    return res


# ----------------------------------------------------------------------------- online offers (Google Shopping via SerpApi)
VET_MAX = 5  # seller domains the Trust Agent checks per page (cheapest first)


def _vet_domains(check_id: str, domains: list[str]) -> dict[str, dict]:
    """Trust Agent real-site checks on each seller domain (https, registry age); blocking: runs its own event loop."""
    from backend.agents import trust as trust_agent

    async def all_():
        tvs = await asyncio.gather(*(trust_agent.assess_store(f"https://{d}/", f"{check_id}:shop:{d}") for d in domains))
        return {d: tv for d, tv in zip(domains, tvs)}

    return asyncio.run(all_())


def _online(check_id: str, page: PageFacts, host: str, res: PriceResult) -> None:
    """Search Google Shopping for this exact item (SerpApi, 6 h cache), keep the same item in new condition from OTHER
    stores, vet the sellers with the Trust Agent, and report the cheapest trusted offer below this page's price, or that
    this page is the lowest trusted price found. Read only; the stores' own sites are never loaded."""
    from backend.tools import rdap, shopping

    p = page.product
    query = shopping.query_for(p)
    offers, source = shopping.search(query) if query else (None, "off")
    if offers is None:
        if source == "off":
            return  # no SerpApi key: skip cleanly, the observations and Compare links stand on their own
        why = "Online price search didn't answer"
        res.findings.append(_f(check_id, "online_offers", "unverified", f"{why}. Use Compare prices to search other stores.",
                               confidence=0.0))
        return
    mine = rdap.registrable(host)
    matched = [o for o in offers if o["domain"] and o["domain"] != mine and shopping.same_item(p, o)[0]]
    if not matched and p.model and p.name:  # the model number found nothing the same: try the cleaned title once
        q2 = shopping.clean_title(p.name, p.brand)
        if q2 and catalog.key(q2) != catalog.key(query):
            more, _ = shopping.search(q2)
            matched = [o for o in more or [] if o["domain"] and o["domain"] != mine and shopping.same_item(p, o)[0]]
    ev_search = {"kind": "log", "ref": f"google_shopping:{query}", "label": f"{len(offers)} offers ({source}), {len(matched)} the same item"}
    if not matched:
        res.findings.append(_f(check_id, "online_offers", "unverified",
                               f"Google Shopping had no exact match for this item ({len(offers)} offers checked). "
                               "Use Compare prices to search other stores.", [ev_search], confidence=0.0))
        return
    matched.sort(key=lambda o: (o["total"], o["domain"]))
    domains = list(dict.fromkeys(o["domain"] for o in matched))[:VET_MAX]
    tvs = _vet_domains(check_id, domains)
    trusted = [o for o in matched if o["domain"] in tvs and tvs[o["domain"]]["checked"] and tvs[o["domain"]]["verdict"] == "safe"]
    rows = []
    for o in matched[:8]:
        tv = tvs.get(o["domain"])
        ok = o in trusted
        rows.append({"store": o["domain"], "total": o["total"], "status": "ok" if ok else ("rejected" if tv else "skipped"),
                     "note": f"{o['source']} · {o['shipping_note']}" + ("" if ok else " · not checked" if not tv else " · didn't pass store checks")})
    price = p.price
    cheaper = [o for o in trusted if price is not None and o["total"] < price - 0.005]
    if cheaper:
        o = cheaper[0]
        text = f"Same item for {money(o['total'])} at {o['domain']}, trusted store ({o['shipping_note']})."
        res.findings.append(_f(check_id, "best_price", "confirmed", text,
                               [{"kind": "url", "ref": o["link"] or f"https://{o['domain']}/"},
                                {"kind": "price_row", "ref": f"offer:{o['domain']}", "label": f"{o['title'][:80]} · {money(o['total'])}"},
                                ev_search], confidence=0.8))
        res.best_price = BestPrice(status="good", headline=f"{o['domain']} · {money(o['total'])}", store=o["domain"],
                                   total=o["total"], url=o["link"], reasons=[
            Reason(text=f"Same item, new: {o['title'][:70]}", tone="good", proof="stores"),
            Reason(text=f"{money(o['price'])} + {o['shipping_note']} at {o['source']}; this page: {money(price)}", tone="good", proof="stores"),
            Reason(text=f"{o['domain']} passed the Trust Agent's checks (secure, long-registered domain)", tone="good")])
        res.alt = {"store": o["domain"], "total": o["total"], "url": o["link"]}
        rows[next(i for i, r in enumerate(rows) if r["store"] == o["domain"] and r["total"] == o["total"])]["status"] = "chosen"
    elif trusted:
        also = ", ".join(f"{o['domain']} {money(o['total'])}" for o in trusted[:3])
        res.findings.append(_f(check_id, "lowest_price", "confirmed",
                               f"This is the lowest trusted price we found. Also checked: {also}.", [ev_search], confidence=0.75))
        res.best_price = BestPrice(status="good", headline="Lowest trusted price", reasons=[
            Reason(text=f"This page {money(price)}; trusted stores: {also}", tone="good", proof="stores")])
    else:
        res.findings.append(_f(check_id, "online_offers", "unverified",
                               f"Found the same item at {len(domains)} other store(s), but none passed the Trust Agent's checks.",
                               [ev_search], confidence=0.0))
    res.stores = rows + res.stores


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


# ----------------------------------------------------------------------------- store safety


async def vet_stores(check_id: str, store_urls: dict[str, str]) -> dict[str, dict]:
    """Ask the Trust Agent about each store, concurrently: {store_id: {"verdict", "reasons", "checked"}}.
    Each vet gets its own check id so a candidate's trust findings never overwrite the page's own rows."""
    from backend.agents import trust as trust_agent

    ids = list(store_urls)
    tvs = await asyncio.gather(*(trust_agent.assess_store(store_urls[s], f"{check_id}:vet:{s}") for s in ids))
    return {s: {"verdict": tv["verdict"], "reasons": tv["reasons"], "checked": tv["checked"]} for s, tv in zip(ids, tvs)}


def _trust_text(tv: dict) -> str:
    return "; ".join(r.rstrip(".") for r in tv["reasons"][:TRUST_REASONS_SHOWN])


def _safety(r: dict, trust: Optional[dict[str, dict]]) -> Optional[Judgement]:
    """None = safe enough to compete. Trust Agent verdicts when given, else the seeded catalog flags."""
    if trust is not None:
        tv = trust.get(r["store_id"])
        if tv is None or not tv["checked"]:
            return Judgement("skipped", "unverified", "the Trust Agent couldn't check this store (it did not respond)",
                             "couldn't check", "trust")
        if tv["verdict"] == "unsafe":
            return Judgement("rejected", "rejected", f"blocked by the Trust Agent ({_trust_text(tv)})", "unsafe store", "trust")
        if tv["verdict"] == "caution":
            return Judgement("rejected", "rejected", f"the Trust Agent says be careful ({_trust_text(tv)})", "be careful",
                             "trust")
        return None
    if r["trust"] == "scam":
        return Judgement("rejected", "rejected", "flagged as a scam in our store data", "scam")
    if r["trust"] != "ok":
        return Judgement("rejected", "rejected", "not a trusted store (risk signals in our store data)", "risky store")
    return None


def _judge(r: dict, item: dict, trust: Optional[dict[str, dict]]) -> Judgement:
    if r["model_key"] != item["model_key"]:
        return Judgement("rejected", "rejected", f"{r['model']} is a different model, not the same item", r["model"],
                         lookalike=True)
    if r["condition"] != item["condition"]:
        return Judgement("rejected", "rejected", f"{r['condition']}, not the same item", r["condition"], lookalike=True)
    unsafe = _safety(r, trust)
    if unsafe:
        return unsafe
    if r["status"] == "outage":
        return Judgement("skipped", "unverified", "site down during the check", "site down")
    return Judgement("ok")


def _own_is_safe(own: dict, trust: Optional[dict[str, dict]]) -> bool:
    if own["status"] == "outage":
        return False
    if trust is not None:
        tv = trust.get(own["store_id"])
        return bool(tv and tv["checked"] and tv["verdict"] == "safe")
    return own["trust"] == "ok"


# ----------------------------------------------------------------------------- the agent


async def assess_async(check_id: str, url: str, page: PageFacts, day: int = 1, use_trust: bool = False,
                       propose_lookalikes: bool = False) -> PriceResult:
    """The Price Agent's findings and "Best safe price" verdict for one page on demo `day` (1..9). Never raises.
    use_trust=True asks the Trust Agent about every candidate store (AGENT_TRUST=real); False uses the seeded flags."""
    if not is_storelab(url):
        return await asyncio.to_thread(_real_site, check_id, page, url)
    try:
        rows, sql, source = await asyncio.to_thread(_load, day)
        rows = [{**r, "url": _at_origin(r["url"], url)} for r in rows]
        here = store_key(url, page).removeprefix("storelab/")
        own, item = _identify(rows, here, page)
        trust = None
        if use_trust and item:
            urls = {r["store_id"]: r["url"] for r in rows if r["family"] == item["family"]}
            trust = await vet_stores(check_id, urls)
        res = _decide(check_id, day, rows, sql, source, here, own, item, trust, propose_lookalikes)
        res.trust = "trust agent" if trust is not None else "seeded"
        return res
    except Exception as e:  # never break a check
        log.exception("price agent failed")
        res = PriceResult(source="error")
        res.findings.append(_f(check_id, "best_price", "unverified",
                               f"Couldn't compare other stores ({type(e).__name__}).", confidence=0.0))
        res.best_price = BestPrice(status="unknown", headline="Couldn't compare",
                                   reasons=[Reason(text="Other-store comparison failed this time", tone="unknown")])
        return res


def assess(check_id: str, url: str, page: PageFacts, day: int = 1, use_trust: bool = False) -> PriceResult:
    """Blocking version of assess_async, for callers without an event loop (scripts, tests)."""
    return asyncio.run(assess_async(check_id, url, page, day, use_trust))


def _decide(check_id: str, day: int, rows: list[dict], sql: Optional[str], source: str, here: str,
            own: Optional[dict], item: Optional[dict], trust: Optional[dict[str, dict]],
            propose_lookalikes: bool = False) -> PriceResult:
    res = PriceResult(sql=sql, source=source)
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
    ok, judged = [], []
    for r in family:
        j = _judge(r, item, trust)
        res.stores.append({"store": r["store"], "total": r["true_total"], "status": j.status,
                           "note": f"{j.why[0].upper()}{j.why[1:]}" if j.why else f"{_math(r)} · {r['condition']}"})
        if j.status == "ok":
            ok.append(r)
        else:
            judged.append((r, j))

    best = min(ok, key=lambda r: (r["true_total"], r["store"])) if ok else None
    here_best = own is not None and _own_is_safe(own, trust) and (best is None or own["true_total"] <= best["true_total"])
    if here_best:
        best = own
    groups, proposals = {}, []
    for r, j in judged:
        cheaper = best is None or r["true_total"] < best["true_total"]
        if propose_lookalikes and j.lookalike and cheaper and _safety(r, trust) is None and r["status"] != "outage":
            proposals.append((r, j))
        else:
            groups.setdefault((j.why, j.finding_status, j.short, j.by), []).append(r)
    skipped = [([r], j.short) for r, j in proposals if r["model_key"] == item["model_key"]]
    for (why, f_status, short, by), group in groups.items():
        if group[0]["model_key"] == item["model_key"]:
            skipped.append((group, short))
        if len(group) > 2:  # many stores with the same reason: one finding, not a wall of rows
            text = f"{len(group)} stores skipped ({', '.join(r['store'] for r in group)}): {why}."
        else:
            text = " ".join(f"{r['store']}, {money(r['true_total'])}: {why}." for r in group)
        extra = {"challenged_by": by, "rounds": 1} if by else {}
        res.findings.append(_f(check_id, "candidate", f_status, text, [{"kind": "url", "ref": r["url"]} for r in group],
                               confidence=0.9 if f_status == "rejected" else 0.0, **extra))
    for r, j in proposals:
        res.findings.append(_proposal(check_id, day, r, item, j, res, len(rows)))

    skipped_text = _and([f"{len(g)} stores ({short})" if len(g) > 2 else _and([f"{r['store']} ({short})" for r in g])
                         for g, short in skipped])
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
    stores_ok = "stores the Trust Agent checked" if trust is not None else "trusted stores"
    where = "This store already has the best safe price" if here_best else f"{best['store']}, {money(best['true_total'])}"
    res.findings.append(_f(check_id, "best_price", "confirmed",
                           f"{where}: exact match ({item['condition']}, same model), {_math(best)}."
                           + (f" Cheapest true total among {len(ok) + (1 if here_best else 0)} {stores_ok}." if ok else ""),
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


def _proposal(check_id: str, day: int, r: dict, item: dict, j: Judgement, res: PriceResult, n: int) -> Finding:
    """A cheaper look-alike, proposed as a best price for the Skeptic to accept or reject (propose_lookalikes=True)."""
    from backend.rechecker import exact_ref  # the exact-match ref shape the Skeptic's rechecker reads

    ref = exact_ref(r["url"], r["model_key"] == item["model_key"], r["condition"] == item["condition"], r["true_total"])
    what = r["model"] if r["model_key"] != item["model_key"] else r["condition"]
    return _f(check_id, "best_price", "proposed",
              f"{r['store']}, {money(r['true_total'])}: cheaper look-alike ({what}). Proposed for the Skeptic to check it is "
              f"the same item.",
              [{"kind": "url", "ref": r["url"]},
               {"kind": "price_row", "ref": f"listing:{r['store_id']}:{r['product_id']}:day{day}",
                "label": f"{_math(r)} = {money(r['true_total'])}"},
               {"kind": "price_row", "ref": ref, "label": "Exact match"}] + _sql_ev(res, n), confidence=0.5)


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else ""


def best_for(model: str, condition: str = "new", day: int = 1, origin: Optional[str] = None,
             use_trust: bool = False) -> Optional[dict]:
    """The best safe listing for a model on demo `day` across all Store Lab stores (the wishlist watch uses this).
    use_trust=True vets the stores with the Trust Agent at `origin` (the running backend); blocking: no event loop here."""
    rows, _, _ = _load(day)
    want = catalog.key(model)
    rows = [r for r in rows if r["model_key"] == want and r["condition"] == condition and r["status"] != "outage"]
    trust = None
    if use_trust and origin:
        rows = [{**r, "url": _at_origin(r["url"], origin)} for r in rows]
        trust = asyncio.run(vet_stores("wishlist", {r["store_id"]: r["url"] for r in rows}))
    ok = [r for r in rows if _safety(r, trust) is None]
    return min(ok, key=lambda r: (r["true_total"], r["store"])) if ok else None
