# Emmanuel: Deal Agent

## Before the prompt
- Confirm Cursor, Zetaris and Nightshift checks pass; install Playwright for Python (pip install playwright). Do not run "playwright install" if Chromium is already provided.
- Pull the repo skeleton; create the branch emmanuel/deal.
- Paste the prompt below into your coding agent with the repo open.

## Paste this prompt into your coding agent

```text
You are my coding partner. Read AGENTS.md first and follow it exactly. Then build my part.

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

MY ROLE: Deal Agent. It answers "Is this deal honest?" by catching four tricks, each with proof.

BUILD backend/agents/deal.py with async def assess_deal(page_url: str, check_id: str) -> list[Finding], running these tools in parallel:
1. backend/tools/runahead.py - Playwright (headless Chromium) adds the item to the cart and walks the checkout steps to the LAST step before
   payment, reading every line item and fee. SAFETY GUARD: a deny-list check runs before every click and refuses any button or link whose
   text or attributes match /pay|place order|confirm|submit order|complete purchase|buy now/i on checkout pages. Unit-test the guard.
   Output: list of fees with the step where each appeared, and the real total. Finding type "drip_fees" if total > displayed price.
2. backend/tools/timer_test.py - open the page in 3 fresh browser contexts a few minutes apart (configurable; 20 s in SAMPLE_MODE),
   read the countdown text each time, screenshot it. If it restarts, Finding type "fake_timer" with 3 screenshots as evidence.
3. backend/tools/addons.py - find pre-checked checkboxes or toggles that add cost; Finding type "preticked_addon" with a CSS selector as evidence
   (kind "selector") so the extension can outline it. Expose the selector for POST /action/untick (only runs after user approval).
4. backend/tools/discount.py - query Zetaris price_history for this listing; if the "was" price appeared within 30 days before the sale and the
   sale price equals the typical price over the prior 60+ days, Finding type "fake_discount" with the SQL and rows as evidence.
- Use the Nemotron FAST lane only to read messy page text (e.g. fee labels); decisions follow the explicit rules above.
- Write all findings to the World Model; respond to Skeptic challenges (e.g. "rerun the timer test") via the function rerun(finding_id).

TESTS: on the AudioDealz Store Lab page you must find: fake discount, fake timer, drip fees (real total $191.00), and the pre-ticked add-on.
On Store I (clean) you must find nothing.
ALSO: create bench pages for fake discount (7), timer (6), drip fees (7), add-on (6) with labels in bench/labels.csv.
DONE WHEN: all four AudioDealz tricks are caught with evidence in under 10 seconds (timer test excluded in SAMPLE_MODE).
```
