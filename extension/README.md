# extension/

The Pricise Chrome extension (Manifest V3). Plain HTML, CSS and ES modules: no build step, so **Load unpacked** always works.
**Owner:** Shriganesh.

| Path | What it is |
|---|---|
| `manifest.json` | Permissions, content scripts, side panel |
| `background.js` | Service worker: runs every check (one per tab + URL), sets the toolbar badge, sends the quick card, opens the side panel |
| `content/content.js` | Reads the page (product, price, timers, add-ons, fees), decides if it's a product page, draws the red/amber outlines. Read only |
| `content/quick-card.*` | The small result card on product pages (closed Shadow DOM) |
| `sidepanel/` | The side panel: verdict, the three questions, "Ask about this page" chat |
| `evidence/`, `wishlist/` | Full-page Evidence and Wishlist screens |
| `shared/` | `tokens.css` (design tokens), `icons.js` (line icons), `api.js` (backend client), `check.js` (check logic shared by panel and card) |
| `icons/` | `logo.svg` and the toolbar PNGs |

**Run it:** start the backend (`scripts/run.sh`), then in Chrome open `chrome://extensions`, turn on Developer mode,
**Load unpacked** and pick this folder. After a change: click the reload button on the Pricise card in `chrome://extensions`,
refresh the shop tab, then close and reopen the side panel.

**Test it:** there are no JS unit tests yet. Check by hand on `http://localhost:8787/storelab/audiodealz/` (red card, 3 tricks),
`/storelab/generic-shop/` (red) and `/storelab/store-c/` (green), and on a search or news page (no card).

**Rules:** the extension holds no keys and only talks to `http://localhost:8787`. On real sites it never clicks or submits;
the only page change is unticking an add-on when the user presses "Untick". UI colours come from `shared/tokens.css` only.
