"""Test helpers: serve the repo over HTTP and run the REAL extension content script (extension/content/content.js) on a page,
so Deal Agent tests use exactly the PageFacts the extension would send. Local test pages only.
"""
from __future__ import annotations

import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTENT_JS = ROOT / "extension" / "content" / "content.js"

# Just enough of the chrome.* API for content.js to answer a "pricise:read" message. Chromium defines window.chrome as
# non-configurable, so redefining it throws: the fake APIs then go onto the existing object.
FAKE_CHROME = """
(() => {
  let listener = null;
  const fake = { storage: { local: { get: async () => ({}), set: async () => {} } },
                 runtime: { onMessage: { addListener: (fn) => { listener = fn; } } } };
  try { Object.defineProperty(window, 'chrome', { value: fake, configurable: true, writable: true }); }
  catch { Object.assign(window.chrome, fake); }
  window.__priciseRead = () => new Promise((resolve) => listener({ type: 'pricise:read' }, {}, resolve));
})();
"""


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class _Server(ThreadingHTTPServer):
    # The default listen backlog (5) drops connections when the Trust Agent fetches ~10 store files at once (seen as a
    # flaky "photos are the store's own" on Store D). uvicorn's backlog is 2048, so only this test server needs it.
    request_queue_size = 128


def serve_repo() -> tuple[ThreadingHTTPServer, str]:
    """Starts a background HTTP server on 127.0.0.1 serving the repo root. Returns (server, base_url)."""
    server = _Server(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


async def read_page_facts(url: str) -> dict:
    from backend.tools.browser import chromium

    async with chromium() as browser:
        page = await browser.new_page(viewport={"width": 1400, "height": 1000})
        await page.add_init_script(FAKE_CHROME)
        await page.goto(url, wait_until="load")
        await page.add_script_tag(content=CONTENT_JS.read_text())
        return await page.evaluate("window.__priciseRead()")
