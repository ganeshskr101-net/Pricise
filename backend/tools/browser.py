"""Headless Chromium for the Store Lab tools (run-ahead, timer test). Never used on real store pages.

Launch order: PLAYWRIGHT_CHROMIUM_PATH (env) -> Playwright's own browser -> the newest Chromium already in the Playwright cache
(so a version mismatch after `pip install playwright` doesn't need a re-download).
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

CACHE = Path.home() / "Library" / "Caches" / "ms-playwright"  # macOS; Linux uses ~/.cache/ms-playwright
LINUX_CACHE = Path.home() / ".cache" / "ms-playwright"


def cached_chromium() -> Optional[str]:
    for root in (CACHE, LINUX_CACHE):
        hits = sorted(root.glob("chromium-*/chrome-*/**/MacOS/Google Chrome for Testing"), reverse=True) + \
               sorted(root.glob("chromium-*/chrome-linux*/chrome"), reverse=True)
        if hits:
            return str(hits[0])
    return None


@asynccontextmanager
async def chromium() -> AsyncIterator:
    """Yields a headless Browser and always closes it. Raises if Playwright or Chromium is missing (callers catch)."""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        path = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH")
        try:
            browser = await pw.chromium.launch(headless=True, executable_path=path or None)
        except Exception:
            fallback = cached_chromium()
            if path or not fallback:
                raise
            browser = await pw.chromium.launch(headless=True, executable_path=fallback)
        try:
            yield browser
        finally:
            await browser.close()
