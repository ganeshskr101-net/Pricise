"""Headless Chromium for the Store Lab tools (run-ahead, timer test). Never used on real store pages.

Launch order: PLAYWRIGHT_CHROMIUM_PATH (env) -> Playwright's own browser -> the newest Chromium already in the Playwright cache
(so a version mismatch after `pip install playwright` doesn't need a re-download).

The server calls warm() at startup: one browser is launched then and reused by every check (each check still gets its own
fresh browser context), so there is no ~1 s cold start per check. Without warm() (tests, the bench), chromium() launches a
browser per use and closes it afterwards, as before.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

log = logging.getLogger("pricise.browser")
CACHE = Path.home() / "Library" / "Caches" / "ms-playwright"  # macOS; Linux uses ~/.cache/ms-playwright
LINUX_CACHE = Path.home() / ".cache" / "ms-playwright"

_shared: dict = {}  # {"pw", "browser", "loop"} once warm() has run


def cached_chromium() -> Optional[str]:
    """PLAYWRIGHT_CHROMIUM_PATH if that file exists, else the newest Chromium in Playwright's cache (macOS, Linux, Windows)."""
    path = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH", "").strip()
    if path and Path(path).is_file():
        return path
    windows = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright" if os.environ.get("LOCALAPPDATA") else None
    for root in (CACHE, LINUX_CACHE, *([windows] if windows else [])):
        hits = sorted(root.glob("chromium-*/chrome-*/**/MacOS/Google Chrome for Testing"), reverse=True) + \
               sorted(root.glob("chromium-*/chrome-linux*/chrome"), reverse=True) + \
               sorted(root.glob("chromium-*/chrome-win*/chrome.exe"), reverse=True)
        if hits:
            return str(hits[0])
    return None


async def _launch(pw):
    path = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH")
    try:
        return await pw.chromium.launch(headless=True, executable_path=path or None)
    except Exception:
        fallback = cached_chromium()
        if path or not fallback:
            raise
        return await pw.chromium.launch(headless=True, executable_path=fallback)


def _shared_browser():
    b = _shared.get("browser")
    if b is not None and b.is_connected() and _shared.get("loop") is asyncio.get_running_loop():
        return b
    return None


async def warm() -> None:
    """Launch the shared browser now (server startup). Safe to call again; relaunches if it crashed."""
    if _shared_browser():
        return
    from playwright.async_api import async_playwright

    t0 = time.perf_counter()
    pw = _shared.get("pw") or await async_playwright().start()
    _shared.update(pw=pw, browser=await _launch(pw), loop=asyncio.get_running_loop())
    log.info("shared Chromium ready in %.2fs", time.perf_counter() - t0)


async def shutdown() -> None:
    b, pw = _shared.pop("browser", None), _shared.pop("pw", None)
    _shared.clear()
    if b is not None:
        await b.close()
    if pw is not None:
        await pw.stop()


@asynccontextmanager
async def chromium() -> AsyncIterator:
    """Yields a headless Browser. The shared one if the server warmed it (left open), otherwise a fresh one that is closed
    afterwards. Raises if Playwright or Chromium is missing (callers catch)."""
    if _shared.get("browser") is not None and _shared.get("loop") is asyncio.get_running_loop():
        if not _shared_browser():  # it crashed: relaunch once
            await warm()
        yield _shared["browser"]
        return
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await _launch(pw)
        try:
            yield browser
        finally:
            await browser.close()
