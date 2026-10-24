"""Writes the made-up store data the Trust Agent reads, so it is reproducible. Run from the repo root:

    python bench/trust/make_fixtures.py

- storelab/audiodealz, storelab/store-c: adds about.json, returns.html, listings.json, reviews.json, img/ (never touches index.html)
- storelab/store-d: the scam store the Price Agent proposes and Trust blocks ($189); index.html only if it doesn't exist yet
- storelab/_official/: the manufacturer photo every honest store may reuse (never counts as copied)
- bench/trust/t-01 .. t-12: 7 scam stores + 5 legitimate ones, and their rows in bench/labels.csv (trick "risky_store",
  expected 1 = the Trust Agent should say caution or unsafe, 0 = safe)
All names, dates, addresses and reviews are invented. Dates are relative to AS_OF and pinned in about.json ("as_of").
"""
from __future__ import annotations

import csv
import json
import random
import sys
import zlib
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
AS_OF = date(2026, 10, 24)
OFFICIAL_SEED = 999


def photo(seed: int, path: Path) -> None:
    """A distinct synthetic product photo per seed (shapes on a background)."""
    rnd = random.Random(seed)
    img = Image.new("RGB", (240, 240), tuple(rnd.randint(150, 255) for _ in range(3)))
    d = ImageDraw.Draw(img)
    for _ in range(6):
        x0, y0 = rnd.randint(0, 180), rnd.randint(0, 180)
        box = (x0, y0, x0 + rnd.randint(30, 110), y0 + rnd.randint(30, 110))
        (d.ellipse if rnd.random() < 0.5 else d.rectangle)(box, fill=tuple(rnd.randint(0, 140) for _ in range(3)))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, optimize=True)


GOOD = ["Great sound and comfy on long flights", "Battery lasts all week for me", "Noise cancelling is excellent on the train",
        "A bit pricey but worth it", "Fits well, the case is sturdy", "Calls are clear, the app is fine",
        "Returned a pair once, easy process", "Better than my old pair", "Bass is strong without being muddy",
        "Arrived quickly and well packed"]
SPAM = "Best headphones ever!!! Five stars, highly recommend this seller"


def reviews(kind: str, seed: int) -> list[dict]:
    """normal: spread over a year · burst: 70% in one week · dupes: every 3rd is the same text · both · few: 6 reviews."""
    rnd = random.Random(seed)
    n = 6 if kind == "few" else 30
    out = []
    for i in range(n):
        burst = kind in {"burst", "both"} and i < n * 0.7
        day = AS_OF - timedelta(days=rnd.randint(0, 5) if burst else rnd.randint(20, 360))
        text = SPAM if kind in {"dupes", "both"} and i % 3 == 0 else f"{rnd.choice(GOOD)} (order {1000 + i})"
        out.append({"product_id": "sony-wh1000xm5", "date": day.isoformat(),
                    "rating": 5 if kind != "normal" else rnd.randint(3, 5), "text": text})
    return out


RETURNS = {
    "30": "Return any unused item within 30 days of delivery for a full refund. We pay return shipping.",
    "14": "You may return items within 14 days of delivery for a refund.",
    "5": "Returns are accepted within 5 days of delivery, unopened items only.",
    "3": "Items can be returned within 3 days of delivery.",
    "none": "All sales are final. No returns or exchanges.",
    "missing": None,
}


@dataclass
class Store:
    folder: str            # path from the repo root
    name: str
    age_days: int = 900
    address: str | None = "120 Market Street, Fairfax, VA 22030"
    company: str | None = "VA-0412233"
    copy_from: str | None = None   # folder of the store whose own photo this one reuses
    reviews: str = "normal"
    returns: str = "30"
    price: float = 229.0
    expected: int | None = None    # bench label; None = Store Lab demo store
    note: str = ""
    write_index: bool = True


DEMO = [
    Store("storelab/audiodealz", "AudioDealz", age_days=6, address=None, company=None, copy_from="storelab/store-c",
          reviews="burst", returns="5", price=149, write_index=False, note="hero demo store"),
    Store("storelab/store-c", "Store C", age_days=3200, address="88 Harbor Road, Arlington, VA 22201", company="VA-0098812",
          price=255, write_index=False, note="honest store, the winner"),
    Store("storelab/store-d", "Store D", age_days=6, address="", company=None, copy_from="storelab/store-c", returns="none",
          price=189, note="scam store the Price Agent proposes; Trust blocks it"),
]

BENCH = [
    Store("bench/trust/t-01", "Bolt Audio Outlet", age_days=4, address=None, company=None, copy_from="bench/trust/t-08",
          expected=1, note="new domain + no identity + copied photo"),
    Store("bench/trust/t-02", "MegaSave Sound", age_days=11, reviews="both", expected=1,
          note="new domain + review burst + duplicate reviews"),
    Store("bench/trust/t-03", "Prime Gadget Hub", address="n/a", company="", returns="none", expected=1,
          note="no address or company + no returns"),
    Store("bench/trust/t-04", "SoundKing Deals", age_days=19, copy_from="bench/trust/t-09", expected=1,
          note="new domain + copied photo"),
    Store("bench/trust/t-05", "QuickBuy Electro", age_days=2, returns="3", expected=1, note="new domain + 3-day returns"),
    Store("bench/trust/t-06", "Clearance Kings", reviews="both", returns="missing", expected=1,
          note="review burst + duplicates + no returns page (three weak signs)"),
    Store("bench/trust/t-07", "HeadphoneHaus Direct", age_days=300, company=None, copy_from="bench/trust/t-10", expected=1,
          note="copied photo + no company number"),
    Store("bench/trust/t-08", "Northwind Audio", age_days=4000, expected=0, note="long-standing store (victim of t-01)"),
    Store("bench/trust/t-09", "Harbor Electronics", age_days=45, expected=0,
          note="fairly new domain (45 days) but nothing else wrong (victim of t-04)"),
    Store("bench/trust/t-10", "Maple Sound Co", returns="14", reviews="few", expected=0,
          note="14-day returns, only 6 reviews (victim of t-07)"),
    Store("bench/trust/t-11", "Cobalt Tech Supply", returns="5", expected=0, note="one weak sign (5-day returns) is not enough"),
    Store("bench/trust/t-12", "Riverside Hi-Fi", age_days=1500, expected=0,
          note="reuses the manufacturer photo like everyone (must not count as copied)"),
]


def seed(folder: str) -> int:
    return zlib.crc32(folder.encode())


def write_store(s: Store) -> None:
    d = ROOT / s.folder
    (d / "img").mkdir(parents=True, exist_ok=True)
    slug = Path(s.folder).name
    about = {"name": s.name, "domain": f"{slug}.example", "domain_created": (AS_OF - timedelta(days=s.age_days)).isoformat(),
             "as_of": AS_OF.isoformat(), "address": s.address, "company_number": s.company,
             "_note": "Made-up store for Pricise (Store Lab). as_of = date of the made-up WHOIS snapshot."}
    (d / "about.json").write_text(json.dumps(about, indent=2) + "\n")
    policy = RETURNS[s.returns]
    returns_file = d / "returns.html"
    if policy is None:
        returns_file.unlink(missing_ok=True)
    else:
        returns_file.write_text(f"<!doctype html>\n<meta charset=\"utf-8\"><title>Returns · {s.name}</title>\n"
                                f"<h1>Returns policy</h1>\n<p>{policy}</p>\n")
    photo(OFFICIAL_SEED, d / "img" / "official.png")
    photo(seed(s.copy_from or s.folder), d / "img" / "product.png")
    listings = [{"listing_id": f"{slug}-1", "product_id": "sony-wh1000xm5", "image_url": "img/official.png", "price": s.price},
                {"listing_id": f"{slug}-2", "product_id": "sony-wh1000xm5", "image_url": "img/product.png", "price": s.price}]
    (d / "listings.json").write_text(json.dumps(listings, indent=2) + "\n")
    (d / "reviews.json").write_text(json.dumps(reviews(s.reviews, seed(s.folder)), indent=1) + "\n")
    index = d / "index.html"
    if s.write_index and (s.expected is not None or not index.exists()):
        index.write_text(
            f"<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><title>{s.name} · Sony WH-1000XM5</title></head>\n"
            f"<body style=\"font-family:system-ui;max-width:640px;margin:40px auto\">\n"
            f"<p><small>Made-up store for Pricise testing.</small></p>\n<h1 id=\"product-title\">Sony WH-1000XM5 Wireless Headphones</h1>\n"
            f"<img src=\"img/product.png\" width=\"240\" height=\"240\" alt=\"Product photo\">\n"
            f"<p class=\"price\">${s.price:,.2f}</p>\n<p><a href=\"returns.html\">Returns</a> · <a href=\"about.json\">About</a></p>\n"
            f"<button>Add to cart</button>\n</body></html>\n")


def write_labels() -> None:
    path = ROOT / "bench" / "labels.csv"
    raw = path.read_bytes().decode()
    newline = "\r\n" if "\r\n" in raw else "\n"
    text = raw.replace("\r\n", "\n")
    rows = [r for r in csv.reader(text.splitlines()) if r and not r[0].startswith("trust/")]
    rows += [[s.folder.removeprefix("bench/") + "/index.html", "risky_store", str(s.expected), s.note] for s in BENCH]
    with path.open("w", newline="") as fh:
        csv.writer(fh, lineterminator=newline).writerows(rows)


def main() -> None:
    photo(OFFICIAL_SEED, ROOT / "storelab" / "_official" / "wh1000xm5.png")
    for s in DEMO + BENCH:
        write_store(s)
    write_labels()
    print(f"wrote {len(DEMO)} Store Lab stores, {len(BENCH)} bench stores, and the trust rows in bench/labels.csv")


if __name__ == "__main__":
    sys.exit(main())
