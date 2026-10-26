"""Copied-photo check: perceptual hash (imagehash.phash) of this store's product photos vs. other Store Lab stores' photos.

Direction matters: if Store D copies Store C's photo, only Store D (the younger domain) is flagged, never the victim.
Manufacturer photos every honest shop may reuse are excluded: storelab/_official/*.png (plus TRUST_OFFICIAL_IMAGES,
a comma list of URLs or repo paths) never count as copied.
"""
from __future__ import annotations

import asyncio
import io
import os
import time
from datetime import date
from typing import Optional

import httpx
import imagehash
from PIL import Image

from backend.tools.trust_common import LISTINGS_PATHS, ROOT, Signal, first_json, get_bytes, get_json, resolve

MATCH_DISTANCE = 4          # phash Hamming distance at or below which two images are "the same photo"
MAX_IMAGES_PER_STORE = 8
PEER_CACHE_S = 300          # other stores' photos rarely change; re-hashing them on every vet is the slow part
OFFICIAL_DIR = ROOT / "storelab" / "_official"

Hashes = list[tuple[str, imagehash.ImageHash]]
_peer_cache: dict[str, tuple[float, tuple[Optional[date], Hashes]]] = {}
_official: Optional[list[imagehash.ImageHash]] = None


def clear_cache() -> None:
    global _official
    _peer_cache.clear()
    _official = None


def phash_bytes(data: bytes) -> Optional[imagehash.ImageHash]:
    try:
        return imagehash.phash(Image.open(io.BytesIO(data)).convert("RGB"))
    except Exception:  # noqa: BLE001 - an undecodable image is just skipped
        return None


async def official_hashes(client: httpx.AsyncClient) -> list[imagehash.ImageHash]:
    """Hashes of manufacturer photos (loaded once)."""
    global _official
    if _official is not None:
        return _official
    blobs = [p.read_bytes() for p in sorted(OFFICIAL_DIR.glob("*.png"))] if OFFICIAL_DIR.is_dir() else []
    for ref in (x.strip() for x in os.getenv("TRUST_OFFICIAL_IMAGES", "").split(",") if x.strip()):
        path = ROOT / ref
        blob = await get_bytes(client, ref) if ref.startswith("http") else (path.read_bytes() if path.is_file() else None)
        if blob:
            blobs.append(blob)
    hashes = await asyncio.gather(*(asyncio.to_thread(phash_bytes, b) for b in blobs))  # CPU work: off the event loop
    _official = [h for h in hashes if h is not None]
    return _official


def _created(about: object) -> Optional[date]:
    try:
        return date.fromisoformat(str(about.get("domain_created"))[:10])  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        return None


async def _image_hashes(client: httpx.AsyncClient, base: str) -> Hashes:
    listings, _ = await first_json(client, base, LISTINGS_PATHS)
    if not isinstance(listings, list):
        return []
    urls = list(dict.fromkeys(resolve(base, x["image_url"]) for x in listings
                              if isinstance(x, dict) and x.get("image_url")))[:MAX_IMAGES_PER_STORE]
    blobs = await asyncio.gather(*(get_bytes(client, u) for u in urls))
    hashes = await asyncio.gather(*(asyncio.to_thread(phash_bytes, b) if b else asyncio.sleep(0) for b in blobs))
    return [(u, h) for u, h in zip(urls, hashes) if h is not None]  # phash is CPU work: off the event loop


async def store_photos(client: httpx.AsyncClient, base: str, use_cache: bool = True) -> tuple[Optional[date], Hashes]:
    """(domain_created, [(image_url, phash)]) for one store; peers are cached for PEER_CACHE_S."""
    hit = _peer_cache.get(base)
    if use_cache and hit and time.monotonic() - hit[0] < PEER_CACHE_S:
        return hit[1]
    about, hashes = await asyncio.gather(get_json(client, base + "/about.json"), _image_hashes(client, base))
    result = (_created(about), hashes)
    if hashes:  # don't remember a store that was down
        _peer_cache[base] = (time.monotonic(), result)
    return result


async def check_copied_photos(client: httpx.AsyncClient, base: str, peer_bases: list[str]) -> Signal:
    base = base.rstrip("/")
    peers = [p.rstrip("/") for p in peer_bases if p.rstrip("/") != base]
    official = await official_hashes(client)
    (my_created, mine), *theirs = await asyncio.gather(store_photos(client, base, use_cache=False),
                                                       *(store_photos(client, p) for p in peers))
    if not mine:
        return Signal("copied_photos", "strong", False, False, "Couldn't check the product photos (none found).",
                      [{"kind": "url", "ref": base + LISTINGS_PATHS[0]}], 0.3)
    if not peers:
        return Signal("copied_photos", "strong", False, False,
                      "Couldn't compare product photos (no other stores to compare with).", [], 0.3)

    def i_copied(peer_created: Optional[date]) -> bool:
        """The younger domain copied the older one. Our age unknown: assume we copied. Theirs unknown or same age: can't tell."""
        if my_created is None:
            return True
        return peer_created is not None and my_created > peer_created

    for my_url, my_h in mine:
        if any(my_h - o <= MATCH_DISTANCE for o in official):
            continue
        for (peer_created, peer_imgs) in theirs:
            if not i_copied(peer_created):
                continue
            for peer_url, peer_h in peer_imgs:
                dist = my_h - peer_h
                if dist <= MATCH_DISTANCE:
                    return Signal("copied_photos", "strong", True, True, "Product photos are copied from another store.",
                                  [{"kind": "url", "ref": my_url}, {"kind": "url", "ref": peer_url},
                                   {"kind": "log", "ref": f"phash_distance={dist} (match <= {MATCH_DISTANCE}); "
                                                          f"this domain {my_created}, original {peer_created}"}],
                                  0.9 if dist == 0 else 0.8)
    return Signal("copied_photos", "strong", False, True,
                  f"Product photos are the store's own (compared with {len(peers)} other stores).",
                  [{"kind": "log", "ref": f"images_checked={len(mine)} stores_compared={len(peers)}"}], 0.85)
