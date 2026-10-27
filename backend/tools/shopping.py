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
    brand, model = (product.brand or "").strip(), (product.model or "").strip()
    if model:
        return (f"{brand} {model}" if brand and brand.lower() not in model.lower() else model)[:100]
    return clean_title(product.name or "", brand)[:100]


def pack_count(text: str) -> Optional[int]:
    m = COUNT_RE.search(text or "")
    return int(m.group(1) or m.group(2)) if m else None


def same_item(product, offer: dict) -> tuple[bool, str]:
    """(same item?, why). product: contracts.PageProduct; offer: one parsed offer."""
    title = offer.get("title") or ""
    if USED_RE.search(f"{title} {offer.get('condition') or ''}"):
        return False, "not new"
    brand = (product.brand or "").strip().lower()
    if brand and brand not in title.lower():
        return False, "other brand"
    model_key = catalog.key(product.model or "")
    if len(model_key) >= 4 and model_key in catalog.key(title):
        return True, "model number matches"
    page = f"{product.name or ''} {product.model or ''}"
    want = pack_count(page)
    if want is not None and pack_count(title) != want:
        return False, "different pack size"
    page_nums = set(NUM_RE.findall(page))
    extra = [n for n in NUM_RE.findall(title) if n not in page_nums]
    if extra:
        return False, f"other size or bundle ({', '.join(extra[:3])})"
    page_words = set(_words(clean_title(product.name or "", product.brand, max_words=12)))
    if not page_words:
        return False, "no title to compare"
    overlap = len(page_words & set(_words(title))) / len(page_words)
    return (overlap >= 0.6, f"{overlap:.0%} of the title matches")


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


def parse(results: list[dict]) -> list[dict]:
    """SerpApi shopping_results -> offers {title, source, domain, price, shipping, total, shipping_note, condition, link}."""
    offers = []
    for r in results or []:
        price = r.get("extracted_price")
        if not isinstance(price, (int, float)) or price <= 0:
            continue
        shipping, note = shipping_of(r.get("delivery"))
        offers.append({"title": (r.get("title") or "")[:200], "source": (r.get("source") or "")[:80],
                       "domain": store_domain(r.get("source") or ""), "price": round(float(price), 2),
                       "shipping": shipping, "total": round(float(price) + (shipping or 0.0), 2), "shipping_note": note,
                       "condition": r.get("second_hand_condition") or "new", "link": r.get("product_link") or r.get("link")})
    return offers


# ----------------------------------------------------------------------------- search + 6 h cache


def _search_serpapi(query: str) -> Optional[list[dict]]:
    """One live search. None on any failure. The key is sent to SerpApi only and never logged."""
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


def search(query: str) -> tuple[Optional[list[dict]], str]:
    """(offers or None, source): "cache" when a search for this query is under 6 h old, "serpapi" when it was just run,
    "off" without a key, "failed" when the search didn't answer."""
    from backend.data import repo

    qk = catalog.key(query)
    if not qk or not configured():  # no key: online search is off entirely (no cache read either)
        return None, "off"
    cached = repo.cached_offers(qk, time.time() - CACHE_S)
    if cached is not None:
        return cached, "cache"
    offers = _search_serpapi(query)
    if offers is None:
        return None, "failed"
    repo.save_offers(qk, offers, time.time())
    return offers, "serpapi"
