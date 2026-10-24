# CLAUDE.md

Read these first, in order:
1. `AGENTS.md`: the shared rules for every coding agent on the team (contracts, secrets, real-page safety). Follow it exactly.
2. `docs/PLAN.md`: what Pricise is, every decision so far, the UI spec, demo numbers and demo script.
3. `README.md`: how to run the stub backend and load the extension.
4. `docs/mockups/*.png`: the target look (they still say "Guardian"; the product is Pricise).

## Who I am
Shriganesh: product lead. I own the Chrome extension UI and the demo. I build in VS Code with the Claude Code extension.
Teammates build the agents in Cursor: Raj (Trust), Emmanuel (Deal), Josh (Price + Store Lab + Zetaris), Abel (orchestrator, Skeptic, Meterless).

## What already exists (v0.1, working end to end against the stub)
- `extension/`: MV3 extension. Side panel (`sidepanel/`), content script that reads any page and draws outlines (`content/`),
  evidence page (`evidence/`), wishlist + demo clock + desktop notification (`wishlist/`), shared tokens + API client (`shared/`).
- `backend/stub_server.py`: fake backend on :8787. Scripted demo for `storelab/audiodealz/`; heuristic findings for any real page.
- `storelab/audiodealz/` (hero demo page), `storelab/store-c/`, `storelab/generic-shop/` (test page shaped like a real checkout).
- `backend/tests/`: pytest checks of the API contract.

## Rules for you (Claude Code)
- Keep changes small and reviewable. My teammates pull them into Cursor.
- Never change `backend/contracts.py` or the API shapes without telling me; Abel owns them and the agents depend on them.
- On real store pages: read only, careful wording, nothing automated (see AGENTS.md).
- UI style: use `extension/shared/tokens.css` variables only; Inter or system font; white cards, 14px radius; colour only for meaning
  (amber caution, red tricks, green good, grey not checked); minimum 12px text; calm and trustworthy; no new colours.
- No build step: plain HTML, CSS and ES modules, so "Load unpacked" always works.
- After any extension change, tell me exactly what to reload (chrome://extensions reload button, then reopen the side panel).
