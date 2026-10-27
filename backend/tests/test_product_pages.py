"""Product detection by the real content.js on saved pages shaped like big shops' product pages (made-up content, local
files): each is detected with a reason, its price/brand/model/SKU are read, and a search grid and an article are not."""
import asyncio

import pytest

from backend.tests.content_reader import detect_product, serve_repo
from backend.tools.browser import cached_chromium

pytestmark = pytest.mark.skipif(not cached_chromium(), reason="needs the hidden browser: run scripts/setup.sh")
BASE = "/backend/tests/fixtures/real_pages/"
PRODUCTS = {  # page: (price, brand, model, sku)
    "amazon-like/dp/B0TEST1234/": (219.99, "Acme", "AC-500X", "B0TEST1234"),
    "bestbuy-like/site/acme-ac-500x-headphones/6501234.p/": (229.99, "Acme", "AC-500X", "6501234"),
    "walmart-like/ip/acme-ac-500x-headphones/123456789/": (214.0, "Acme", "AC-500X", "123456789"),
    "target-like/p/acme-ac-500x-headphones/-/A-87654321/": (224.99, "Acme", "AC-500X", "87654321"),
    "ebay-like/itm/acme-ac-500x-headphones/123456789012/": (199.0, "Acme", "AC-500X", "123456789012"),
    "shopify-like/products/acme-ac-500x-headphones/": (209.0, "Acme", "AC-500X", None),
}
NOT_PRODUCTS = {"search-like/search/?q=headphones": "listing", "article-like/blog/how-to-choose-headphones/": "no buy button"}


@pytest.fixture(scope="module")
def site():
    server, url = serve_repo()
    yield url
    server.shutdown()


@pytest.mark.parametrize("rel", PRODUCTS)
def test_store_like_product_pages_are_detected_and_read(site, rel):
    found, facts, logs = asyncio.run(detect_product(site + BASE + rel))
    assert found["product"] is True and found["reason"]
    p = facts["product"]
    assert (p["price"], p["brand"], p["model"], p["sku"]) == PRODUCTS[rel]
    assert f"[Pricise] product page: yes ({found['reason']})" in logs


@pytest.mark.parametrize("rel", NOT_PRODUCTS)
def test_search_and_article_pages_are_not_product_pages(site, rel):
    found, _facts, logs = asyncio.run(detect_product(site + BASE + rel))
    assert found["product"] is False and NOT_PRODUCTS[rel] in found["reason"]
    assert any(line.startswith("[Pricise] product page: no (") for line in logs)


AMAZON_BUYBOX = "amazon-like-buybox/dp/B0TESTAAA1/"


def test_amazon_buy_box_layout(site):
    """Title from #productTitle (not the cart flyout's "Subtotal"), the buy-box price with cents, "Typical price" as the
    was-price, card/Subscribe & Save/points promotions are not discount claims, and the cart flyout is never read."""
    found, facts, _logs = asyncio.run(detect_product(site + BASE + AMAZON_BUYBOX))
    p = facts["product"]
    assert found["product"] is True and "price element" in found["reason"]
    assert (p["name"], p["price"], p["was_price"], p["sku"]) == ("Acme AAA Batteries, 32 Pack", 15.99, 19.99, "B0TESTAAA1")
    claims = [s for s in facts["signals"] if s["type"] == "claimed_discount"]
    assert claims and all("19.99" in s["text"] or "20%" in s["text"] for s in claims)
    text = " ".join(s["text"] for s in facts["signals"])
    for promo in ("Visa", "Subscribe", "points", "$0.00", "gift card"):
        assert promo not in text
    for cart in ("Subtotal", "42.17", "59.99", "Service fee"):  # the cart flyout
        assert cart not in text
    assert not [s for s in facts["signals"] if s["type"] == "drip_fees"]
    [info] = [s for s in facts["signals"] if s["type"] == "store_info"]
    assert info["detail"] == {"returns": True, "contact": True}
