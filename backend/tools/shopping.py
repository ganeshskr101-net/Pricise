"""Online offers for a REAL product page: one Google Shopping search through SerpApi, read-only, never the stores' sites.

search(query) -> offers: SERPAPI_KEY from .env (never logged); 8 s timeout; any failure or a missing key gives None and the
Price Agent says it couldn't search. Results are cached per query for CACHE_S (6 h): the Pricise Postgres table
shopping_offers (read through Zetaris, or straight from Postgres if Zetaris can't read it yet) with DATA_SOURCE=zetaris,
memory otherwise.

same_item(page, offer): explicit rules, precision over recall (a wrong "same item" would be a wrong price):
  - new condition only (refurbished, renewed, used, pre-owned, open box are out);
  - the brand is in the offer's title (when the page has a brand);
  - the page's model number in the title is a match on its own;
  - otherwise: the same pack count when the page has one, no number the page doesn't have (other sizes, combos, bundles),
    and at least 60% of the page title's words.
store_domain(source): offers name a store ("Walmart", "eBay - seller", "mcgrocer.com"), not a website. Only names that are a
domain already, or are in KNOWN_STORES, get one; other offers are skipped (the Trust Agent can't check a store without one).
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Optional

import httpx

from backend.data import catalog
from backend.env import load_dotenv

log = logging.getLogger("pricise.price")
SERPAPI_URL = "https://serpapi.com/search.json"
TIMEOUT_S = 8
CACHE_S = 6 * 3600
KNOWN_STORES = {"walmart": "walmart.com", "target": "target.com", "best buy": "bestbuy.com", "amazon": "amazon.com",
                "amazon.com": "amazon.com", "ebay": "ebay.com", "home depot": "homedepot.com", "the home depot": "homedepot.com",
                "lowe's": "lowes.com", "lowes": "lowes.com", "costco": "costco.com", "sam's club": "samsclub.com",
                "kohl's": "kohls.com", "macy's": "macys.com", "staples": "staples.com", "office depot": "officedepot.com",
                "newegg": "newegg.com", "b&h photo": "bhphotovideo.com", "b&h photo-video-audio": "bhphotovideo.com",
                "rei": "rei.com", "dick's sporting goods": "dickssportinggoods.com", "academy sports + outdoors": "academy.com",
                "cvs pharmacy": "cvs.com", "walgreens": "walgreens.com", "kroger": "kroger.com", "wayfair": "wayfair.com",
                "advance auto parts": "advanceautoparts.com", "autozone": "autozone.com", "chewy": "chewy.com",
                "dell": "dell.com", "apple": "apple.com", "adorama": "adorama.com", "crutchfield": "crutchfield.com"}
USED_RE = re.compile(r"\b(refurbished|renewed|reconditioned|used|pre-?owned|open[- ]box|second[- ]hand|for parts)\b", re.I)
COUNT_RE = re.compile(r"(?:pack of|set of|count of)\s*(\d+)|(\d+)\s*-?\s*(?:pack|pk|count|ct|pcs|pieces|piece|pc)\b", re.I)
NUM_RE = re.compile(r"\d+(?:\.\d+)?")
STOP = {"the", "and", "for", "with", "of", "a", "an", "in", "to", "by", "new", "pack", "count", "ct", "pk", "set", "x", "&",
        "-", "|", "men", "women", "unisex"}


def configured() -> bool:
    load_dotenv()
    key = os.environ.get("SERPAPI_KEY", "")
    return bool(key) and "replace" not in key.lower()


# ----------------------------------------------------------------------------- query and matching


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9][a-z0-9.+-]*", (text or "").lower()) if w not in STOP]


def clean_title(name: str, brand: Optional[str] = None, max_words: int = 8) -> str:
    """"SWIFTVEST Running Vest ... | Lightweight ..." -> the first part, brand first, at most max_words words."""
    first = re.split(r"\s[|–—]\s|,\s|\s-\s|\(", name or "")[0]
    words = first.split()
    if brand and brand.lower() not in first.lower():
        words = [brand, *words]
    return " ".join(words[:max_words])


def query_for(product) -> str:
    """Brand + model when the page's title names that model; otherwise the title's first part (brand first) plus the colour
    the title states ("JBL Flip 6 Blue"): a store's item code such as JBLFLIP6BLUAM finds little on Google Shopping."""
    brand, model = (product.brand or "").strip(), (product.model or "").strip()
    named = catalog.key(model) and catalog.key(model) in catalog.key(product.name or "")
    if model and (named or not product.name):
        return (f"{brand} {model}" if brand and brand.lower() not in model.lower() else model)[:100]
    q = clean_title(product.name or "", brand)
    colour = [c for c in _variant(product.name or "")["color"] if c not in q.lower()]
    return (f"{q} {sorted(colour)[0].capitalize()}" if len(colour) == 1 else q)[:100]


def pack_count(text: str) -> Optional[int]:
    m = COUNT_RE.search(text or "")
    return int(m.group(1) or m.group(2)) if m else None


EDITIONS = {"signature", "kids", "essentials", "colorsoft", "scribe", "oasis", "pro", "max", "plus", "mini", "lite", "ultra",
            "se", "xl", "slim", "basic", "premium", "deluxe", "jr", "junior", "international"}
COLORS = {"black", "white", "silver", "gray", "grey", "blue", "red", "green", "pink", "gold", "jade", "purple", "yellow",
          "orange", "brown", "beige", "navy", "teal", "graphite", "midnight", "starlight", "agave", "raspberry", "camo"}
COMBO_RE = re.compile(r"\b(combo|bundle|variety|assorted|kit|set of)\b", re.I)
STORAGE_RE = re.compile(r"(\d+)\s?(gb|tb)\b", re.I)
GEN_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\s*gen", re.I)
YEAR_RE = re.compile(r"\b(20[12]\d)\b")


def _variant(text: str) -> dict:
    words = set(_words(text))
    return {"storage": {f"{n}{u.lower()}" for n, u in STORAGE_RE.findall(text or "")},
            "edition": words & EDITIONS, "color": words & COLORS,
            "gen": set(GEN_RE.findall(text or "")), "year": set(YEAR_RE.findall(text or ""))}


def same_item(product, offer: dict, ignore_condition: bool = False) -> tuple[bool, str]:
    """(same item?, why). product: contracts.PageProduct; offer: one parsed offer. Edition, storage, colour, generation and
    year must agree whenever both titles state them ("Signature Edition 32GB" never matches the regular 16GB)."""
    from backend.data.trusted_retailers import BRAND_LINES, brand_key

    title = offer.get("title") or ""
    if ignore_condition:  # an open-box or refurbished offer: is it the same item, whatever its condition?
        title = USED_RE.sub(" ", title)
    elif USED_RE.search(f"{title} {offer.get('condition') or ''}"):
        return False, "not new"
    low = title.lower()
    brand = (product.brand or "").strip().lower()
    lines = BRAND_LINES.get(brand_key(product.brand, product.name) or "", [])
    if brand and not any(w in low for w in brand.split() if len(w) >= 3) and not any(line in low for line in lines):
        return False, "other brand"
    page = f"{product.name or ''} {product.model or ''}"
    mine, theirs = _variant(page), _variant(title)
    if mine["edition"] != theirs["edition"]:
        return False, f"other edition ({', '.join(sorted(theirs['edition'] ^ mine['edition']))})"
    for k, label in (("storage", "storage"), ("color", "colour"), ("gen", "generation"), ("year", "year")):
        if mine[k] and theirs[k] and not (mine[k] & theirs[k]):
            return False, f"other {label} ({', '.join(sorted(theirs[k]))})"
    if COMBO_RE.search(title) and not COMBO_RE.search(page):
        return False, "bundle or combo"
    model_key = catalog.key(product.model or "")
    if len(model_key) >= 4 and model_key in catalog.key(title):
        return True, "model number matches"
    words = [w for w in _words(clean_title(product.name or "", None, max_words=4)) if w not in _words(product.brand or "")]
    short = catalog.key(" ".join(words))  # "JBL Flip 6" -> "flip6": the model name the title starts with, spaces ignored
    if len(short) >= 4 and any(c.isdigit() for c in short):  # a model name with a number: it must be in the title
        if re.search(re.escape(short) + r"(?!\d)", catalog.key(title)):
            return True, "model name matches"
        return False, "other model"
    want = pack_count(page)
    if want is not None and pack_count(title) != want:
        return False, "different pack size"
    if re.search(r"\d+\s*/\s*\d+", title) and not re.search(r"\d+\s*/\s*\d+", page):
        return False, "several sizes in one listing"
    page_words = set(_words(clean_title(product.name or "", product.brand, max_words=12)))
    if not page_words:
        return False, "no title to compare"
    overlap = len(page_words & set(_words(title))) / len(page_words)
    return (overlap >= 0.6, f"{overlap:.0%} of the title matches")


def other_model(product, title: str) -> Optional[tuple[str, str]]:
    """("Flip 7", "Flip 6") when the offer is another model of the same line as the page's ("JBL Flip 6"), else None."""
    words = [w for w in _words(clean_title(product.name or "", None, max_words=4)) if w not in _words(product.brand or "")]
    fam = next((w for w in words if not any(c.isdigit() for c in w)), None)
    num = next((w for w in words if w.isdigit()), None)
    if not fam or not num:
        return None
    m = re.search(rf"\b{re.escape(fam)}\s*(\d+)\b", title, re.I)
    return (f"{fam.capitalize()} {m[1]}", f"{fam.capitalize()} {num}") if m and m[1] != num else None


def store_domain(source: str) -> Optional[str]:
    s = (source or "").strip().lower()
    base = s.split(" - ")[0].strip()  # "eBay - ielectrica" -> "ebay" (the marketplace is the website)
    if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", base):
        return base.removeprefix("www.")
    return KNOWN_STORES.get(base)


def shipping_of(delivery: Optional[str]) -> tuple[Optional[float], str]:
    """(shipping cost or None if unknown, note): "Free delivery" -> 0; "$5.99 delivery" -> 5.99."""
    d = (delivery or "").lower()
    if not d:
        return None, "shipping not shown"
    if "free" in d:
        return 0.0, "free shipping"
    m = re.search(r"\$\s?(\d+(?:\.\d{1,2})?)", d)
    return (float(m.group(1)), f"${m.group(1)} shipping") if m else (None, "shipping not shown")


DENY_RE = re.compile(r"/\s?mo\b|per month|\bmo\.|monthly|installment|payments?\b|affirm|klarna|with trade-?in|"
                     r"with activation|renewed|refurbished|\bused\b|open[- ]box|like[- ]new|pre-?owned", re.I)


def excluded(r: dict) -> Optional[str]:
    """Why a raw SerpApi offer can't be a real one-time price for a new item (None if it can): words in its price text,
    extensions, tag or title."""
    for field, text in (("price", r.get("price")), ("extensions", " ".join(r.get("extensions") or [])), ("tag", r.get("tag")),
                        ("title", r.get("title")), ("condition", r.get("second_hand_condition"))):
        m = DENY_RE.search(str(text or ""))
        if m:
            return f"\"{m.group(0).strip()}\" in its {field}"
    return None


def parse(results: list[dict]) -> list[dict]:
    """SerpApi shopping_results -> offers {title, source, domain, price, shipping, total, shipping_note, condition, link}.
    condition is "new", or "excluded: <why>" (a per-month, trade-in or not-new price): kept so the cache keeps the reason."""
    offers = []
    for r in results or []:
        price = r.get("extracted_price")
        if not isinstance(price, (int, float)) or price <= 0:
            continue
        why = excluded(r)
        shipping, note = shipping_of(r.get("delivery"))
        offers.append({"title": (r.get("title") or "")[:200], "source": (r.get("source") or "")[:80],
                       "domain": store_domain(r.get("source") or ""), "price": round(float(price), 2),
                       "shipping": shipping, "total": round(float(price) + (shipping or 0.0), 2), "shipping_note": note,
                       "condition": f"excluded: {why}" if why else "new", "link": r.get("product_link") or r.get("link"),
                       "token": r.get("immersive_product_page_token"),  # for the sellers lookup; not cached
                       "multi": bool(r.get("multiple_sources"))})  # Google lists several stores for this product
    return offers


# ----------------------------------------------------------------------------- search + 6 h cache


def _search_serpapi(query: str) -> Optional[list[dict]]:
    """One live search. None on any failure. The key is sent to SerpApi only and never logged."""
    log.info("serpapi call: google_shopping q=%r", query)
    try:
        r = httpx.get(SERPAPI_URL, timeout=TIMEOUT_S, params={"engine": "google_shopping", "q": query, "gl": "us", "hl": "en",
                                                              "api_key": os.environ.get("SERPAPI_KEY", "")})
        if r.status_code != 200:
            log.warning("shopping search failed: HTTP %s", r.status_code)
            return None
        return parse(r.json().get("shopping_results") or [])
    except Exception as err:  # noqa: BLE001 - network, JSON: the page check carries on without it
        log.warning("shopping search failed: %s", type(err).__name__)
        return None


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(key: str) -> threading.Lock:
    """One lock per query: the Deal and Price agents ask for the same search at once, and only one of them pays for it."""
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def search(query: str) -> tuple[Optional[list[dict]], str]:
    """(offers or None, source): "cache" when a search for this query is under 6 h old, "serpapi" when it was just run,
    "off" without a key, "failed" when the search didn't answer."""
    qk = catalog.key(query)
    if not qk or not configured():  # no key: online search is off entirely (no cache read either)
        return None, "off"
    with _lock(qk):
        return _search(query, qk)


def _search(query: str, qk: str) -> tuple[Optional[list[dict]], str]:
    from backend.data import repo

    cached = repo.cached_offers(qk, time.time() - CACHE_S)
    if cached is not None:
        return cached, "cache"
    offers = _search_serpapi(query)
    if offers is None:
        return None, "failed"
    LIVE[qk] = offers  # with their lookup tokens
    LIVE_AT[qk] = time.time()
    repo.save_offers(qk, offers, time.time())
    return offers, "serpapi"


LIVE: dict[str, list[dict]] = {}  # query key -> the last live search's offers in this process (tokens included)
LIVE_AT: dict[str, float] = {}     # query key -> when that live search ran (a token search runs at most once per 6 h)


def live_token(query: str, product) -> Optional[str]:
    """The sellers-lookup token from this process's last live search for the query (None if there wasn't one)."""
    offers = LIVE.get(catalog.key(query))
    return best_match_token(product, offers) if offers else None


# ----------------------------------------------------------------------------- credible offers only

SANITY = 0.70  # a credible new offer under 70% of this page's price or of the credible offers' median is "unusually low"
FLOOR = 0.50   # under 50% of this page's price it is not a real price for the item (a monthly or partial price): dropped
CONDITION_WORDS = [("open box", ("open box", "open-box")), ("refurbished", ("refurbished", "renewed", "reconditioned", "restored")),
                   ("used", ("used", "pre-owned", "preowned", "like new", "like-new", "second hand")),
                   ("monthly or installment price", ("/mo", "per month", "mo.", "monthly", "installment", "payment", "affirm",
                                                     "klarna", "trade-in", "trade in", "activation"))]


def condition_kind(reason: str) -> str:
    """'"open box" in its title' -> "open box"; the kind of not-new or not-full-price offer an exclusion reason names."""
    low = (reason or "").lower()
    return next((kind for kind, words in CONDITION_WORDS if any(w in low for w in words)), "not new")


def classify_offers(product, offers: list[dict], page_price: Optional[float], page_domain: str
                    ) -> tuple[list[dict], list[dict], list[dict]]:
    """(kept, blocked, dropped). kept: credible stores (allowlisted retailers, or the brand's own or official store) with a
    new, full-price offer of the same item, sorted by true total; each has "seller" (a brand's official store on a
    retailer, else None) and "low" (under 70% of this page or of the kept offers' median: unusually low, for the Skeptic
    to review). blocked: the same item at a credible store but open box, refurbished, used or a monthly price, with
    "kind": these go to the Skeptic too, who can never approve them. dropped: everything else, each with its reason (logged)."""
    import statistics

    from backend.data.trusted_retailers import credible_domain, seller_of

    kept, blocked, dropped = [], [], []

    def drop(o, reason):
        dropped.append({"source": o.get("source"), "price": o.get("price"), "title": (o.get("title") or "")[:90], "reason": reason})
        log.info("price: dropped offer %s $%s %r: %s", o.get("source"), o.get("price"), (o.get("title") or "")[:60], reason)

    for o in offers:
        excluded_why = (o.get("condition") or "new").removeprefix("excluded: ") if (o.get("condition") or "new") != "new" else None
        domain, why = credible_domain(o.get("source") or "", product.brand, product.name)
        if not domain:
            drop(o, excluded_why or why)
            continue
        if domain == page_domain:
            drop(o, excluded_why or "this page's own store")
            continue
        same, why = same_item(product, o, ignore_condition=bool(excluded_why))
        other = other_model(product, o.get("title") or "") if not same and why == "other model" else None
        if not same and not other:
            drop(o, excluded_why or f"not the same item: {why}")
            continue
        if excluded_why or other:  # for the Skeptic, who rejects it with every reason (other model, open box, ...)
            blocked.append({**o, "domain": domain, "seller": seller_of(o.get("source") or ""),
                            "kind": condition_kind(excluded_why) if excluded_why else "new", "other_model": other,
                            "reason": "; ".join(x for x in (f"not the same item: {other[0]}, not {other[1]}" if other else "",
                                                            excluded_why or "") if x)})
            continue
        kept.append({**o, "domain": domain, "seller": seller_of(o.get("source") or ""), "low": False})
    if page_price:
        for o in [o for o in kept if o["total"] < FLOOR * page_price]:
            kept.remove(o)
            drop(o, f"under 50% of this page's ${page_price:,.2f} (likely a monthly, partial or wrong price)")
    median = statistics.median(o["total"] for o in kept) if len(kept) >= 2 else None
    for o in kept:
        o["low"] = bool((page_price and o["total"] < SANITY * page_price) or (median and o["total"] < SANITY * median))
        if o["low"]:
            o["median"] = median
            log.info("price: unusually low offer %s $%s (median %s): for the Skeptic to review", o["source"], o["total"], median)
    return sorted(kept, key=lambda o: (o["total"], o["domain"])), blocked, dropped


def credible_offers(product, offers: list[dict], page_price: Optional[float], page_domain: str
                    ) -> tuple[list[dict], list[dict]]:
    """(kept, dropped) of classify_offers, with the blocked offers counted as dropped (for callers that don't review them)."""
    kept, blocked, dropped = classify_offers(product, offers, page_price, page_domain)
    return kept, dropped + [{"source": o.get("source"), "price": o.get("price"), "title": (o.get("title") or "")[:90],
                             "reason": o["reason"]} for o in blocked]


# ----------------------------------------------------------------------------- sellers of one product ("compare prices")


def parse_sellers(stores: list[dict]) -> list[dict]:
    """SerpApi google_immersive_product stores -> offers like parse() gives, with the store's OWN product link."""
    out = []
    for s in stores or []:
        price = s.get("extracted_price")
        if not isinstance(price, (int, float)) or price <= 0:
            continue
        r = {"price": s.get("price"), "title": s.get("title"), "extensions": [*(s.get("details_and_offers") or []), s.get("tag") or ""]}
        why = excluded(r)
        shipping, note = shipping_of(f"{s.get('shipping')} delivery" if s.get("shipping") else None)
        out.append({"title": (s.get("title") or "")[:200], "source": (s.get("name") or "")[:80], "domain": None,
                    "price": round(float(price), 2), "shipping": shipping, "total": round(float(price) + (shipping or 0.0), 2),
                    "shipping_note": note, "condition": f"excluded: {why}" if why else "new", "link": s.get("link")})
    return out


def _sellers_serpapi(token: str) -> Optional[list[dict]]:
    log.info("serpapi call: google_immersive_product (sellers) token=%s…", (token or "")[:12])
    try:
        r = httpx.get(SERPAPI_URL, timeout=TIMEOUT_S, params={"engine": "google_immersive_product", "page_token": token,
                                                              "api_key": os.environ.get("SERPAPI_KEY", "")})
        if r.status_code != 200:
            log.warning("sellers lookup failed: HTTP %s", r.status_code)
            return None
        return parse_sellers((r.json().get("product_results") or {}).get("stores") or [])
    except Exception as err:  # noqa: BLE001
        log.warning("sellers lookup failed: %s", type(err).__name__)
        return None


def sellers(query: str, token: Optional[str]) -> tuple[Optional[list[dict]], str]:
    """The sellers list of the best-matching product, cached 6 h under the search's query. (offers or None, source)."""
    key = catalog.key(query) + "sellers"
    if not configured():
        return None, "off"
    with _lock(key):
        return _sellers(key, token)


def _sellers(key: str, token: Optional[str]) -> tuple[Optional[list[dict]], str]:
    from backend.data import repo

    cached = repo.cached_offers(key, time.time() - CACHE_S)
    if cached is not None:
        return cached, "cache"
    if not token:
        log.info("sellers lookup skipped: no product token (the search came from an older cache)")
        return None, "no token"
    offers = _sellers_serpapi(token)
    if offers is None:
        return None, "failed"
    repo.save_offers(key, offers, time.time())
    return offers, "serpapi"


def sellers_for(query: str, product, offers: Optional[list[dict]] = None) -> tuple[Optional[list[dict]], str]:
    """The sellers list for this query: from its 6 h cache; else looked up with the best token from `offers`, this
    process's last live search, or the tokens cached with the search. With no token anywhere: ONE live search, only to get
    one (cached with its token), then the lookup. (offers or None, source)"""
    from backend.data import repo

    if not configured():
        return None, "off"
    qk = catalog.key(query)
    token = (best_match_token(product, offers or []) or live_token(query, product)
             or best_match_token(product, repo.cached_tokens(qk, time.time() - CACHE_S)))
    extra, source = sellers(query, token)
    if extra is None and source == "no token":
        if time.time() - LIVE_AT.get(qk, 0) < CACHE_S:
            return None, "no token (Google gave none)"  # this process searched live already: there is no token to get
        with _lock(qk):
            fresh = _search_serpapi(query)
            LIVE_AT[qk] = time.time()
            if fresh:
                LIVE[qk] = fresh
                repo.save_offers(qk, fresh, time.time())
        if fresh:
            extra, source = sellers(query, best_match_token(product, fresh))
            source = f"{source} (token from a new search)"
    return extra, source


def best_match_token(product, offers: list[dict]) -> Optional[str]:
    """The lookup token of the search result most likely to be this exact item: same item and new, credible store first."""
    from backend.data.trusted_retailers import credible_domain

    cands = [o for o in offers if o.get("token") and o.get("condition") == "new" and same_item(product, o)[0]]
    # A product Google sells at several stores first (its sellers list is the comparison); one store's own listing
    # (Woot's) has a sellers list of one. Then a credible store, then the cheapest.
    colours = _variant(product.name or "")["color"]
    names_colour = lambda o: bool(colours & _variant(o.get("title") or "")["color"])  # noqa: E731
    cands.sort(key=lambda o: (not o.get("multi"), not names_colour(o),  # the page's colour named in the title, if any
                              credible_domain(o.get("source") or "", product.brand, product.name)[0] is None,
                              o.get("total") or 0))
    return cands[0]["token"] if cands else None
