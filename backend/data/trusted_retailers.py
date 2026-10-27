"""The credible stores for online price comparison: a curated allowlist of well-known retailers, plus each brand's own store.

Only offers from these stores are ever shown, whatever the Trust Agent says about others; the Trust Agent still checks
these too. Marketplace sellers on them ("Walmart - SomeSeller", "eBay - x") are not the retailer and are excluded.
SerpApi names a store ("Best Buy"), not a website: STORE_NAMES maps the names it uses to the domain.
"""
from __future__ import annotations

import re
from typing import Optional

# domain -> name shown to the shopper
RETAILERS = {
    "amazon.com": "Amazon", "bestbuy.com": "Best Buy", "walmart.com": "Walmart", "target.com": "Target",
    "costco.com": "Costco", "samsclub.com": "Sam's Club", "bhphotovideo.com": "B&H Photo", "adorama.com": "Adorama",
    "newegg.com": "Newegg", "crutchfield.com": "Crutchfield", "staples.com": "Staples", "officedepot.com": "Office Depot",
    "homedepot.com": "The Home Depot", "lowes.com": "Lowe's", "kohls.com": "Kohl's", "macys.com": "Macy's",
    "dickssportinggoods.com": "Dick's Sporting Goods", "rei.com": "REI", "apple.com": "Apple",
    "woot.com": "Woot",  # owned by Amazon
}

# the names SerpApi uses for those stores (lower case)
STORE_NAMES = {
    "amazon": "amazon.com", "amazon.com": "amazon.com", "best buy": "bestbuy.com", "bestbuy.com": "bestbuy.com",
    "walmart": "walmart.com", "walmart.com": "walmart.com", "target": "target.com", "costco": "costco.com",
    "sam's club": "samsclub.com", "b&h photo": "bhphotovideo.com", "b&h photo-video-audio": "bhphotovideo.com",
    "b&h photo video": "bhphotovideo.com", "adorama": "adorama.com", "newegg": "newegg.com", "newegg.com": "newegg.com",
    "crutchfield": "crutchfield.com", "staples": "staples.com", "office depot": "officedepot.com",
    "office depot & officemax": "officedepot.com", "the home depot": "homedepot.com", "home depot": "homedepot.com",
    "lowe's": "lowes.com", "lowes": "lowes.com", "kohl's": "kohls.com", "macy's": "macys.com",
    "dick's sporting goods": "dickssportinggoods.com", "rei": "rei.com", "apple": "apple.com", "woot": "woot.com",
    "woot!": "woot.com",
}

# brand -> its own official store (allowed for that brand's products only), and product lines that name the brand
BRAND_STORES = {"amazon": "amazon.com", "apple": "apple.com", "sony": "sony.com", "samsung": "samsung.com",
                "google": "store.google.com", "microsoft": "microsoft.com", "dell": "dell.com", "hp": "hp.com",
                "lenovo": "lenovo.com", "bose": "bose.com", "logitech": "logitech.com", "nike": "nike.com", "lego": "lego.com",
                "jbl": "jbl.com"}
BRAND_LINES = {"amazon": ["kindle", "echo", "fire tv", "fire hd", "ring", "blink", "eero"],
               "apple": ["iphone", "ipad", "airpods", "macbook", "apple watch", "imac"],
               "google": ["pixel", "nest", "chromecast"], "microsoft": ["surface", "xbox"]}
BRAND_NAMES = {"sony.com": "Sony", "samsung.com": "Samsung", "store.google.com": "Google Store", "microsoft.com": "Microsoft",
               "dell.com": "Dell", "hp.com": "HP", "lenovo.com": "Lenovo", "bose.com": "Bose", "logitech.com": "Logitech",
               "nike.com": "Nike", "lego.com": "LEGO", "jbl.com": "JBL"}
BRAND_STORE_NAMES = {"sony": "sony.com", "samsung": "samsung.com", "google store": "store.google.com",
                     "microsoft": "microsoft.com", "microsoft store": "microsoft.com", "dell": "dell.com", "hp": "hp.com",
                     "hp store": "hp.com", "lenovo": "lenovo.com", "bose": "bose.com", "logitech": "logitech.com",
                     "nike": "nike.com", "lego": "lego.com", "jbl": "jbl.com"}

# what each retailer's own terms say about warranty, shown as a caveat next to its offer (we never load the store's page)
WARRANTY_NOTES = {"woot.com": "90-day Woot warranty, not {brand}'s manufacturer warranty (Woot's usual terms, check the listing)"}


def brand_key(brand: Optional[str], name: Optional[str] = None) -> Optional[str]:
    """The brand this product belongs to ("Amazon Kindle" -> "amazon"), for its official store."""
    text = f"{brand or ''} {name or ''}".lower()
    for b in BRAND_STORES:  # whole words: "ring" (Amazon's Ring) must not match "pairing"
        if (brand or "").lower().startswith(b) or any(re.search(rf"\b{re.escape(line)}\b", text) for line in BRAND_LINES.get(b, [])):
            return b
    return None


def credible_domain(source: str, brand: Optional[str], name: Optional[str] = None) -> tuple[Optional[str], str]:
    """(domain, why) for a SerpApi store name: an allowlisted retailer, or this product's brand store. None + why otherwise."""
    s = (source or "").strip().lower()
    if " - " in s:  # "Walmart - JBL Store": a marketplace seller, unless it is this brand's official store on that retailer
        retailer, seller = (x.strip() for x in s.split(" - ", 1))
        if STORE_NAMES.get(retailer) in RETAILERS and official_seller(seller, brand, name):
            return STORE_NAMES[retailer], "the brand's official store on the retailer"
        return None, "marketplace seller, not the retailer"
    domain = STORE_NAMES.get(s)
    if domain in RETAILERS:
        return domain, "allowlisted retailer"
    official = BRAND_STORE_NAMES.get(s)
    if official and BRAND_STORES.get(brand_key(brand, name) or "") == official:
        return official, "the brand's own store"
    return None, "not an allowlisted store"


def official_seller(seller: str, brand: Optional[str], name: Optional[str] = None) -> bool:
    """"JBL Store", "JBL Official Store", "JBL" for a JBL product: the brand selling on a retailer's marketplace.
    Authorized dealers and other sellers are not the brand."""
    b = brand_key(brand, name) or (brand or "").strip().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", (seller or "").lower()).split()
    return bool(b) and bool(s) and s[0] == b and all(w in {"store", "official", "shop"} for w in s[1:])


def seller_of(source: str) -> Optional[str]:
    """The marketplace seller in "Walmart - JBL Store" ("JBL Store"), None for the retailer itself."""
    return source.split(" - ", 1)[1].strip() if " - " in (source or "") else None


def warranty_note(domain: str, brand: Optional[str]) -> Optional[str]:
    note = WARRANTY_NOTES.get(domain)
    return note.format(brand=brand or "the maker") if note else None


def display_name(domain: str) -> str:
    return RETAILERS.get(domain) or BRAND_NAMES.get(domain) or domain


GOOGLE_PRODUCT_RE = re.compile(r"^https://www\.google\.[a-z.]+/(?:shopping/product/|search\?(?:[^#]*&)?ibp=oshop\b)")


def google_shopping_url(query: str) -> str:
    from urllib.parse import quote

    return f"https://www.google.com/search?tbm=shop&q={quote(query or '', safe='')}"


def offer_url(offer: dict, query: str) -> str:
    """Where "Go to <store>" and "Open" lead: the offer's own listing from the search result, else its Google Shopping
    product page, else a Google Shopping search for the exact product. Never a store's own search URL: those often 404
    or show other items, and they were built, not found."""
    from urllib.parse import urlsplit

    links = [x for x in (offer.get("link"), offer.get("product_link")) if isinstance(x, str) and x.startswith("https://")]
    own = next((x for x in links if not re.search(r"(^|\.)google\.", urlsplit(x).netloc)), None)
    return own or next((x for x in links if GOOGLE_PRODUCT_RE.match(x)), None) or google_shopping_url(query)
