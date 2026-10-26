"""Store Lab pages A-I: each page's JSON-LD Product matches the seeded listing (what the extension reads is what Zetaris has),
every page says it is made up, and none imitates a real retailer."""
import json
import re
from pathlib import Path

import pytest

from backend.data import catalog

STORELAB = Path(__file__).resolve().parents[2] / "storelab"
LD_RE = re.compile(r'<script type="application/ld\+json">\s*(.*?)\s*</script>', re.S)
LISTINGS = {l["store_id"]: l for l in catalog.rows()["listings"] if l["product_id"] == catalog.XM5}


def _product(store_id: str) -> dict:
    html = (STORELAB / store_id / "index.html").read_text()
    return json.loads(LD_RE.search(html).group(1))


@pytest.mark.parametrize("store_id", [f"store-{c}" for c in "abcdefghi"] + ["audiodealz"])
def test_json_ld_matches_the_seed(store_id):
    p, l = _product(store_id), LISTINGS[store_id]
    assert p["@type"] == "Product" and p["model"] == "WH-1000XM5"
    assert p["sku"] == l["sku"]
    assert float(p["offers"]["price"]) == l["price"]
    if store_id != "audiodealz":
        assert p["itemCondition"].endswith(("RefurbishedCondition" if l["condition"] == "refurbished" else "NewCondition"))


@pytest.mark.parametrize("store_id", [f"store-{c}" for c in "abdefghi"])
def test_pages_say_made_up_and_use_their_own_brand(store_id):
    html = (STORELAB / store_id / "index.html").read_text()
    assert "MADE-UP" in html and "made-up store" in html
    brand = next(s for s in catalog.rows()["stores"] if s["store_id"] == store_id)["brand"]
    assert brand in html
    for real in ("Amazon", "Best Buy", "Walmart", "Target", "eBay", "Costco", "Newegg"):
        assert real not in html


def test_trick_pages_show_their_trick():
    f = (STORELAB / "store-f" / "index.html").read_text()
    g = (STORELAB / "store-g" / "index.html").read_text()
    h = (STORELAB / "store-h" / "index.html").read_text()
    assert "Was $399.00" in f
    assert "Service fee" in g and "Handling fee" in g
    assert 'id="countdown"' in h and "Only 2 left" in h
    assert "down for maintenance" in (STORELAB / "store-e" / "index.html").read_text()
