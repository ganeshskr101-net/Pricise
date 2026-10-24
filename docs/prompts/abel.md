# Abel: orchestrator, Skeptic, Meterless and Nemotron

## Before the prompt
- Tonight's checkpoint is yours: one request from the backend through Nightshift to a Nemotron lane, with the model name in the run log.
- In Nightshift: Providers → NVIDIA key; NIGHT → LANES → a fast Nemotron lane and a deep Nemotron lane; copy the runtime URL and token from Console into .env.
- Open meterless.ai/engines.html and copy the POWER "Add Meterless as a provider" prompt and the IMPLEMENT prompts for World Model, H-MEM, Scout Intent and Markovian. Paste the prompt below first, then those.

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

MY ROLE: the backbone. Meterless + Nemotron integration, the orchestrator, the Skeptic, the four Meterless Engines, and the test-bench harness.

TASK 1 - backend/llm/meterless.py: OpenAI-compatible client to METERLESS_BASE_URL with Authorization Bearer METERLESS_RUNTIME_TOKEN and
the meterless-app / meterless-scope headers required by the official Power Prompt (I will paste it next; follow it as the contract).
Two functions: fast(messages, **kw) uses NEMOTRON_FAST_MODEL; deep(messages, **kw) uses NEMOTRON_DEEP_MODEL. Log provider, model, latency
and token counts for EVERY call to traces/. Timeouts 30 s, one retry, then raise a clear error. Add GET /health checks for Nightshift and Zetaris.

TASK 2 - backend/main.py + backend/orchestrator.py: replace Shriganesh's stub server with the real FastAPI app on port 8787, same endpoints.
POST /check: start Trust, Deal and Price in parallel (asyncio), stream every finding over SSE as it lands, then run the Skeptic, then emit final_card.
Keep a full trace per check (who proposed, who challenged, evidence, model used) in traces/{check_id}.json.

TASK 3 - backend/agents/skeptic.py: enforce these rules, each as a separate tested function:
1. no evidence -> reject;  2. timer is "fake" only after 3 resets;  3. best price must pass exact match and Trust;
4. fake discount needs >= 30 days of history;  5. max 2 challenge rounds, then "unverified";  6. missing evidence -> card says "couldn't verify".
Use the Nemotron DEEP lane to write the challenge reason and the final card wording, but the pass/fail decision follows the rules.

TASK 4 - Meterless Engines in backend/engines/ (follow each IMPLEMENT prompt I paste, keep its conformance tests and save their output to docs/):
- world_model.py: shared findings store all agents read/write; idempotent (re-running a step never duplicates a finding or an alert).
- hmem.py: user preferences, cards, trusted stores, wishlist rules; viewable and deletable from the wishlist page.
- scout.py: intent routing plus a prompt-injection guard on ALL page text the agents read, and approval contracts: untick, checkout and any
  notification action require an approval token from the user.
- markovian.py: wishlist watches run as bounded chunks on a schedule, resume after a crash or laptop sleep, then report a catch-up summary.

TASK 5 - bench/run_bench.py: run all labelled pages in bench/pages, compare with bench/labels.csv, print per-type detection rate,
false alarms on clean pages, exact-match accuracy, scam detection rate and median check time; save results to bench/results.json.
DONE WHEN: one real check runs end to end through Nemotron with at least one Skeptic challenge in the trace, and run_bench.py prints numbers.
```
