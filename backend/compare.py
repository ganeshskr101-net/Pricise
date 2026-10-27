"""Compare-on-other-stores search links, built only from the page's own product fields (no scraping: the shopper clicks).

Same rules as extension/shared/check.js compareQuery/compareLinks, so the quick card, the panel and the chat search for the
same thing: brand + model when there is a model, else the product name without a " | Store" suffix.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote

from backend.contracts import PageProduct

SITES = [("Google Shopping", "https://www.google.com/search?tbm=shop&q="),
         ("Best Buy", "https://www.bestbuy.com/site/searchpage.jsp?st="),
         ("Walmart", "https://www.walmart.com/search?q="),
         ("eBay", "https://www.ebay.com/sch/i.html?_nkw=")]


def _clean(s: Optional[str]) -> str:
    return " ".join((s or "").split())


def compare_query(product: Optional[PageProduct]) -> str:
    p = product or PageProduct()
    brand, model = _clean(p.brand), _clean(p.model)
    if model:
        return (f"{brand} {model}" if brand and brand.lower() not in model.lower() else model)[:100]
    return re.split(r"\s[|–—-]\s", _clean(p.name))[0][:100]


def compare_links(product: Optional[PageProduct]) -> list[dict]:
    """[{"label", "url"}] (Google Shopping first), or [] when the page gave nothing to search for."""
    q = compare_query(product)
    # encodeURIComponent's safe set, so the extension and the backend build identical URLs
    return [{"label": label, "url": base + quote(q, safe="-_.!~*'()")} for label, base in SITES] if q else []
