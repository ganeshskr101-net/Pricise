# AGENTS.md: rules every coding agent on this team follows

Product: **Pricise**, a Chrome extension plus local agents (Open Agent Hackathon 2026, Track 3: Reasoning Architecture).
Older planning docs call it "Guardian"; same product.

On any product page, real or Store Lab, Pricise answers three questions before the user clicks Buy:
1. **Shop safe?** 2. **Honest deal?** 3. **Best safe price** for the exact same item, from trusted stores only?

Team: Trust Agent (Raj), Deal Agent (Emmanuel), Price Agent + Store Lab + Zetaris (Josh), Skeptic + orchestrator + Meterless (Abel),
UI + demo + repo (Shriganesh).

## Hard rules
- Every model call goes through Meterless Nightshift (OpenAI-compatible) via `backend/llm/meterless.py` only. Read `METERLESS_BASE_URL`,
  `METERLESS_RUNTIME_TOKEN`, `NEMOTRON_FAST_MODEL`, `NEMOTRON_DEEP_MODEL` from `.env`. Never call any other model provider directly.
- All data lives in Zetaris (Docker, REST at `ZETARIS_REST_URL`, `POST /sql-editor/sqls/run`, Bearer `ZETARIS_API_KEY`, `X-Org-ID`, fresh UUID `X-Request-ID`).
- Secrets only in `.env`. Commit `.env.example` with placeholders. Never print, log or commit keys.
- **Real store pages:** the extension may READ the page the user is looking at (product, price, timers, add-ons, fees). It never clicks, submits
  or buys on a real site. The only page change allowed is unticking an add-on when the user presses "Untick".
- **Careful wording on real stores:** say what the evidence shows ("this was-price isn't supported by the price history we found"), never
  "this store is a scam" without proof. Claims we can't check yet are `unverified`.
- **Hidden browser (run-ahead):** Store Lab only (`storelab/`). It must NEVER click pay, place order, confirm or submit; it stops at payment.
- Every finding uses this JSON shape (`backend/contracts.py`, Pydantic):
  `{"check_id","agent","type","status":"proposed|challenged|confirmed|rejected|unverified","summary",
   "evidence":[{"kind":"screenshot|log|sql|price_row|url|selector|text","ref","label?","placement?"}],"confidence":0-1,"model","challenged_by","rounds","ts"}`
  Evidence of kind `selector` + `label` draws a red outline + label on the page.
- Backend API (FastAPI, http://localhost:8787):
  `POST /check {url, page}` -> `{check_id}` (page = what the extension read: product + signals);
  `GET /check/{id}/events` (SSE: `{"kind":"finding"}` per finding, then `{"kind":"final_card"}`); `GET /check/{id}`;
  `POST /action/untick`; `POST /wishlist`; `GET /wishlist`; `POST /demo/clock {day}`; `GET /health`.
  `backend/stub_server.py` implements all of it with fake agents. The real orchestrator must keep the same shapes.
- Repo layout: `extension/ backend/{agents,tools,engines,llm,data,tests}/ storelab/ bench/ docs/ README.md .env.example AGENTS.md`
- Python 3.11+, type hints, small functions, pytest tests for every module, timeouts on every network call (max 8 s), no unbounded retries.
- Before creating or deleting anything big, show a plan and wait for a go-ahead. Explain each file you create in one line.

## Who owns what
| Path | Owner |
|---|---|
| `extension/`, `storelab/audiodealz`, `README.md` | Shriganesh |
| `backend/agents/trust.py` | Raj |
| `backend/agents/deal.py` | Emmanuel |
| `backend/agents/price.py`, `storelab/` stores A-I, `backend/data/` (Zetaris) | Josh |
| `backend/orchestrator.py`, `backend/agents/skeptic.py`, `backend/llm/meterless.py`, `backend/contracts.py` | Abel |
