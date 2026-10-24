# backend/

The local Pricise backend (FastAPI on http://localhost:8787) and the agents behind it.

| Path | What it is | Owner |
|---|---|---|
| `stub_server.py` | The API the extension calls (`/check`, events stream, `/chat`, wishlist, demo clock). Scripted agents by default; `AGENT_DEAL=real` swaps in the real Deal Agent | Shriganesh (until Abel's orchestrator replaces it) |
| `contracts.py` | Shared data shapes (Finding, FinalCard, PageFacts…). **Change only with Abel's OK** | Abel |
| `agents/deal.py` | Deal Agent: fake discount, resetting timer, pre-ticked add-on, drip fees | Emmanuel |
| `agents/chat.py` | "Ask about this page": answers only from the stored check | Shriganesh |
| `agents/trust.py`, `agents/trust_api.py` | Trust Agent: domain age, company record, copied photos, review patterns, returns policy; `POST /internal/trust/vet` for the Price Agent; `AGENT_TRUST=real` swaps it in. See `docs/trust_agent.md` | Raj |
| `agents/price.py`, `skeptic.py` | Not written yet | Josh, Abel |
| `tools/` | Trust tools: `domain.py`, `registry.py`, `photo_match.py`, `reviews.py`, `returns.py`, `trust_common.py` (Raj). Deal tools: `runahead.py` (checkout walk + pay guard), `timer_test.py`, `discount.py`, `addons.py`, `browser.py` (shared headless Chromium) | Emmanuel |
| `llm/meterless.py` | The only way to call a model (Meterless Nightshift). Minimal client for now | Abel |
| `data/` | `history.py` (`get_price_history`, JSON fixture today, Zetaris later) | Josh |
| `engines/` | Meterless engines (World Model, H-MEM, Scout Intent, Markovian): not written yet. `world_model_stub.py` is an in-memory stand-in Trust writes to until the World Model lands | Abel |
| `env.py` | Loads `.env` at startup (shell variables win; no `.env` is fine) | |
| `tests/` | pytest suite | everyone |

**Run it:** `scripts/run.sh` (add `--real` for the real Deal Agent). **Test it:** `scripts/test.sh`.
Tests that need the hidden browser skip themselves when Chromium isn't installed.

**Rules:** every model call goes through `llm/meterless.py`; timeouts on every network call (max 8 s); the hidden browser only
runs on Store Lab pages (localhost + `/storelab/` or `/bench/`) and never clicks pay, place order, confirm or submit.
