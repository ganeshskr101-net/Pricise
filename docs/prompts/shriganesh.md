# Shriganesh: product lead, UI and repo bootstrap

## Before the prompt
- Create the GitHub repository "guardian", add the other four as collaborators, and protect the main branch (pull requests only).
- Install Cursor, then confirm your Zetaris and Nightshift setup checks pass (setup guide, "Verify your setup").
- Paste the prompt below into Cursor's Agent chat with the empty repo open. Push the skeleton within the first hour so everyone else can start.

## Paste this prompt into your coding agent

```text
You are my coding partner in Cursor. Read the shared context, then do my tasks in order.

SHARED CONTEXT (identical in every teammate's prompt; do not change it)
Project: "Guardian", a Chrome extension plus local agents, for the Open Agent Hackathon 2026, Track 3 (Reasoning Architecture).
On any product page Guardian answers three questions before the user clicks Buy:
  1. Shop safe?   2. Honest deal?   3. Best price for the exact same item, from trusted stores only?
Four agents: Trust Agent (Raj), Deal Agent (Emmanuel), Price Agent (Josh), Skeptic + orchestrator (Abel). UI and demo: Shriganesh.
Hard rules:
- Every model call goes through Meterless Nightshift (OpenAI-compatible) via backend/llm/meterless.py only. Read METERLESS_BASE_URL,
  METERLESS_RUNTIME_TOKEN, NEMOTRON_FAST_MODEL, NEMOTRON_DEEP_MODEL from .env. Never call any other model provider directly.
- All data lives in Zetaris (Docker, REST at ZETARIS_REST_URL, POST /sql-editor/sqls/run, Bearer ZETARIS_API_KEY, X-Org-ID, fresh UUID X-Request-ID).
- Secrets only in .env. Commit .env.example with placeholders. Never print, log or commit keys.
- We only use our own made-up stores (storelab/). Never automate real websites.
- The hidden browser must NEVER click pay, place order, confirm or submit. It stops at the payment step.
- Every finding uses this JSON shape and is written to the shared World Model:
  {"check_id","agent","type","status":"proposed|challenged|confirmed|rejected|unverified","summary",
   "evidence":[{"kind":"screenshot|log|sql|price_row|url","ref"}],"confidence":0-1,"model","challenged_by","rounds","ts"}
- Backend API (FastAPI, http://localhost:8787): POST /check {url} -> {check_id}; GET /check/{id}/events (SSE: one event per finding, then "final_card");
  GET /check/{id}; POST /action/untick; POST /wishlist; GET /wishlist; POST /demo/clock {day}; GET /health.
- Repo layout: extension/ backend/{agents,tools,engines,llm,data}/ storelab/ bench/ docs/ README.md .env.example AGENTS.md
- Python 3.11+, type hints, small functions, pytest tests for every module, timeouts on every network call (max 8 s), no unbounded retries.
- Before creating or deleting anything big, show me a plan and wait for my go-ahead. Explain each file you create in one line.

MY ROLE: product lead and UI owner. I also bootstrap the repo for the whole team tonight.

TASK 1 - Repo skeleton (do this first, then stop and show me):
- Create the folder layout above with a placeholder __init__.py or README.md in each folder.
- Write AGENTS.md at the root containing the shared context above, so every coding agent on the team follows it.
  Also create CLAUDE.md that only says: "Follow AGENTS.md".
- Write .env.example with placeholder values for every variable named above, plus BACKEND_URL=http://localhost:8787 and SAMPLE_MODE=true.
- Write .gitignore (include .env, node_modules, __pycache__, traces/, *.png under bench/out).
- Write README.md with: one-paragraph pitch, setup steps placeholder, team table (Shriganesh UI, Raj Trust, Emmanuel Deal, Josh Price + Store Lab, Abel orchestration + Skeptic + Meterless).
- Write backend/contracts.py with Pydantic models for Finding, FinalCard (three verdicts: safe, honest, best_price, each with status and reasons), WishlistItem.
- Write backend/stub_server.py: a FastAPI app on port 8787 that implements every endpoint above with FAKE data matching the demo
  (AudioDealz page: 3 tricks, real total $191, Store C best safe price $229), streaming findings over SSE with small delays.

TASK 2 - Chrome extension (Manifest V3) in extension/:
- manifest.json with side_panel, storage, notifications, activeTab, scripting, and host permission for http://localhost:8787/*.
- Side panel (extension/sidepanel/): header "Guardian" with "Checked in Ns"; three cards in this order:
  Shop safe? (amber/red/green), Honest deal? (lists tricks), Best safe price (store, true total, buttons Go, Watch price, See why);
  agent chips Trust, Deal, Price, Skeptic; footer "Analysed on this laptop". Cards fill in live as SSE events arrive.
- Content script (extension/content/): draws a 3px dashed red outline and a small red label on each page element a finding points to
  (finding.evidence with kind "selector"). Toolbar badge shows the number of tricks.
- Evidence page (extension/evidence/): price-history chart, timer replay (3 visit frames), fee breakdown table, store comparison list, agent trace.
- Wishlist page (extension/wishlist/): table of items with rule and status, "This week" stats (price changes seen, alerts sent, tricks caught,
  stores checked), privacy box, demo clock buttons Day 1..Day 9 calling POST /demo/clock, and chrome.notifications for new alerts.
- Style: Inter or system font, white surfaces, 12px radius cards, colour only for meaning (amber caution, red tricks, green good), min 13px text.

TASK 3 - Make it run against the stub server end to end, then write the README "Run the demo" section.
Definition of done: loading the AudioDealz page from storelab (or a local HTML copy) opens the panel, the three cards fill in,
outlines appear on the page, See why opens the evidence page, and Watch price adds the item to the wishlist.
```
