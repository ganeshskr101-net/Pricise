# Price Agent contract (orchestrator wiring)

How the orchestrator uses Josh's Price Agent (`backend/agents/price.py`) when `AGENT_ORCH=on` and `AGENT_PRICE=real`.
Owner of this wiring: Abel (`backend/orchestrator_stub.py`, `backend/rechecker.py`). Owner of the agent: Josh.
With `AGENT_PRICE=stub` (the default) nothing here runs: the scripted price rows stay and the Skeptic has no rechecker.

## What the wiring calls

```python
res = await price.assess_async(check_id, url, page, day, use_trust=AGENT_TRUST == "real")  # -> PriceResult, never raises
```

| Field of `PriceResult` | Used for |
|---|---|
| `findings` | Streamed as the `price` agent's findings (after the adapter below) |
| `best_price`, `alt` | The card, through `stub_server._apply_price` |
| `stores`, `sql` | The evidence page's store table (`evidence_data.stores`, `stores_sql`) |
| `source` | Which data the adapter reads again (`zetaris`, `fixture`, `fixture (Zetaris unavailable)`) |

The wiring imports `price` inside the agent function, so the default path never loads it.

## What the Price Agent emits

- One `catalog_match` finding (confirmed, or unverified when the item is not in the catalog).
- `candidate` findings, grouped by reason: rejected (different model, different condition, store not safe) or
  unverified (store down, Trust could not check). Evidence: the store URLs.
- **One** confirmed `best_price` finding, for the winner only. Evidence: `url` (the store), `price_row`
  `listing:<store_id>:<product_id>:day<n>`, and the Zetaris SQL (`sql`) or the catalog (`log`).
- On a real store page: one unverified `catalog_only` finding ("covers our demo catalog only"). On any error: one
  unverified `best_price` finding with no evidence ("Couldn't compare").

Store safety: with `use_trust=True` the Price Agent asks the Trust Agent about every candidate and rejects both
`unsafe` and `caution`. With `use_trust=False` it uses the seeded catalog flags (`scam` and `risky` are rejected).
**Only stores the Trust Agent calls safe (or seeded `ok`) can win a best price.**

## The adapter (`rechecker.adapt`)

The Price Agent's findings are streamed as it made them, with one change: its confirmed `best_price` finding gets an
extra `price_row` evidence item in the exact-match shape the Skeptic's rechecker reads:

```
store=<store url>;model=0|1;variant=1;condition=0|1;warranty=1;price=<true total>
```

- The adapter reads the store and product from the `listing:` ref, reads the listings again **from the same source as
  the run** (`PriceResult.source`), and compares that row with the page's own row (`price._identify`).
- `model` and `condition` come from the data. **The catalog has no variant or warranty fields**, so both are written
  as 1, and the finding's summary ends with "Model and condition checked against data; variant and warranty not in data,
  assumed equal." Pricise never claims an exact match on all four.
- If the listings can't be read again (for example Zetaris fails between the run and the adapter), the finding keeps no
  exact-match ref: the Skeptic then has no inputs for it and it keeps its status.
- Any agent may emit `best_price` findings already in this shape, one per candidate. The wiring accepts any number.

## The Skeptic's re-check (`rechecker.Rechecker`)

For every `best_price` finding with the exact-match ref, the Skeptic runs `skeptic.check_best_price` with:

- `match`: the four flags from the ref (variant and warranty are always 1, see above).
- `trust_verdict`: with `AGENT_TRUST=real`, `trust.assess_store(url, f"{check_id}:recheck:{store}")` (cached 5 minutes,
  so a store the Price Agent already vetted costs nothing); with `AGENT_TRUST=stub`, the seeded catalog flag
  (`ok` safe, `risky` caution, `scam` unsafe).

The rule rejects a different model or condition, an `unsafe` store and a store nobody could check. It passes `caution`,
but the Price Agent never proposes a caution store, so in practice only safe stores win. Rule 1 still applies first:
a `best_price` with no evidence is rejected. An unverified finding with no evidence ("Couldn't compare") makes no claim,
so the Skeptic leaves it alone.

## The card's best price

`rechecker.best_price_on_card`, called by the orchestrator's card builder:

- Only `best_price` findings with the exact-match ref **that the Skeptic confirmed** count; the cheapest true total wins.
  While any of them is still undecided, the card says "Checking the best price…".
- If the winner is the Price Agent's own, its verdict (reasons, `alt`) is kept.
- If every candidate is rejected, the card says "No safe match" with the Skeptic's reason for each one. **There is no
  runner-up from the Price Agent**: `PriceResult.alt` is the winner itself (the safer choice than the current page), not
  a second store. So with its single finding the card says "No runner-up: the Price Agent proposes one store only".
- No such findings at all (real site, item not in catalog, couldn't compare): the Price Agent's own verdict stays.

After the run, the findings are stored like the non-orchestrator path does (`stub_server._store_findings`, only with
`DATA_SOURCE=zetaris`; a failure is a log warning, once per check).

## Zetaris: what is real

| Mode | What happens |
|---|---|
| `DATA_SOURCE=fixture` (default) | Listings come from `backend/data/catalog.py` in memory. No Zetaris call. |
| `DATA_SOURCE=zetaris` | One Lightning SQL query through the Zetaris REST API (`backend/data/zetaris.py`) on the `pricise_pg` datasource. On `ZetarisError` or `ValueError` the agent falls back to the catalog and says so (`source = "fixture (Zetaris unavailable)"`). |
| Findings storage | Only with `DATA_SOURCE=zetaris` and `PRICISE_PG_*` set: written straight to the Pricise Postgres that Zetaris reads (Zetaris cannot UPDATE a JDBC source). Otherwise nothing is stored. |

- The Zetaris path is real only with `DATA_SOURCE=zetaris`, a reachable Zetaris, the `pricise_pg` datasource registered
  (`scripts/register_zetaris.py`) and the Pricise Postgres seeded (`scripts/seed.py`, `PRICISE_PG_*` set). In every other
  case the fixture is used.
- **The data is synthetic in every mode.** Zetaris serves the same made-up Store Lab catalog that `catalog.py` holds:
  made-up stores, listings, card offers and 90-day price histories.
- On the machine where this was written (26 Oct 2026), Zetaris answered but the `pricise_pg.stores` table was not found and
  `PRICISE_PG_*` were not set, so every run used the fixture and the live Zetaris tests skipped.

## Tests

- `backend/tests/test_price_wiring.py`: fake Price and fake Trust: look-alike rejected, honest cheaper store wins,
  unsafe winner falls back to the runner-up or shows "no runner-up", unchecked store not accepted, no-evidence finding
  rejected, Price outage gives an unverified `agent_failed` finding and the check continues, "Couldn't compare" stays
  unverified. Josh's real agent (fixture data): exact-match ref added, Store C $229 on the card. A contract test against
  `price.assess_async` (skips if `price.py` is missing) pins the signature, the single `best_price` and `alt` = winner.
- `backend/tests/test_price.py` (Josh): the agent itself, including "caution is not a safe price".

## Known limits

- One `best_price` finding per run, so no runner-up (a per-candidate mode is a note for Josh in `docs/notes_for_team.md`).
- Variant and warranty are assumed equal (not in the data).
- The adapter calls `price._identify` and `repo._catalog_listings` (private names); the contract test fails if they change.
- No labelled look-alike pages exist, so the bench's exact-match line is "n/a (0 labelled pages)".
