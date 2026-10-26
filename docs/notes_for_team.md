# Notes for the team

From Abel, 26 Oct 2026, branch `abel/price-ready` (Price Agent wired into the orchestrator, Skeptic rechecker, cleanup).
Each item names a file you own. I did not edit your files; the change is yours to make (or to reject).

## Shriganesh

**Three card bugs in `backend/stub_server.py` `real_card()`** (seen in the full bench, 26 Oct; the bench now reports
both `card.tone/card.tricks` and `card.safe` per page, so you can see each one there):

1. **`fake_discount` is missing from `TRICK_TYPES`** (line 230: `{"prechecked_addon", "resetting_timer", "drip_fees"}`).
   Example `bench/deal/fd-02.html`: the Deal Agent's `fake_discount` is **confirmed** with 2 evidence items (the Skeptic
   keeps it confirmed), but the card says tone `good`, 0 tricks, "No tricks spotted · Looks honest". Same on fd-01..fd-05.
   The AudioDealz demo is not affected (`_demo_card_live` counts it). Fix: add `"fake_discount"` to `TRICK_TYPES`.
2. **The card's tone and title ignore the Trust verdict.** Example `bench/trust/t-01/index.html`: `card.safe` is `bad`
   (Trust verdict unsafe: domain 4 days old, no company record, copied photos) but `card.tone` is `good` and the title says
   "No tricks spotted". `real_card` sets the tone from tricks only, and the card builder replaces only `card.safe`.
3. **A "proposed" trick is counted as a trick.** Example `bench/deal/ad-06.html` (labelled honest: a pre-ticked
   "Send me offers by email" box with no cost): the Deal Agent's `prechecked_addon` is only **proposed**
   (`tools/addons.py` confirms an add-on only when it costs money), but `real_card` counts every status except
   unverified, so the card says "Be careful here", 1 trick. Suggested fix: count only confirmed tricks and list proposed
   ones under "Check these". The bench shows this as a shopper-visible false alarm; its confirmed-only line does not count it.

- **`backend/tests/content_reader.py` `FAKE_CHROME` breaks on current Chromium:** `window.chrome` is a non-configurable
  property (Chromium builds 1200 and 1228), so `Object.defineProperty(window, 'chrome', ...)` throws and
  `window.__priciseRead` never exists. Every browser test that uses it fails once Chromium is found (they skip when it
  isn't). Fix: wrap it in `try { ... } catch { Object.assign(window.chrome, fake); }`. `bench/run_bench.py` has this fix
  in its own copy (`BENCH_CHROME`).
- **Merging this branch:** use a merge commit (not squash), so the history keeps the separate steps.
- **Pin `AGENT_ORCH` in `backend/tests/conftest.py`:** add `monkeypatch.delenv("AGENT_ORCH", raising=False)` and
  `monkeypatch.setenv("WORLD_MODEL_URL", "http://127.0.0.1:1")`, like the other switches. Today a developer's shell or
  `.env` value changes the test results.
- **With `AGENT_ORCH=on`, 6 tests failed before this branch; 3 still fail** (all correct behaviour or test fixtures):
  - `test_chat.py::test_page_instructions_are_not_obeyed` and `::test_model_that_obeys_the_page_is_overruled`: the
    orchestrator's Scout guard strips the injected text from `chk["page"]` before `chat.has_injection()` runs, so chat no
    longer sees it. Suggested fix in `chat.py`: also treat a `hidden_instructions` finding as injection.
  - `test_stub_server.py::test_agent_deal_real_sends_a_verdict_early_and_firms_it_up`: the test's fake `resetting_timer`
    finding has no evidence, so Skeptic rule 1 rejects it (2 tricks, not 3). That is correct behaviour: give the fake
    finding an evidence item.
  - `test_price.py` x3: fixed by the Price wiring on this branch.
- **Run scripts need Node 20+ and the sidecar:** add `npm install --ignore-scripts` in `backend/engines/world_model_ts`
  to `scripts/setup.sh` / `setup.ps1`, and start the sidecar (`npx tsx server.ts`) in `scripts/run.sh` / `run.ps1`.
- **CORS in `backend/stub_server.py`:** `allow_origins=["*"]` (line 79). Restrict it to the extension's origin.
- **Panel adds a row per finding** (`extension/background.js:84` appends every version). With `AGENT_PRICE=real` the
  Skeptic sends 3 versions of each best price (confirmed, challenged, result), so the panel needs replace-by-id
  (same agent, type and evidence = same finding, as `world_model.finding_key` does).
- **`docs/HANDOFF_ABEL.md` is out of date:** it says "436 passed, 1 failed"; on 26 Oct, before this branch's changes,
  the suite gave 432 passed, 5 skipped. It calls Zetaris "Live", but Zetaris is used only with `DATA_SOURCE=zetaris`
  and `PRICISE_PG_*` set (see `docs/price_agent_contract.md`).
- **Windows:** set `PLAYWRIGHT_CHROMIUM_PATH` (see Emmanuel's note on `cached_chromium`).

## Emmanuel

- Timer frames from `deal.rerun()` (and `EVIDENCE["timer_frames"]`) lack `at` / `remaining_s`, so
  `skeptic.visits_from_frames()` turns them into no visits and a timer re-check can only be "unverified".
- Real Deal findings need evidence items: a finding with none is rejected by Skeptic rule 1 under the orchestrator.
- `backend/tools/browser.py` `cached_chromium()` searches only the macOS and Linux caches, not
  `%LOCALAPPDATA%\ms-playwright` on Windows. Without `PLAYWRIGHT_CHROMIUM_PATH`, the bench stopped with
  "Chromium could not start".
- The run-ahead's checkout steps will need a token later (a real checkout would refuse a form post without one).

## Raj

- `backend/agents/trust.py` lines 35-38: drop the duplicate `write_finding` (the World Model has no `write_finding()`,
  so it always falls back to `world_model_stub.py`; the orchestrator already records findings).
- `_reword()` (line ~96): mark the reasons as page-derived text with `meterless.untrusted()` before they go into the prompt.
- Move the prompt log (`docs/prompts_log/raj.md`) to the agreed folder.
- Please confirm that caution stores are blocked from the best price. The Price Agent rejects them with
  `use_trust=True` and the docs now say "only safe stores can win"; the Skeptic's rule alone would let caution pass.

## Josh

- **Add `variant` and `warranty_months` to the listings data** (`backend/data/catalog.py`, the seed, the `listings` SQL).
  Today the adapter writes `variant=1;warranty=1` and the finding says "variant and warranty not in data, assumed equal".
- **Optional: a mode that emits one `best_price` finding per candidate**, in the exact-match ref shape (see
  `docs/price_agent_contract.md`). Today there is one finding, for the winner, and `PriceResult.alt` is the winner
  itself, so when the Skeptic rejects the winner the card can only say "no runner-up".
- **Store B is a refurbisher, not a look-alike.** There is no look-alike store page in Store Lab.
- **Add `lookalike_listing` and `clean_control` labels to `bench/labels.csv`** (with pages). The bench prints
  "n/a (0 labelled pages)" for exact match until then; the plan expects 7 and 10.
- **Zetaris setup:** please document how to register `pricise_pg` in Zetaris and seed the Postgres. On my machine
  Zetaris answers, but `pricise_pg.stores` is "TABLE_OR_VIEW_NOT_FOUND", so every run uses the fixture.
- **Confirm the `ZETARIS_ORG_ID` name** (it is sent as `X-Org-ID`).
- The wiring calls `price._identify` and `repo._catalog_listings`. If you rename them, the contract test in
  `backend/tests/test_price_wiring.py` fails: tell me, or keep a public name.

## Everyone

- Wrap page or user text that goes into a prompt with `meterless.untrusted()` (`backend/llm/meterless.py:82`).
