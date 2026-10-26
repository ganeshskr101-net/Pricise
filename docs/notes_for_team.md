# Notes for the team

From Abel, 26 Oct 2026, branch `abel/price-ready` (Price Agent wired into the orchestrator, Skeptic rechecker, cleanup).
With the owner's agreement for this branch only, the listed bugs in your files were fixed here, one commit per owner,
each with a test that fails before the fix and passes after. **Please review the change in your files.** Items marked
"Open" are still yours to do.

## Shriganesh

**Card bugs in `backend/stub_server.py` `real_card()`** (found in the full bench; the bench now shows both
`card.tone/card.tricks` and `card.safe` per page):

1. **Fixed on this branch.** `fake_discount` was missing from `TRICK_TYPES`. Example `bench/deal/fd-02.html`: a confirmed
   fake discount (2 evidence items) gave tone `good`, 0 tricks, "No tricks spotted". Test:
   `test_stub_server.py::test_real_card_counts_a_confirmed_fake_discount`.
2. **Fixed on this branch.** The card's tone and title ignored "Shop safe?". Example `bench/trust/t-01/index.html`:
   `card.safe` `bad` but tone `good`. New `_safe_headline(card)`: safe `bad` gives tone `bad`, "Don't buy here"; `caution`
   raises a `good` tone to `caution`, "Be careful here"; it never lowers a tone. It runs once `card.safe` is final: in
   `_with_trust` and at the end of the orchestrator's card builder, not inside `real_card` (whose provisional section
   says "Risky" on every `http://` page). Tests: `test_real_card_headline_follows_an_unsafe_or_caution_store`,
   `test_headline_uses_the_final_safe_section_not_real_cards_provisional_one`,
   `test_orchestrator.py::test_trust_real_replaces_not_connected_row_and_sets_card_safe`.
3. **Fixed on this branch.** A "proposed" trick was counted as a trick. Example `bench/deal/ad-06.html` (honest: a no-cost
   "Send me offers" box): "Be careful here", 1 trick. Now only confirmed tricks count; proposed ones go under "Check these".
   Test: `test_real_card_counts_confirmed_tricks_only`.

- **Fixed on this branch.** `backend/tests/content_reader.py` `FAKE_CHROME` threw on current Chromium (`window.chrome` is
  non-configurable), so every browser test failed once Chromium was found. It now falls back to `Object.assign`.
  Test: `test_stub_server.py::test_content_reader_reads_a_page_on_current_chromium` (needs Chromium).
- **Fixed on this branch.** The panel added a row per finding version (`extension/background.js:84`). New
  `upsertFinding()` in `extension/shared/check.js` replaces a row by finding id (check, agent, type, evidence), as
  `world_model.finding_key` does. Test: `test_panel_replaces_a_finding_version_instead_of_adding_a_row` (needs Node).
- **Fixed on this branch.** CORS `allow_origins=["*"]` is now `chrome-extension://<any id>` and `http://localhost` /
  `127.0.0.1` only. Content scripts never call the backend. Test: `test_cors_allows_only_the_extension_and_localhost`.
- **Fixed on this branch.** `conftest.py` pins `AGENT_ORCH` (removed) and `WORLD_MODEL_URL=http://127.0.0.1:1`.
  Test: `test_conftest_pins_the_orchestrator_switch_and_the_sidecar_url`.
- **Fixed on this branch.** With `AGENT_ORCH=on`, chat no longer saw injected text (the Scout guard removes it first):
  `chat.has_injection()` now also reads the `hidden_instructions` finding. Test:
  `test_chat.py::test_page_cleaned_by_the_orchestrator_still_says_ignored`. The fake `resetting_timer` and `drip_fees`
  findings in `test_agent_deal_real_sends_a_verdict_early_and_firms_it_up` now carry evidence; the test runs with
  `AGENT_ORCH` off and on (with on, only the final card is checked: the orchestrator sends a card when an agent finishes,
  not when the fast Deal tools finish).
- **Fixed on this branch.** `scripts/setup.sh` / `setup.ps1` run `npm install --ignore-scripts` in
  `backend/engines/world_model_ts` when Node 20+ is found (otherwise a note; the sidecar is optional).
  `scripts/run.sh` / `run.ps1` start the sidecar on 8788 (unless one already answers) and stop it when the backend stops.
  Tests (text checks only): `test_setup_scripts_install_the_sidecar_with_node_20`, `test_run_scripts_start_and_stop_the_sidecar`.
  **Please run each script once by hand.**
- **Fixed on this branch.** `test_demo_clock_sends_one_alert_on_day_9` left the demo clock on Day 9 (later checks showed
  $209). It now resets to Day 1. Test: `test_demo_clock_is_back_on_day_1_after_the_clock_test`.
- **Fixed on this branch.** `docs/HANDOFF_ABEL.md`: test counts, Zetaris "Live" only with `DATA_SOURCE=zetaris` and
  `PRICISE_PG_*`, orchestrator Price item done.
- **Merging this branch:** use a merge commit (not squash), so the history keeps the separate steps.
- **Open:** Raj's prompt log folder (`docs/prompts_log/raj.md`): which folder is the agreed one?

## Emmanuel

- **Fixed on this branch.** `EVIDENCE["timer_frames"]` (from `timer_test.check_timer`, also used by `deal.rerun()`) now
  carries `at` and `remaining_s`, so `skeptic.visits_from_frames()` can verify a timer challenge. Test:
  `test_deal.py::test_timer_frames_carry_at_and_remaining_s_for_the_skeptic`.
- **Fixed on this branch.** A page signal with no selector gave a finding with no evidence (a real-page `resetting_timer`
  was then rejected by Skeptic rule 1). `timer_test.passthrough` and `deal.check_scarcity` now add a `log` evidence item.
  Test: `test_signal_without_a_selector_still_gives_evidence` (validated against `contracts.Finding`).
- **Fixed on this branch.** `browser.cached_chromium()` returns `PLAYWRIGHT_CHROMIUM_PATH` when that file exists and also
  searches `%LOCALAPPDATA%\ms-playwright` on Windows. Test: `test_cached_chromium_honors_the_env_path_and_searches_windows`.
- **Open (report only, not changed):** `tools/runahead.py` `visible_fees()` (lines ~314-316) emits a **confirmed**
  `drip_fees` finding with **no evidence** when the fee signals have no selector. Add a `log` evidence item as above.
- **Open:** the run-ahead's checkout steps will need a token later (a real checkout would refuse a form post without one).

## Raj

- **Fixed on this branch.** `trust.py` wrote every finding to `world_model_stub` (a second store nothing reads; the
  orchestrator already records Trust findings in the World Model). The import, `_write_all` and its two calls are gone.
  Tests: `test_trust.py::test_findings_follow_the_contract_and_reach_the_world_model`, `test_rerun_does_not_duplicate_findings`.
- **Fixed on this branch.** `photo_match.phash_bytes` blocked the event loop; it now runs in `asyncio.to_thread`.
  Test: `test_photo_hashing_runs_off_the_event_loop`.
- **Fixed on this branch.** `_reword()` now sends `meterless.trusted_prefix(<fixed instruction>)` +
  `meterless.untrusted(<reasons>)`, so the Scout guard checks only the reasons. Test: `test_reword_marks_the_reasons_as_untrusted`.
- **Open:** the prompt log stays in `docs/prompts_log/raj.md` until Shriganesh confirms the folder.
- **Open:** please confirm that caution stores are blocked from the best price. The Price Agent rejects them with
  `use_trust=True` and the docs say "only safe stores can win"; the Skeptic's rule alone would let caution pass.

## Josh

- **Open: add `variant` and `warranty_months` to the listings data.** Not done here: it needs `catalog.py`,
  `backend/data/schema.sql` (seed inserts every listing key, and the table has no such columns), the `listings` SQL in
  `repo.py`, and a reseed. The facts are on the Store Lab pages, not in the catalog:
  - colour "Black": AudioDealz, Store A, Store C, Store G, Store I (the XM5 listing);
  - warranty: Store A, Store C and Store I "12-month manufacturer warranty"; Store B "90-day seller warranty";
  - Stores D, E, F, H and the Store I XM4 listing state neither: leave them out, don't invent values.
  Until then the adapter writes `variant=1;warranty=1` and the finding says "variant and warranty not in data, assumed equal".
- **Open: look-alike pages and labels.** No look-alike page exists in `storelab/` or `bench/` (Store B is a refurbisher,
  not a look-alike), so no `lookalike_listing` or `clean_control` labels were added. The bench prints
  "n/a (0 labelled pages)" for exact match; the plan expects 7 and 10.
- **Open: Zetaris setup (what is missing on my machine):** how to register the `pricise_pg` datasource (Zetaris answers,
  but `pricise_pg.stores` is "TABLE_OR_VIEW_NOT_FOUND"), how to seed the Pricise Postgres (`PRICISE_PG_*` not set), and
  whether `ZETARIS_ORG_ID` is the right name (sent as `X-Org-ID`). Until then every run uses the fixture.
- **Open (optional):** a mode that emits one `best_price` finding per candidate, in the exact-match ref shape
  (`docs/price_agent_contract.md`). Today `PriceResult.alt` is the winner itself, so a rejected winner means "no runner-up".
- The wiring calls `price._identify` and `repo._catalog_listings`. If you rename them, the contract test in
  `backend/tests/test_price_wiring.py` fails: tell me, or keep a public name.

## Abel (my files, same branch)

- `orchestrator_stub.py` calls `_safe_headline` on every card, after the Trust verdict sets `card.safe` (through the
  lazy `stub_server` import inside the function: no cycle).
- `orchestrator.challengeable()` skips "still checking" placeholders (`model == "pending"`): the timer placeholder has no
  evidence and was marked "rejected". Test: `test_orchestrator.py::test_still_checking_placeholder_is_not_challenged`.
- `skeptic.visits_from_frames` docstring updated (timer frames now carry `at` / `remaining_s`).

## Everyone

- Wrap page or user text that goes into a prompt with `meterless.untrusted()` (`backend/llm/meterless.py:82`).
