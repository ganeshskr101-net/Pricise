"""Manufacturers' launch prices (MSRP, US) for products Pricise checks: reference data with its source, never a search result.

A page's "List Price" that equals the launch price is a real list price; the discount against it is real against MSRP
(backend/tools/discount.py then says what credible stores charge now). Add an entry only with a source a reviewer can open.
"""
from __future__ import annotations

from typing import Optional

from backend.data import catalog

# model name key (catalog.key of brand + model name) -> (MSRP in USD, source)
MSRP = {
    "jblflip6": (129.95, "JBL's US launch price for the Flip 6 (2021); samsung.com still lists $129.95 in the Google Shopping "
                         "sellers list of 27 Oct 2026"),
}


def lookup(brand: Optional[str], name: Optional[str]) -> Optional[tuple[float, str]]:
    """(msrp, source) when the page's title starts with a model we have a launch price for ("JBL Flip 6 - ..."), else None."""
    from backend.tools.shopping import clean_title

    key = catalog.key(clean_title(name or "", brand, max_words=4))
    return next((v for k, v in MSRP.items() if key.startswith(k) and not key[len(k):len(k) + 1].isdigit()), None)
