"""Fake vs real discount, decided from price history only.

Rule (explicit, no model): the discount is FAKE when the was-price appeared on <= 7 of the last 90 days AND the sale price
equals the typical (median) price over those days. No history -> "claimed_discount", unverified. Careful wording on real stores.
"""
from __future__ import annotations

import re
import statistics
from datetime import date, timedelta
from typing import Optional
from urllib.parse import urlparse

from backend.contracts import PageFacts
from backend.data.history import get_price_history
from backend.tools.common import ToolResult, finding, is_storelab, money

WINDOW_DAYS = 90
MIN_SPAN_DAYS = 30  # the page's own history or past observations: fewer days than this can't judge a discount
MAX_DAYS_AT_WAS = 7
PCT_RE = re.compile(r"(\d{1,2})\s?%")


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.5, 0.01 * max(a, b))


def store_key(url: str, page: PageFacts) -> str:
    """Store Lab stores share one host, so key them by path: storelab/audiodealz, bench/deal. Real stores: the host."""
    if is_storelab(url):
        parts = [p for p in urlparse(url).path.split("/") if p]
        for root in ("storelab", "bench"):
            if root in parts and parts.index(root) + 1 < len(parts):
                return f"{root}/{parts[parts.index(root) + 1]}"
    return (page.host or urlparse(url).netloc).lower()


# A store credit card's signup offer ("Get $50 off instantly: pay $64.95 upon approval for Amazon Visa") is not this item's
# price and not a discount on it. The extension already skips these; this guard keeps any that slip through out.
CARD_OFFER_RE = re.compile(r"upon approval|\bvisa\b|mastercard|credit card|card ?member|store card|apply now", re.I)


def discount_claims(page: PageFacts) -> list:
    return [s for s in page.signals if s.type == "claimed_discount" and not CARD_OFFER_RE.search(s.text or "")]


def claimed_was(page: PageFacts) -> tuple[Optional[float], Optional[int]]:
    """The was-price the page claims (from discount signals or the product), plus a claimed % if shown."""
    price = page.product.price
    amounts = [s.amount for s in discount_claims(page) if s.amount and (price is None or s.amount > price)]
    was = max(amounts) if amounts else page.product.was_price
    pct = None
    for s in discount_claims(page):
        m = PCT_RE.search(s.text)
        if m:
            pct = int(m.group(1))
            break
    if was is None and pct and price:
        was = round(price / (1 - pct / 100), 2)
    return was, pct


def _sql_evidence(rows) -> Optional[dict]:
    """When the history came from Zetaris: the query itself, labelled with the rows it returned (price x days)."""
    if not getattr(rows, "sql", None):
        return None
    counts: dict[float, int] = {}
    for _, p in rows:
        counts[p] = counts.get(p, 0) + 1
    shown = ", ".join(f"{money(p)} x {n}" for p, n in sorted(counts.items(), key=lambda kv: -kv[1]))
    return {"kind": "sql", "ref": rows.sql, "label": f"Zetaris returned {len(rows)} daily rows: {shown}"}


def _history(url: str, page: PageFacts):
    host = store_key(url, page)
    p = page.product
    for ident in (p.sku, p.model, p.name):
        if ident:
            rows = get_price_history(host, ident)
            if rows:
                return host, ident, rows
    if not is_storelab(url):  # real pages: the page's own "Price history", else prices seen here before
        from backend.data import catalog
        from backend.data.history import observation_history, page_history

        rows = page_history(page.signals) or observation_history(host.removeprefix("www."), catalog.key(p.model or ""), url)
        if rows:
            return host, p.sku or p.model or "page", rows
    return host, None, None


def check_discount(check_id: str, url: str, page: PageFacts) -> ToolResult:
    out = ToolResult()
    claims = discount_claims(page)
    was, pct = claimed_was(page)
    price = page.product.price
    if not claims and was is None:
        return out  # the page makes no discount claim
    seen, sel = set(), []
    for s in claims:
        if s.selector and s.selector not in seen:
            seen.add(s.selector)
            sel.append(s.selector)
    claim_text = " ".join((claims[0].text if claims else f"was {money(was)}").split())[:80]

    host, ident, rows = _history(url, page)
    source = getattr(rows, "source", "")
    short = bool(rows) and source in ("page", "observations") and len(rows) < MIN_SPAN_DAYS
    if (short or not rows) and not is_storelab(url) and price is not None and was is not None:
        what = ("the page's own price history" if source == "page" else "Pricise's saved prices for this page") if rows else ""
        history = (f"Only {len(rows)} days of {what}, too few to judge it." if rows
                   else "Pricise has no saved price history for this item.")
        market = market_check(check_id, page, price, was, claim_text, sel, history)
        if market is not None:
            out.findings.append(market)
            return out
    if short:
        what = "the page's own price history" if source == "page" else "the prices seen here before"
        out.findings.append(finding(check_id, "claimed_discount", "unverified",
                                    f"The page claims a reduced price ({claim_text}). Only {len(rows)} days of {what}, too "
                                    f"little to check it, so this is unverified.",
                                    [{"kind": "selector", "ref": r, "label": "Discount claim" if i == 0 else ""} for i, r in enumerate(sel)],
                                    confidence=0.4))
        return out
    if not rows or price is None or was is None:
        why = "no price history found for this item" if not rows else "the page's prices couldn't be read"
        out.findings.append(finding(check_id, "claimed_discount", "unverified",
                                    f"The page claims a reduced price ({claim_text}). {why.capitalize()}, so this is unverified.",
                                    [{"kind": "selector", "ref": r, "label": "Discount claim" if i == 0 else ""} for i, r in enumerate(sel)],
                                    confidence=0.4))
        return out

    since = date.today() - timedelta(days=WINDOW_DAYS - 1)
    recent = [p for d, p in rows if d >= since]
    typical = statistics.median(recent)
    days_at_was = sum(1 for p in recent if _same(p, was))
    days_at_price = sum(1 for p in recent if _same(p, price))
    pct_shown = pct or round((1 - price / was) * 100)
    out.evidence_data["price_history"] = {
        "store": host.split("/")[-1].replace("audiodealz", "AudioDealz"),
        "label": "Synthetic history (Store Lab)" if is_storelab(url) else
                 {"page": f"Price history shown on the {host} page", "observations": f"Prices you saw on {host}"}.get(
                     getattr(rows, "source", ""), f"Price history for {host}"),
        "points": recent, "claimed_was": was, "days_at_claim": days_at_was, "days_at_price": days_at_price,
    }
    rows_ev = {"kind": "price_row", "ref": f"history:{host}:{ident}",
               "label": f"{money(price)} on {days_at_price} of {len(recent)} days; {money(was)} on {days_at_was} days; median {money(typical)}"}
    sql_ev = _sql_evidence(rows)
    if sql_ev:
        out.evidence_data["price_history"].update(sql=sql_ev["ref"], source="Zetaris")

    if days_at_was <= MAX_DAYS_AT_WAS and _same(price, typical):
        summary = (f"\"{pct_shown}% off\" is not real: the price was {money(price)} for {days_at_price} of the last {len(recent)} days. "
                   f"{money(was)} appeared for {days_at_was} day{'s' if days_at_was != 1 else ''} only.")
        if not is_storelab(url):
            summary = (f"This was-price isn't supported by the price history we found: {money(price)} was the usual price "
                       f"({days_at_price} of {len(recent)} days) and {money(was)} appeared on only {days_at_was} days.")
        ev = [{"kind": "selector", "ref": r, "label": f"Fake {pct_shown}% off" if i == 0 else ""} for i, r in enumerate(sel)]
        out.findings.append(finding(check_id, "fake_discount", "confirmed", summary, ev + [rows_ev] + ([sql_ev] if sql_ev else []),
                                    confidence=0.93))
    else:
        summary = (f"The price history supports this discount: {money(was)} was charged on {days_at_was} of the last {len(recent)} days "
                   f"and the usual price is {money(typical)}.")
        out.findings.append(finding(check_id, "claimed_discount", "confirmed", summary, [rows_ev], confidence=0.8))
    return out


INFLATED = 0.90  # every credible store at or under 90% of a list price: the list price looks inflated


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else ""


def market_check(check_id: str, page: PageFacts, price: float, was: float, claim_text: str, sel: list[str],
                 history: str):
    """A real page's "List Price" / "Was" claim against (a) the manufacturer's launch price (backend/data/msrp.py) and
    (b) what credible stores charge now (the Price Agent's Google Shopping search, shared through its 6 h cache):
      - list price = MSRP          -> claimed_discount, confirmed: real against MSRP, then whether others charge the same or less
      - credible stores all at or under 90% of a non-MSRP list price -> inflated_discount, PROPOSED (never "fake" without
        price-history proof)
      - otherwise                  -> claimed_discount, unverified, with the reason.
    None when the search is off or failed and no MSRP is known (the caller says "unverified" as before)."""
    from backend.data import msrp
    from backend.data.trusted_retailers import display_name
    from backend.tools import rdap, shopping

    p = page.product
    cond = shopping.page_condition(page)  # open box, refurbished or used: a launch price and new prices aren't its "was"
    launch = msrp.lookup(p.brand, p.name) if cond == "new" else None
    query = shopping.query_for(p)
    # The same offers the Price Agent compares: the USL's safe_price first (Zetaris), else the search; then the sellers list
    from backend.data import catalog, repo

    offers, _ = repo.usl_offers(catalog.key(query)) if query else (None, None)
    source = "zetaris usl" if offers else ""
    if offers is None:
        offers, source = shopping.search(query) if query else (None, "off")
    stores: dict[str, float] = {}
    if offers is not None:
        mine = rdap.registrable(page.host or "")
        kept, _, _ = shopping.classify_offers(p, offers, price, mine, cond)
        if len({o["domain"] for o in kept}) < 3:  # the Price Agent's own rule: the same sellers lookup, shared via the cache
            extra, _ = shopping.sellers_for(query, p, None if source == "zetaris usl" else offers)
            if extra:
                kept, _, _ = shopping.classify_offers(p, offers + extra, price, mine, cond)
        for o in kept:  # the lowest new, full-price offer per credible store
            name = display_name(o["domain"])
            stores[name] = min(stores.get(name, o["total"]), o["total"])
    if launch is None and not stores:
        return None
    sel_ev = [{"kind": "selector", "ref": r, "label": "Discount claim" if i == 0 else ""} for i, r in enumerate(sel)]
    ev = sel_ev + ([{"kind": "log", "ref": f"google_shopping:{query}", "label": f"{len(stores)} credible stores ({source})"}]
                   if stores else [])
    same = sorted(n for n, t in stores.items() if _same(t, price))
    lower = sorted(((t, n) for n, t in stores.items() if t < price and not _same(t, price)))
    higher = sorted(((t, n) for n, t in stores.items() if t > price and not _same(t, price)))
    now = ""
    if stores:  # every credible store in the comparison: lower, the same, higher
        parts = ([f"{n} {money(t)} lower" for t, n in lower] + [f"{n} {money(price)} same" for n in same]
                 + [f"{n} {money(t)} higher" for t, n in higher])
        when = repo.fetched_text(repo.USL_FETCHED.get(catalog.key(query)) or []) if source == "zetaris usl" else ""
        now = f" Credible stores: {', '.join(parts)} (prices from {when})." if when else f" Credible stores now: {', '.join(parts)}."
    if launch and _same(was, launch[0]):
        verdict = ("others charge the same or less" if (same or lower) and not higher else
                   "credible stores charge from " + f"{money(min(stores.values()))} to {money(max(stores.values()))}"
                   if stores else "no credible store had it new to compare")
        sel_ev = [{"kind": "selector", "ref": r, "label": "Discount: real vs launch price" if i == 0 else ""}
                  for i, r in enumerate(sel)]  # the panel outlines a confirmed claimed_discount grey, never red
        ev = sel_ev + ev[len(sel):]
        out = finding(check_id, "claimed_discount", "confirmed",
                      f"The {money(was)} list price matches {p.brand or 'the maker'}'s launch price (MSRP), so the discount is "
                      f"real against MSRP; {verdict}.{now}",
                      ev + [{"kind": "text", "ref": f"msrp: {money(launch[0])} ({launch[1]})", "label": "MSRP"}], confidence=0.8)
        return out
    prices = list(stores.values())
    if cond != "new":
        return finding(check_id, "claimed_discount", "unverified",
                       f"The page claims a reduced price ({claim_text}). {history} Unverified: this listing is {cond}, so "
                       "its \"was\" price can't be checked against the launch price (MSRP) or new prices at other stores."
                       + (f"{now.replace('Credible stores', 'New at credible stores', 1)}" if now else ""), ev, confidence=0.4)
    if len(prices) >= 2 and max(prices) <= INFLATED * was:
        return finding(check_id, "inflated_discount", "proposed",
                       f"Discount claim looks inflated: every credible store charges {money(min(prices))} to "
                       f"{money(max(prices))}, well below the {money(was)} list price.{now} {history} Not called fake: "
                       "that needs price history.", ev, confidence=0.6)
    why = ("the list price isn't a launch price we know, and credible stores don't all sell well below it" if prices
           else "no credible store had this item new to compare")
    return finding(check_id, "claimed_discount", "unverified",
                   f"The page claims a reduced price ({claim_text}). {history} Unverified: {why}.{now}", ev, confidence=0.4)
