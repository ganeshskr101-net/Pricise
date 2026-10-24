# Raj: Trust Agent

## Before the prompt
- Confirm Cursor works and that you can reach Zetaris (SELECT 1) and Nightshift (a test request) from your laptop.
- Pull the repo skeleton once Shriganesh pushes it; create the branch raj/trust.
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

MY ROLE: Trust Agent. It answers "Is this site safe to shop from?" for the page the user is on, and for every store the Price Agent proposes.

BUILD backend/agents/trust.py with:
- async def assess_store(store_url: str, check_id: str) -> TrustVerdict  where TrustVerdict = {"verdict":"safe|caution|unsafe","reasons":[...],"findings":[Finding,...]}
- Tools in backend/tools/ (one file each, each with a timeout and a pytest test):
  1. domain.py: domain age from the Store Lab's /about.json (field domain_created) since we use made-up stores; flag < 30 days.
  2. registry.py: business address and company record present? (Store Lab /about.json fields address, company_number).
  3. photo_match.py: perceptual hash (imagehash library) of product images; flag if the same image appears on another Store Lab store's listing.
  4. reviews.py: flag review bursts (more than 40% of reviews in any 7-day window) and duplicate review text.
  5. returns.py: read the returns policy page; flag "no returns", missing policy, or < 7 days.
- Combine signals with explicit, documented rules first (e.g. 2+ strong signals = unsafe, 1 = caution). Use the Nemotron FAST lane only to
  summarise reasons in plain English, never to decide the verdict on its own.
- Write each signal as a Finding (agent "trust", status "proposed") with evidence pointers, to the World Model via backend/engines/world_model.py
  (if Abel's module is not ready, write to an in-memory stub with the same interface).
- Expose POST /internal/trust/vet {store_url} so the Price Agent can call it.

TESTS: pytest for each tool with Store Lab fixtures. Store D (new domain, no address, copied photos) must be "unsafe"; Store C must be "safe";
AudioDealz must be "caution" or "unsafe".
ALSO: create bench/pages/trust_*.html cases (7 scam stores, 5 legitimate) with labels in bench/labels.csv.
DONE WHEN: the Price Agent can vet a store in under 2 seconds and the trust accuracy prints from bench/run_bench.py.
```
