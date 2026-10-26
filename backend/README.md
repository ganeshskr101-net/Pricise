# backend/

The local Pricise backend (FastAPI on http://localhost:8787) and the agents behind it.

| Path | What it is | Owner |
|---|---|---|
| `stub_server.py` | The API the extension calls (`/check`, events stream, `/chat`, wishlist, demo clock). Scripted agents by default; `AGENT_DEAL=real` swaps in the real Deal Agent | Shriganesh (until Abel's orchestrator replaces it) |
| `contracts.py` | Shared data shapes (Finding, FinalCard, PageFacts…). **Change only with Abel's OK** | Abel |
| `agents/deal.py` | Deal Agent: fake discount, resetting timer, pre-ticked add-on, drip fees | Emmanuel |
| `agents/chat.py` | "Ask about this page": answers only from the stored check | Shriganesh |
| `agents/trust.py`, `agents/trust_api.py` | Trust Agent: domain age, company record, copied photos, review patterns, returns policy; `POST /internal/trust/vet` for the Price Agent; `AGENT_TRUST=real` swaps it in. See `docs/trust_agent.md` | Raj |
| `agents/price.py` | Price Agent: exact same item (model + condition) at a trusted Store Lab store, by true total (price + shipping + fees − card offers). Asks the Trust Agent about every candidate when `AGENT_TRUST=real`. `AGENT_PRICE=real` | Josh |
| `agents/skeptic.py` | Skeptic: six pure rules that challenge other agents' findings (no model decides) | Abel |
| `tools/` | Trust tools: `domain.py`, `registry.py`, `photo_match.py`, `reviews.py`, `returns.py`, `trust_common.py` (Raj). Deal tools: `runahead.py` (checkout walk + pay guard), `timer_test.py`, `discount.py`, `addons.py`, `browser.py` (shared headless Chromium) | Emmanuel |
| `llm/meterless.py` | The only way to call a model (Meterless Nightshift). Minimal client for now | Abel |
| `data/` | `zetaris.py` (REST client, `run_sql`), `history.py` (`get_price_history`), `repo.py` (listings, wishlist, findings), `catalog.py` (the synthetic Store Lab data), `schema.sql` + `pg.py` (the Pricise Postgres Zetaris reads). `DATA_SOURCE=fixture\|zetaris` | Josh |
| `engines/` | Meterless engines. `world_model_ts/` is the **engine sidecar**: one Node process on 127.0.0.1 that hosts the World Model (`src/`), Scout Intent (`scout/`), H-MEM (`hmem/`, the shopper's notes, saved in the git-ignored `.pricise-data/memory/`) and Markovian (`markovian/`, bounded chains for the wishlist watch). The folder keeps its first name. Python clients: `world_model.py` (shared finding state), `scout.py` (guard for untrusted page and model text, with a local-rules fallback), `hmem.py` (notes the user typed, privacy checks, local outbox when the sidecar is down) and `markovian.py` (runs the watch's chunks on the engine, locally when the sidecar is down). See `docs/hmem-report.md` and `docs/markovian-report.md`. `world_model_stub.py` is Raj's temporary in-memory stand-in that Trust writes to until it uses the World Model. | Abel (stub: Raj) |
| `watch.py` | Wishlist watch: checks each item's price per day, decides a real drop by rules (no model), one alert per item, checkpoint of facts in the git-ignored `.pricise-data/watch/`, `reset()` for demo retakes. Not wired into `stub_server.py` yet (see `docs/markovian-report.md`) | Abel |
| `env.py` | Loads `.env` at startup (shell variables win; no `.env` is fine) | |
| `tests/` | pytest suite | everyone |

**Run it:** `scripts/run.sh` (add `--real` for the real Deal Agent). **Test it:** `scripts/test.sh`.
Tests that need the hidden browser skip themselves when Chromium isn't installed.

**Rules:** every model call goes through `llm/meterless.py`; timeouts on every network call (max 8 s); the hidden browser only
runs on Store Lab pages (localhost + `/storelab/` or `/bench/`) and never clicks pay, place order, confirm or submit.
