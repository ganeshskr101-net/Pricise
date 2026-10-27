# Sponsor technology in Pricise

What each sponsor technology does in Pricise today, where it runs, and what is not proven. Every claim here can be
checked in the code or the linked report. State on 26 Oct 2026, branch `abel/price-ready`.

## Meterless and Nemotron (model calls)

- Client: `backend/llm/meterless.py`. Two lanes: **fast** and **deep**, each pinned to a Nemotron model by
  `NEMOTRON_FAST_MODEL` / `NEMOTRON_DEEP_MODEL`. Every call passes the Scout guard first and is logged to
  `traces/llm_calls.jsonl`, with a `non_nemotron` flag when the reply came from another model.
- **Fast lane, wired:** the side-panel chat (`backend/agents/chat.py`), unreadable fee labels in the Deal run-ahead
  (`backend/tools/runahead.py`), and Trust reason rewording with `TRUST_LLM_SUMMARY=true`. No model ever decides a verdict.
- **Deep lane: available, not yet wired.** `skeptic.challenge_reason()` uses it to reword a Skeptic reason, but nothing in
  the live flow calls it yet.
- Without `METERLESS_*` keys, `configured()` is False and every caller uses its rule-based text. The AudioDealz demo makes
  no model call unless someone uses the chat.

## Meterless engines (one Node sidecar on 127.0.0.1)

All four run in `backend/engines/world_model_ts/server.ts` (loopback only, 64 KB body limit, field allowlists, browser
requests refused). The Python clients fall back to local behaviour when the sidecar is down.

| Engine | Live in the product? | What is proven |
|---|---|---|
| Scout Intent | Page text guard in every orchestrator check (`AGENT_ORCH=on`); local rules before every model call | Eval run on the Meterless **sales-CRM** corpus: proves the **wiring**, not accuracy on shopping pages ([report](scout-eval-report.md)). Pricise behaviour is tested in pytest. |
| World Model | Shared finding store for orchestrator checks (`AGENT_ORCH=on` + sidecar); the trace reads it back | Spec conformance ([report](conformance_world_model.md)) |
| H-MEM | **Not called by the backend yet** (tests only) | Conformance proves spec compliance. **The embedder is a deterministic hash** (FNV-1a), so retrieval is word overlap, not meaning ([report](hmem-report.md)). |
| Markovian | Wishlist watch (`backend/watch.py`, `bench/run_watch.py`); **not wired into the product yet** | Conformance 8/8. The token numbers are **estimates (chars/4)** against a naive baseline written for the measurement, on made-up prices; nothing in the product sends these prompts to a model ([report](markovian-report.md)). |

## Zetaris

- Client: `backend/data/zetaris.py` (Zetaris REST, Lightning SQL, one statement per call). Readers: the Price Agent's
  listings (`backend/data/repo.py`) and the Deal Agent's price history (`backend/data/history.py`).
- **Real only with `DATA_SOURCE=zetaris`**, a reachable Zetaris with the `pricise_pg` datasource registered
  (`scripts/register_zetaris.py`), and the Pricise Postgres seeded with `PRICISE_PG_*` set (`scripts/seed.py`). The
  Price Agent's best-price finding then cites the SQL it ran.
- **Otherwise the fixture is used:** `DATA_SOURCE=fixture` is the default, and a Zetaris failure falls back to the
  in-memory catalog and says so (`source = "fixture (Zetaris unavailable)"`).
- Writes (wishlist, stored findings) go straight to the Pricise Postgres that Zetaris reads, because Zetaris cannot
  UPDATE a JDBC datasource. Without `PRICISE_PG_*`, nothing is stored.
- **The data is synthetic in every mode**: made-up Store Lab stores, listings, card offers and 90-day price histories
  (`backend/data/catalog.py`). Zetaris serves the same numbers; it does not make them real.
- On the machine where this was written, Zetaris answered but `pricise_pg.stores` was not found and `PRICISE_PG_*` were
  not set, so every run used the fixture. Details: [price_agent_contract.md](price_agent_contract.md#zetaris-what-is-real).

## Agents and the Skeptic

- Deal, Trust and Price agents decide by explicit rules; the Skeptic re-checks with rules only (`backend/agents/skeptic.py`).
- With `AGENT_ORCH=on` and `AGENT_PRICE=real`, the Skeptic re-checks every best price (model and condition from the data,
  store safety from the Trust Agent). **Variant and warranty are not in the data and are assumed equal**; only stores
  the Trust Agent calls safe can win. See [price_agent_contract.md](price_agent_contract.md).

## Honest limits

- **Scout evals prove wiring, not accuracy**: the corpus is sales-CRM, and its labelled prompts are the registry's own seeds.
- **H-MEM's embedder is a hash**, not a learned model.
- **Markovian numbers are estimated** (chars/4) on a stand-in: a baseline written for the measurement and made-up prices.
- **The bench is small samples on made-up Store Lab pages**: 38 labelled pages against a plan of 50, written by the team
  that wrote the agents. No look-alike pages are labelled, so exact match is "n/a (0 labelled pages)".
- **The bench measures Deal Agent findings and Trust verdicts, not the card headline.** Trick detection and the main
  false-alarm line count confirmed findings; scam detection reads the card's "Shop safe?" status (`card.safe`). A second
  line, **shopper-visible false alarms**, counts clean pages whose card shows at least one trick, because today the card
  can disagree with the findings.
- All store data is synthetic. Real store pages are read only, and anything Pricise can't check there is "unverified".
