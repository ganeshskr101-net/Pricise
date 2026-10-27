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
    dropped: list[dict] = field(default_factory=list)  # online offers left out, each with its reason (logged too)
    online: Optional[dict] = None    # the online comparison for the evidence page: query, shown, dropped (grouped), SQL


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


def offers_fetched(product_key: str, source: str) -> list[dict]:
    """When the compared offers were fetched: the USL's snapshot (and Pricise cache) times, or now for a live search."""
    from backend.data import repo

    if source == "zetaris usl":
        return list(repo.USL_FETCHED.get(product_key) or [])
    return [{"what": "Google Shopping (live search)", "at": time.time()}] if source == "serpapi" else []


def compared(res: PriceResult) -> bool:
    """A comparison result exists: credible stores were shown, or a cheaper offer went to the Skeptic."""
    return bool((res.online or {}).get("shown")) or any(f.type in ("best_price", "lowest_price") for f in res.findings)


def _real_site(check_id: str, page: PageFacts, url: str = "", propose: bool = False) -> PriceResult:
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
        res.best_price = BestPrice(status="unknown", headline="Compare", reasons=reasons)  # the Compare prices links follow
        _online(check_id, page, host, res, propose)
        if not compared(res):  # only when nothing was compared: the online search had no result for this check
            res.findings.insert(0, _f(check_id, "catalog_only", "unverified",
                                      f"{why}. Use Compare prices to search other stores.", confidence=0.0))
            res.best_price.reasons.append(Reason(text=f"{why}; use Compare prices to search other stores", tone="unknown"))
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
    _online(check_id, page, host, res, propose)
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


SHOW_MAX = 5
ALSO_CHECK = ("bestbuy.com", "walmart.com", "target.com", "amazon.com")  # named in "Also checked" when they had no offer
DROP_GROUPS = [("Under 50% (not a real price)", ("under 50%",)),  # first: its reason text mentions "monthly" too
               ("Installment or monthly price", ("/mo", "per month", "monthly", "installment", "payment", "affirm", "klarna",
                                                  "trade-in", "trade in", "activation")),
               ("Used or refurbished", ("pre-owned", "used", "refurbished", "renewed", "like-new", "like new", "open box")),
               ("Unknown store", ("not an allowlisted store",)), ("Marketplace seller", ("marketplace seller",)),
               ("Different item", ("not the same item",)),
               ("Failed store checks", ("trust agent",)), ("This page's own store", ("own store",))]


GROUP_WORDS = {"Under 50% (not a real price)": "not a real price", "Installment or monthly price": "monthly prices",
               "Used or refurbished": "used or refurbished", "Unknown store": "not from a credible store",
               "Marketplace seller": "marketplace sellers", "Different item": "a different item",
               "Failed store checks": "failed store checks", "Other": "other reasons"}


def rejected_line(drops: list[dict]) -> str:
    """"Rejected 39 offers: 21 not from a credible store, 12 used or refurbished, 6 marketplace sellers." (largest first)"""
    counts: dict[str, int] = {}
    for d in drops:
        g = drop_group(d["reason"])
        counts[g] = counts.get(g, 0) + 1
    parts = [f"{n} {GROUP_WORDS.get(g, g.lower())}" for g, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return f"Rejected {len(drops)} offer{'s' if len(drops) != 1 else ''} by rule: {', '.join(parts)}."


def also_checked(p, offers: list[dict], shown: set, mine: str, dropped: list[dict], blocked: list[dict]) -> list[str]:
    """"Target (not found)" only when the search had no offer at all from that store; otherwise why its offers were left
    out ("Best Buy (used or refurbished only)")."""
    from backend.data.trusted_retailers import credible_domain, display_name

    out = []
    for d in ALSO_CHECK:
        if d in shown or d == mine:
            continue
        theirs = [o for o in offers if credible_domain(o.get("source") or "", p.brand, p.name)[0] == d
                  or (o.get("source") or "").lower().startswith(display_name(d).lower())]
        if not theirs:
            out.append(f"{display_name(d)} (not found)")
            continue
        names = {o.get("source") for o in theirs}
        why = [drop_group(x["reason"]) for x in dropped if x.get("source") in names] + \
              [drop_group(x["reason"]) for x in blocked if x.get("source") in names]
        top = max(set(why), key=why.count) if why else "Other"
        out.append(f"{display_name(d)} ({GROUP_WORDS.get(top, top.lower())} only)")
    return out


def drop_group(reason: str) -> str:
    low = reason.lower()
    return next((g for g, words in DROP_GROUPS if any(w in low for w in words)), "Other")


def _online(check_id: str, page: PageFacts, host: str, res: PriceResult, propose: bool = False) -> None:
    """Search Google Shopping for this exact item (SerpApi, 6 h cache) and keep only CREDIBLE offers: allowlisted retailers
    or the brand's own or official store (backend/data/trusted_retailers.py), the same item (edition, storage, colour,
    model name). With fewer than 3 stores, the sellers list of the best-matching product is added (same rules). The Trust
    Agent checks each store. Shows 3-5 offers when there are. Read only.
    Every offer at least $1 cheaper than this page goes to the Skeptic as a best_price proposal (propose=True, the
    orchestrator) or is decided here with the Skeptic's rules (propose=False): new full-price offers (an unusually low one
    is flagged for review, not dropped) can be approved, with a caveat when one applies (a retailer-only warranty, a colour
    the listing doesn't name); open box, refurbished, used and monthly/installment offers at a credible store are proposed
    too, and the Skeptic's rule can never approve them."""
    from backend import rechecker
    from backend.agents import skeptic
    from backend.data import repo
    from backend.data.trusted_retailers import display_name, listing_url
    from backend.tools import rdap, shopping

    p = page.product
    query = shopping.query_for(p)
    offers, usl_sql = repo.usl_offers(catalog.key(query)) if query else (None, None)  # the USL first, when Zetaris has it
    source = "zetaris usl" if offers else ""
    if offers is None:
        offers, source = shopping.search(query) if query else (None, "off")
    if offers is None:
        if source == "off":
            return  # no SerpApi key: skip cleanly, the observations and Compare links stand on their own
        res.findings.append(_f(check_id, "online_offers", "unverified",
                               "Online price search didn't answer. Use Compare prices to search other stores.", confidence=0.0))
        return
    mine = rdap.registrable(host)
    kept, blocked, dropped = shopping.classify_offers(p, offers, p.price, mine)
    sellers_source, seen_offers = None, offers
    if len({o["domain"] for o in kept}) < 3:  # the sellers list: cached for 6 h, else looked up with the search's token
        extra, sellers_source = shopping.sellers_for(query, p, None if source == "zetaris usl" else offers)
        if extra:
            kept, blocked, dropped = shopping.classify_offers(p, offers + extra, p.price, mine)
            seen_offers = offers + extra
    if kept:
        tvs = _vet_domains(check_id, list(dict.fromkeys(o["domain"] for o in kept))[:SHOW_MAX + 2])
        for o in [o for o in kept if not (tvs.get(o["domain"]) or {}).get("checked") or tvs[o["domain"]]["verdict"] != "safe"]:
            kept.remove(o)
            dropped.append({"source": o["source"], "price": o["price"], "title": o["title"][:90], "reason": "didn't pass the Trust Agent's checks"})
            log.info("price: dropped offer %s $%s: didn't pass the Trust Agent's checks", o["source"], o["price"])
    res.dropped = dropped + [{"source": o["source"], "price": o["price"], "title": o["title"][:90], "reason": o["reason"]}
                             for o in blocked]
    show = []  # one per store, cheapest first; a store's own product link beats a search link
    for o in kept:
        same = next((s for s in show if s["domain"] == o["domain"]), None)
        direct = o.get("link") and "google.com" not in o["link"]
        if same:
            if direct and abs(same["total"] - o["total"]) < 0.005 and not same["direct"]:
                same.update(url=o["link"], direct=True)
            continue
        if len(show) < SHOW_MAX:
            show.append({**o, "store": display_name(o["domain"]), "direct": bool(direct),
                         "url": o["link"] if direct else listing_url(o["domain"], query)})
    shown = {s["domain"] for s in show}
    also = also_checked(p, seen_offers, shown, mine, dropped, blocked) if len(show) < 3 else []  # search + sellers list
    res.online = {"query": query, "source": source, "sellers_source": sellers_source,
                  "sql": (usl_sql if source == "zetaris usl" else
                          repo.offers_sql(catalog.key(query), time.time() - shopping.CACHE_S) if source == "cache" and zetaris_on() else None),
                  "shown": [{"store": s["store"], "price": s["price"], "shipping": s["shipping_note"], "total": s["total"],
                             "condition": "new", "title": s["title"], "url": s["url"], "seller": s.get("seller"),
                             "low": s.get("low", False), "fetched_at": s.get("fetched_at")} for s in show],
                  "fetched": offers_fetched(catalog.key(query), source),
                  "usl_fallback": (repo.USL_ERRORS.get(catalog.key(query)) if usl_sql and source != "zetaris usl" else None),
                  "dropped": [{**d, "group": drop_group(d["reason"])} for d in res.dropped], "also_checked": also,
                  "offers_seen": len(offers)}
    ev_search = {"kind": "log", "ref": f"google_shopping:{query}",
                 "label": f"{len(offers)} offers ({source}); {len(show)} credible stores, {len(res.dropped)} dropped"}
    if res.online["sql"]:
        ev_search = {"kind": "sql", "ref": res.online["sql"], "label": ev_search["label"]}
    also_text = f" Also checked: {'; '.join(also)}." if also else ""
    rule_drops = [d for d in dropped if drop_group(d["reason"]) != "This page's own store"]
    if rule_drops:  # one line for the panel; the full list, grouped, is on the evidence page
        res.findings.append(_f(check_id, "offers_filtered", "confirmed", rejected_line(rule_drops),
                               [{"kind": "log", "ref": f"google_shopping:{query}", "label": "Rejected offers, by reason"}],
                               confidence=0.9))
    price = p.price
    cheaper = [s for s in show if price is not None and s["total"] <= price - 1]
    cheaper += _blocked_cheaper(blocked, price, query)
    if not show and not cheaper:
        res.findings.append(_f(check_id, "online_offers", "unverified",
                               f"No trusted store had this exact item at a real price ({len(offers)} offers checked).{also_text} "
                               "Use Compare prices to search other stores.", [ev_search], confidence=0.0))
        return
    rows = [{"store": s["store"], "total": s["total"], "status": "ok",
             "note": f"{s['shipping_note']} · {s['title'][:60]}" + (" · unusually low, for the Skeptic" if s.get("low") else "")}
            for s in show]
    listed = ", ".join(f"{s['store']} {money(s['total'])}" for s in show)
    links = [{"kind": "url", "ref": s["url"], "label": f"{s['store']} {money(s['total'])}"} for s in show]
    level = [s for s in show if s not in cheaper]  # same price as here or dearer: what this page is compared with
    same = [s["store"] for s in level if price is not None and abs(s["total"] - price) < 1]
    if level:
        text = (f"Same price at {_and(same)}, this page is as good as it gets." if same
                else "This is the lowest price among trusted stores. Also at: "
                     + ", ".join(f"{s['store']} {money(s['total'])}" for s in level) + ".")
        lowest = _f(check_id, "lowest_price", "confirmed", text + also_text, links + [ev_search], confidence=0.75)
    # "As good as it gets" only when no cheaper offer is approved: a cheaper NEW offer may be, so with one the line waits
    # for the Skeptic (in place below) or is left out (the orchestrator decides later); the same-price stores stay in
    # the comparison either way. Open box, refurbished and monthly offers are never approved: they don't hold it back.
    approvable = [o for o in cheaper if (o.get("kind") or "new") == "new"]
    if level and not approvable:
        res.findings.append(lowest)
    res.best_price = BestPrice(status="good" if level else "unknown",
                               headline="As good as it gets" if same else "Lowest trusted price" if level else "Compare",
                               reasons=[Reason(text=f"Credible stores: {listed}; this page {money(price)}" if show
                                               else "No credible store had this item new", tone="good" if show else "unknown",
                                               proof="stores")])
    res.alt = {"kind": "this_page"} if level else None
    res.stores = rows + res.stores
    if not cheaper:
        return
    caveat_ctx = {"brand": p.brand, "colours": shopping._variant(p.name or "")["color"], "median": _median(show)}
    props = [_offer_proposal(check_id, o, price, caveat_ctx, links, ev_search, rechecker) for o in cheaper[:MAX_PROPOSALS]]
    if propose:
        res.findings.extend(props)
        return
    decided = []
    for f in props:  # no orchestrator here: the Skeptic decides in place, the same way it would there
        ref = rechecker.parse_ref(f)
        labels, caveats = rechecker.labels_for(ref["offer"]), rechecker.caveats_of(f)
        if ref["offer"].get("low") == "1":  # unusually low: the deep lane decides, the hard rules validate
            ctx = rechecker.review_context(f, ref, page.model_dump(), show, "safe", ["passed the Trust Agent's checks"])
            result = skeptic.decide_low_offer(f, ref["match"], "safe", labels, caveats, skeptic.review_low_offer(ctx))
        else:
            result = skeptic.check_best_price(f, ref["match"], "safe", labels, caveats)  # stores here passed Trust already
        decided.append(f.model_copy(update={"status": result.status, "challenged_by": "skeptic", "rounds": 1,
                                            "summary": rechecker.offer_summary(f, result),
                                            "model": result.decided_by if result.decided_by != "rules" else f.model}))
        caveat_ctx.setdefault("rechecks", {})[ref["ref"]] = {"reason": result.reason, "caveat": result.caveat}
    res.findings.extend(decided)
    if level and approvable and not any(d.status == "confirmed" for d in decided):
        res.findings.append(lowest)  # the Skeptic approved none of the cheaper offers: this page is as good as it gets
    res.best_price, res.alt = rechecker.best_price_on_card({"page": page.model_dump(), "rechecks": caveat_ctx.get("rechecks")},
                                                           decided, res.best_price, res.alt)


MAX_PROPOSALS = 5  # cheaper offers sent to the Skeptic per page (each may cost one deep-lane wording call)


def _median(show: list[dict]) -> Optional[float]:
    import statistics

    return statistics.median(s["total"] for s in show) if len(show) >= 2 else None


def _blocked_cheaper(blocked: list[dict], price: Optional[float], query: str) -> list[dict]:
    """Open box, refurbished, used or monthly offers at a credible store, cheaper than this page: one per store and kind."""
    from backend.data.trusted_retailers import display_name, listing_url

    out, seen = [], set()
    for o in sorted(blocked, key=lambda o: o["total"]):
        if price is None or o["total"] > price - 1 or (o["domain"], o["kind"], o.get("other_model")) in seen:
            continue
        seen.add((o["domain"], o["kind"], o.get("other_model")))
        direct = o.get("link") and "google.com" not in o["link"]
        out.append({**o, "store": display_name(o["domain"]), "url": o["link"] if direct else listing_url(o["domain"], query)})
    return out


def _caveats(o: dict, ctx: dict) -> list[str]:
    from backend.data.trusted_retailers import warranty_note
    from backend.tools import shopping

    out = []
    note = warranty_note(o["domain"], ctx.get("brand"))
    if note:
        out.append(note)
    if ctx.get("colours") and not shopping._variant(o.get("title") or "")["color"]:
        out.append(f"colour not named in the listing: choose {'/'.join(sorted(c.capitalize() for c in ctx['colours']))}")
    return out


def _offer_proposal(check_id: str, o: dict, price: Optional[float], ctx: dict, links: list[dict], ev_search: dict,
                    rechecker) -> Finding:
    kind = o.get("kind") or "new"
    seller = f", sold by {o['seller']} (the brand's official store on {o['store']})" if o.get("seller") else ""
    what = (f"new{seller}" + (", unusually low price" if o.get("low") else "")) if kind == "new" else kind
    if o.get("other_model"):
        what = f"{o['other_model'][0]}, {what}"
    save = f" (save {money(price - o['total'])})" if price else ""
    ref = rechecker.offer_ref(o["url"], {**o, "save": round(price - o["total"], 2) if price else None},
                              model=not o.get("other_model"), variant=True)
    ev = [{"kind": "url", "ref": o["url"], "label": f"{o['store']} {money(o['total'])}"},
          {"kind": "price_row", "ref": ref, "label": "Exact match"}]
    ev += [{"kind": "text", "ref": f"{rechecker.CAVEAT} {c}", "label": "Caveat"} for c in (_caveats(o, ctx) if kind == "new" else [])]
    return _f(check_id, "best_price", "proposed",
              f"{o['store']} {money(o['total'])}: {what}, cheaper than here{save}. Proposed for the Skeptic to check.",
              ev + [e for e in links if e["ref"] != o["url"]][:3] + [ev_search], confidence=0.6)


def zetaris_on() -> bool:
    from backend.data import zetaris

    return zetaris.use_zetaris()


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
        return await asyncio.to_thread(_real_site, check_id, page, url, propose_lookalikes)
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
