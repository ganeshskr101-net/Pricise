# Josh: Price Agent and Store Lab

## Before the prompt
- Confirm Cursor and Zetaris work (./verify.sh passes, SELECT 1 works) and that you have a Zetaris API key in .env.
- Pull the repo skeleton; create the branch josh/price. Build the Store Lab first: everyone else tests against it.
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

MY ROLE: Store Lab (10 made-up stores everyone tests against) and the Price Agent ("Where can I get the exact same item for the best price?").

TASK 1 - Store Lab in storelab/ (do this FIRST and push it early):
- server.py: one small web server (FastAPI + Jinja templates) serving 10 stores on different local ports or path prefixes:
  AudioDealz (hero), Store A..Store I. Clearly fictional names and designs; never imitate a real brand.
- catalog.json: ~12 products with brand, model_number, upc, variant, colour. Hero product: Sony WH-1000XM5.
- Per store: product pages, cart, 4-step checkout ending at a payment page (no real payment), /about.json (domain_created, address,
  company_number), /returns page, /api/listings JSON.
- tricks.py: per-store switches: fake_discount, resetting_timer, drip_fees (fees on step 4), preticked_addon, scam_signals,
  refurbished_lookalike, outage (no response). Configure per the plan: AudioDealz all tricks + scam signals; A honest (+$13 shipping);
  B refurbished look-alike $199; C trusted winner $255 with $26 card offer (=$229), later $235 - $26 = $209; D scam $189; E outage;
  F fake discount; G drip fees; H timer + "only 2 left"; I clean.
- history_gen.py: synthetic daily price histories (label them synthetic), including the planted fake discounts.
- POST /demo/clock {day}: advances prices so Store C reaches $209 on Day 9.

TASK 2 - Zetaris: data/register_tables.sql and backend/data/zetaris_client.py. Create tables stores, products, listings, price_history,
card_offers, wishlist, purchases, findings. Register each store's /api/listings as a Lightning REST table if possible; otherwise load via a script.
Dry-run each SQL statement first and send ONE statement at a time.

TASK 3 - backend/agents/price.py: async def best_price(product_ref, check_id, user_prefs) -> BestPrice
- Exact match: model_number/UPC + variant + condition "new" + warranty >= 12 months unless the user allows otherwise.
- True cost = price + shipping + tax estimate - card offer (from H-MEM user cards) - valid coupons.
- For each candidate cheaper than the current best, call the Trust Agent (POST /internal/trust/vet); drop "unsafe".
- Buy-or-wait: from price_history, say "wait" if the price dropped >= 10% in the same period in past cycles; give the reason.
- Handle store outages: per-store timeout 8 s, then continue and record "unreachable, skipped".
- Write findings to the World Model; respond to Skeptic rejections by trying the next candidate.

TESTS: hero product -> Store C $229 wins; Store B rejected (refurbished); Store D blocked by Trust; Store E skipped.
ALSO: 7 look-alike bench cases (refurbished, wrong variant, bundle, import) with labels. Stretch only after all else works: price-protection claim flow.
DONE WHEN: the Store Lab runs with one command and the Price Agent returns Store C at $229 with evidence.
```
