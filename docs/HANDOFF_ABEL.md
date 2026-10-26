# Handoff: Abel (Meterless, Nemotron, engines, orchestrator)

State on 25 Oct 2026, from the code on `main`. Updated 26 Oct 2026 (branch `abel/price-ready`): the test counts and the
Zetaris and orchestrator lines below. For the current counts run `scripts/test.sh`; on 26 Oct, before that branch's
changes, the suite gave 432 passed, 5 skipped, 0 failed.

## 1. What works now

| Part | Status | Where |
|---|---|---|
| Deal Agent | Live: fake discount, resetting timer, pre-ticked add-on, late fees (hidden browser, Store Lab only) | `backend/agents/deal.py`, `backend/tools/` |
| Trust Agent | Live: domain age, company record, copied photos, reviews, returns; vets every store the Price Agent considers | `backend/agents/trust.py` |
| Price Agent | Live: same model + condition, true total = price + shipping + fees − card offers; only stores Trust calls safe | `backend/agents/price.py` |
| Zetaris | Live **only with `DATA_SOURCE=zetaris` and `PRICISE_PG_*` set** (and `pricise_pg` registered, the Postgres seeded): reads go through Zetaris REST from the `pricise_pg` datasource (the Pricise Postgres on :5433); writes go straight to that Postgres (Zetaris can't UPDATE a JDBC source). Otherwise the in-memory fixture is used; the data is synthetic in every mode | `backend/data/` |
| UI | Side panel, quick card, red outlines, evidence page (chart, fees, replay, store table, Zetaris SQL), chat, untick, wishlist + Day 9 notification | `extension/` |

**Full live check (one command), then open http://localhost:8787/storelab/audiodealz/ in Chrome with the extension loaded:**

```bash
AGENT_TRUST=real AGENT_PRICE=real DATA_SOURCE=zetaris scripts/run.sh --real
```

AudioDealz must show: **Risky** store · **3 tricks** (fake 40% off, timer resets, $37 late fees) · real total **$191**, not $149 ·
best safe price **Store C $229** · "Store D, $189: blocked by the Trust Agent".

## 2. Setup on a fresh pull

| Step | Command |
|---|---|
| Python venv + packages | `scripts/setup.sh` (creates `.venv`, installs `backend/requirements-dev.txt`, Playwright Chromium, copies `.env.example` to `.env`) |
| Chromium only, if setup's download failed | `.venv/bin/python -m playwright install chromium` |
| Engine sidecar (Node 20+) | `cd backend/engines/world_model_ts && npm install --ignore-scripts` · run it: `npx tsx server.ts` |
| Zetaris | `cd ~/zetaris/zetaris-platform && docker compose start` · check: `.venv/bin/python scripts/check_zetaris.py` |
| Pricise Postgres | `docker compose -f docker-compose.pricise.yml --env-file .env up -d` |
| Seed it (resets Pricise tables) | `.venv/bin/python scripts/seed.py` |
| Register in Zetaris (once per Zetaris install) | `.venv/bin/python scripts/register_zetaris.py` (dry run), then `--yes` |

`.env` keys (names only; never commit `.env`): `AGENT_DEAL AGENT_TRUST AGENT_PRICE DATA_SOURCE SAMPLE_MODE TIMER_GAP_S
TRUST_LLM_SUMMARY TRUST_AS_OF PLAYWRIGHT_CHROMIUM_PATH METERLESS_BASE_URL METERLESS_RUNTIME_TOKEN NEMOTRON_FAST_MODEL
NEMOTRON_DEEP_MODEL ZETARIS_REST_URL ZETARIS_API_KEY ZETARIS_ORG_ID ZETARIS_DATASOURCE PRICISE_PG_HOST PRICISE_PG_PORT
PRICISE_PG_DB PRICISE_PG_USER PRICISE_PG_PASSWORD PRICISE_PG_ZETARIS_HOST WORLD_MODEL_URL BACKEND_URL`

## 3. What's left for you

**Meterless client today** (`backend/llm/meterless.py`): `fast(messages, timeout=None, **kw)` / `deep(...)` -> `(text, reported_model)`,
raise `MeterlessError`; `complete(prompt, lane="fast", max_tokens=64) -> str` returns `""` on any failure; `configured(lane)`,
`model_name(lane)`, `ping()`. It reads `METERLESS_BASE_URL`, `METERLESS_RUNTIME_TOKEN`, `NEMOTRON_FAST_MODEL`, `NEMOTRON_DEEP_MODEL`.
It sends `Authorization: Bearer <token>`, `meterless-app: guardian` (`_APP_ID`, still the old product name), `meterless-scope: user`.
Every call passes the Scout guard first and is logged to `traces/llm_calls.jsonl` (`non_nemotron` flag).

**Callers today:**

| Caller | Lane | When it actually calls |
|---|---|---|
| `backend/agents/chat.py` `answer()` (line ~226) | fast | Every chat question in the side panel |
| `backend/tools/runahead.py` `fee_name()` (line ~157) | fast | Only for fee labels the rules (`FEE_NAMES`) can't read. AudioDealz's labels are rule-readable |
| `backend/agents/trust.py` `_reword()` (line ~99) | fast | Only with `TRUST_LLM_SUMMARY=true` |
| `backend/agents/skeptic.py` `challenge_reason()` (line ~186) | deep | **Never in the live flow**: no caller outside tests |

So the AudioDealz live check makes **no model call** unless someone uses the chat.

- [ ] **Nightshift primary.** Fill the 4 `METERLESS_*`/`NEMOTRON_*` keys; `.env` still holds `.env.example` placeholders, so `configured()` is False.
- [ ] **Model discovery.** Add `GET {METERLESS_BASE_URL}/models` (same headers) to `meterless.py`; it doesn't exist yet (`_normalize_base_url` only strips `/models`).
- [ ] **Pin lanes.** Set `NEMOTRON_FAST_MODEL` / `NEMOTRON_DEEP_MODEL` to Nemotron ids from `/models`, not `meterless/auto`. Confirm with `non_nemotron: false` in `traces/llm_calls.jsonl`.
- [ ] **A model call in the live check.** For example call `skeptic.challenge_reason()` from `Orchestrator._skeptic()` (`backend/orchestrator.py`), or default `TRUST_LLM_SUMMARY=true`. Optional: `meterless-app: pricise`.
- [ ] **A Meterless Engine in the live check.** Status of each engine:

| Engine (`backend/engines/`) | Live flow today? |
|---|---|
| Scout (`scout.py`) | Yes, but only inside a model call (`meterless._guarded`, local rules). Page ingest guard + sidecar only with `AGENT_ORCH=on` |
| World Model (`world_model.py`) | Only with `AGENT_ORCH=on` + sidecar. It has no `write_finding()`, so `trust.py` falls back to `world_model_stub.py` (in memory) |
| H-MEM (`hmem.py`) | Tests only: no caller in the backend |
| Markovian (`markovian.py`) | Only via `backend/watch.py` and `bench/run_watch.py`; `watch.py` isn't wired into `stub_server.py` (the wishlist Day 9 alert is scripted + Price Agent) |

- [x] **`AGENT_ORCH=on` skips the Price Agent and Zetaris findings storage.** Done on `abel/price-ready`: with `AGENT_PRICE=real` the Price Agent runs in the orchestrator, the Skeptic re-checks every best price, and findings are stored after the run (`docs/price_agent_contract.md`).
- [x] **Known failing test** `test_markovian_sidecar.py::test_losing_the_sidecar_mid_run_loses_nothing`: passes on 26 Oct 2026.

## 4. Switches and tests

| Switch | Values (default first) | Effect |
|---|---|---|
| `AGENT_DEAL` | `stub` · `real` | Real Deal Agent (`scripts/run.sh --real`) |
| `AGENT_TRUST` | `stub` · `real` | Real Trust Agent for "Shop safe?" and for every Price Agent candidate |
| `AGENT_PRICE` | `stub` · `real` | Real Price Agent; store safety from Trust when `AGENT_TRUST=real`, else seeded flags |
| `DATA_SOURCE` | `fixture` · `zetaris` | Reads via Zetaris (falls back to the in-memory catalog if Zetaris fails) |
| `AGENT_ORCH` | `off` · `on` | Run checks through `backend/orchestrator.py` (Skeptic, World Model, Scout ingest) |
| `TRUST_LLM_SUMMARY` | `false` · `true` | Nemotron rewords Trust reasons (never the verdict) |
| `WORLD_MODEL_URL` | `http://127.0.0.1:8788` | Engine sidecar (loopback only) |

Tests: `scripts/test.sh` (all) · `scripts/test.sh -k meterless` · `scripts/test.sh -k "markovian or hmem or scout or world_model"`.
Sidecar tests skip without `npm install`; browser tests skip without Chromium.

## 5. Rules

- Don't change the shapes in `backend/contracts.py` (Finding, FinalCard, …) without telling the team first.
- Never commit `.env`; keys only in `.env`, placeholders in `.env.example`; never print or log tokens.
- Real store pages are read only: no clicks, no hidden browser, no scraping other stores; anything unchecked is `unverified`.
