# Pricise

**Shop safe? Honest deal? Best safe price?** Pricise is a Chrome extension that checks any product page before you buy.
It reads the page you're on, spots tricks (fake discounts, countdown timers that reset, pre-ticked add-ons, fees added late),
checks the store, and finds the best price for the exact same item at a trusted store. Four agents (Trust, Deal, Price, Skeptic)
check each other's work, every claim comes with proof, nothing is bought without your approval, and your data stays on your laptop.

Built for the Open Agent Hackathon 2026, Track 3: Reasoning Architecture. Uses Cursor, Zetaris, Meterless Nightshift and NVIDIA Nemotron.

## Run the demo (v0.1, stub backend)

You need Python 3.11+ and Google Chrome.

```bash
# 1. Start the local backend (fake agents for now)
python -m pip install -r backend/requirements.txt
python -m uvicorn backend.stub_server:app --port 8787
```

2. In Chrome, open `chrome://extensions`, turn on **Developer mode** (top right), click **Load unpacked**, and pick the `extension/` folder.
3. Pin Pricise: click the puzzle-piece icon in the toolbar, then the pin next to Pricise.
4. Open http://localhost:8787/storelab/audiodealz/ and click the Pricise icon. The side panel opens and fills in live; red outlines appear on the page.
5. Try: **Untick $4.99 add-on**, **See all evidence**, **Go to Store C**, **Watch price**, then **Wishlist** (footer) → **Day 9** for the desktop notification.
6. Open any real product page: Pricise reads it and reports what it can verify today. Store checks and other-store prices show
   "not connected yet" until the Trust and Price agents replace the stub.

Test pages: `storelab/audiodealz/` (hero demo, scripted), `storelab/store-c/` (honest store), `storelab/generic-shop/` (checkout page
that exercises the real page-reading code).

Run the backend tests: `python -m pip install pytest httpx && python -m pytest backend/tests`

### Real Deal Agent (optional)

```bash
AGENT_DEAL=real TIMER_GAP_S=3 python -m uvicorn backend.stub_server:app --port 8787
```

`AGENT_DEAL=real` streams the real Deal Agent (`backend/agents/deal.py`) instead of the scripted deal findings; everything else stays
scripted. On Store Lab pages it uses a hidden headless browser to walk the made-up checkout (it stops at payment and never clicks pay)
and to visit the page 3 times for the timer test (`TIMER_GAP_S` seconds apart, default 20). On real store pages it only reads what the
extension sent. Screenshots go to `traces/`. Deal bench: `python bench/run_deal.py` (26 labelled pages in `bench/deal/`).

## Team
| Person | Part |
|---|---|
| Shriganesh | Product lead, extension UI, demo |
| Raj | Trust Agent |
| Emmanuel | Deal Agent |
| Josh | Price Agent, Store Lab, Zetaris |
| Abel | Orchestrator, Skeptic, Meterless + Nemotron |

## Notes
- Store Lab stores are made up, and their price histories are synthetic. Real store pages are read only; Pricise never clicks or buys on them.
- Secrets live in `.env` (copy `.env.example`). Never commit `.env`.
