# Paste this into the Claude Code panel in VS Code

Open the `pricise` repo folder in VS Code first. CLAUDE.md loads automatically.

```text
Read CLAUDE.md, AGENTS.md, README.md and docs/PLAN.md, then look at docs/mockups/v1/ui_panel.png.
This repo already has a working v0.1 of my part (the Chrome extension UI + a stub backend). Don't rewrite it; build on it.

Step 1 - Get it running with me (stop after this step and wait for me):
- Check I have Python 3.11+. Create a virtual environment (.venv), install backend/requirements.txt plus pytest and httpx,
  and run python -m pytest backend/tests. Tell me if anything fails.
- Start the stub backend in the VS Code terminal: python -m uvicorn backend.stub_server:app --port 8787 --reload
- Walk me through loading extension/ in Chrome (chrome://extensions > Developer mode > Load unpacked), pinning it, and opening
  http://localhost:8787/storelab/audiodealz/ then clicking the Pricise icon. Tell me exactly what I should see.

Step 2 - Real store pages (my top priority; the product must work on real pages):
- I'll open real product pages and tell you or screenshot what the panel shows. When product name, price or a signal is wrong or missing,
  fix the page-reading code in extension/content/content.js (keep it read-only, keep the careful wording rules in AGENTS.md).
- Add a small debug view: in the side panel, a "What Pricise read" toggle at the bottom that shows the raw page facts JSON,
  so I can see what was extracted.

Step 3 - Demo polish (one at a time, show me each before the next):
a) Checkout approval screen for Store Lab, matching docs/mockups/v1/ui_checkout.png: "Stopped before payment. Your approval is needed."
b) "Shuffle" button on the Store Lab page that randomises which tricks appear, so judges see it isn't scripted.
c) Preference toggle in the panel: "Refurbished OK" (when on, Store B at $199 wins instead of Store C).
d) A banner when Scout Intent blocks a hidden instruction on a page ("This page tried to instruct the agents. Ignored.").
For c) and d) the stub backend needs new fields: propose the change to backend/contracts.py first, because Abel owns it.

Rules: small changes, tell me which files you changed and what to reload in Chrome after each change.
```
