# Trust Agent (Raj)

Answers **"Shop safe?"** for the page the user is on and for every store the Price Agent proposes:
`safe`, `caution` or `unsafe`, with reasons and evidence.

## Run it

```bash
scripts/test.sh -k trust                      # or: python -m pytest backend/tests -k trust
python bench/run_trust.py                     # accuracy on bench/trust + the three demo stores
AGENT_TRUST=real scripts/run.sh               # the panel's "Shop safe?" comes from the real agent (combine with --real for Deal)
curl -s localhost:8787/internal/trust/vet -H 'content-type: application/json' \
     -d '{"store_url":"http://localhost:8787/storelab/store-d/"}'
```

## Two modes (same split as the Deal Agent)

- **Store Lab** (localhost + `/storelab/` or `/bench/`): all five checks below.
- **Real store pages:** read only. Nothing is fetched from the real site. Only the extension's HTTPS fact is used; the store checks
  come back `unverified` ("…were not checked") and the panel shows "Not fully checked". Nothing unchecked is ever called safe.

## The rules (the model never decides the verdict)

| Finding type | Tool | Strength | Flagged when |
|---|---|---|---|
| `domain_age` | `tools/domain.py` | strong | domain created < 30 days before `as_of` |
| `no_company` | `tools/registry.py` | strong if both missing, weak if one | no address and/or no company number |
| `copied_photos` | `tools/photo_match.py` | strong | a product photo matches another store's photo (phash distance ≤ 4) **and this store's domain is younger** |
| `review_burst` | `tools/reviews.py` | weak | > 40% of reviews inside any 7-day window (needs ≥ 10 reviews) |
| `duplicate_reviews` | `tools/reviews.py` | weak | > 20% of reviews repeat the same text |
| `bad_returns` | `tools/returns.py` | weak | no policy page, "no returns / all sales final", or < 7 days |
| `no_https` | `agents/trust.py` | strong | the page isn't HTTPS (real pages, from the extension) |

`decide()` in `backend/agents/trust.py`:
- **unsafe:** 2+ strong signs, or 1 strong + 2 weak
- **caution:** 1 strong sign, or 2+ weak ones
- **safe:** anything less (one weak sign is shown as a note)

A store that doesn't answer → `caution`, `checked: false`, "Couldn't check this store". A check that can't run is an
`unverified` finding, never a guess.

The photo rule: when Store D copies Store C's photo, only the younger domain (D) is flagged; without this, the honest victim
would be flagged too. Manufacturer photos in `storelab/_official/` never count as copied.

Nemotron (fast lane, `meterless.complete`) only rewords the reasons when `TRUST_LLM_SUMMARY=true`, with a 2 s cap. If the
reply doesn't have the same number of lines, the rule text is kept. A test checks that the model can't change the verdict.

## For Josh (Price Agent)

```python
from backend.agents.trust import assess_store
tv = await assess_store(store_url, check_id)      # {"verdict", "reasons", "findings": [Finding], "store", "store_url", "checked", "elapsed_ms"}
if tv["verdict"] == "unsafe": ...                 # drop the candidate, cite tv["findings"]
```
Or `POST /internal/trust/vet {"store_url"}` (same fields, findings as dicts). Results are cached for 5 minutes per URL.
Vet time against the backend: about 30 ms warm, about 0.3 s for the very first vet (hashing the other stores' photos).

Each Store Lab store you add needs `about.json`, `returns.html`, `listings.json` and `reviews.json` (see `storelab/README.md`;
copy `storelab/store-c/`). A dynamic server can serve `/returns`, `/api/listings` and `/api/reviews` instead.
Other stores in the same folder are found automatically and compared for copied photos.

## For Abel (orchestrator, World Model, Skeptic)

- Only warning signs and checks that couldn't run are emitted as findings (the panel paints every trust row as a warning).
  Passing checks still count in the verdict and appear in the card. Store C therefore streams no trust rows.
- Findings are `contracts.Finding` with `agent="trust"`, `model="rules (no model)"`, status `proposed` or `unverified`.
- They are written through `write_finding(finding)` from `backend.engines.world_model` (sync or async both work). Until that
  module exists, `backend/engines/world_model_stub.py` stands in; delete it after merge. Writing is keyed by
  (check_id, agent, type), so reruns don't duplicate.
- The router is in `backend/agents/trust_api.py`: `app.include_router(trust_router)`.
- `trust.safe_verdict(tv)` builds the card's `safe` section (a `contracts.Verdict`).
- Bench rows are in `bench/labels.csv` (`trust/…`, trick `risky_store`, expected 1 = caution/unsafe). Your branch rewrites
  `labels.csv` line endings, so keep the 12 `trust/` rows at the end when you merge.

## Results (made-up stores; labels written before running)

Risky stores caught 7/7, false alarms 0/5. AudioDealz is unsafe, Store C is safe, Store D is unsafe.
This is a smoke test on our own fixtures, not a claim about real shops.
