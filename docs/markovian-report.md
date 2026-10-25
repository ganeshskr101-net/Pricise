# Markovian in Pricise: Phase 1A report

The wishlist watch, Pricise's one long-running job, built as bounded steps on the Meterless Markovian reference engine
inside the existing engine sidecar. Phase 1A = the watch job, the engine, the sidecar routes, the Python client, tests
and a measured run. Nothing in the product calls the watch yet: wiring it into `/demo/clock` and `/wishlist` is
Phase 1B (owner notes at the end).

## Read this first

- **There was no long-running agent loop to refactor.** Before this work, `POST /demo/clock` was a stateless script
  (day 9 or later: Sony becomes $209 and one alert is sent), with its state in memory. The orchestrator, chat and the
  bench are short or batch jobs. So the watch loop was **built**, not refactored.
- **The watch's decisions are rule-based and need no model.** No model was called anywhere in this work.
- **The measurement compares prompt sizes a model-in-the-loop version would receive.** The "before" is a naive baseline
  written for this measurement (it is not code that exists in Pricise), run on the same chunks with the same decisions.
  The numbers are **not a production saving**: today nothing is sent to a model.
- **All numbers are estimated (chars/4)**, the reference engine's own estimator, not provider-measured tokens.
- **Prices are made-up Store Lab fixtures** (`bench/watch/prices.json`): not real stores, not real prices.
- The reference's own demo figure (from its `measured-run.ts`) is not our result and is not quoted here.
- Conformance proves the engine follows the spec's exact contracts. It proves nothing about the watch: the watch's own
  tests do that.

## Measured run

One command (about a second; needs Node and `backend/engines/world_model_ts/node_modules`). It starts its own sidecar
on a free port, runs the 30-day watch for the 4 demo wishlist items in a temp folder, stops the sidecar, prints the
table and saves `bench/out/watch.json` (git-ignored):

```bash
python bench/run_watch.py
```

- after (Markovian): the prompt the vendored engine built for each day's chunk (goal + bounded carryover), as returned
  by the sidecar.
- before (naive): for day d, the goal plus the full text of the chunks of days 1 to d-1, then "Continue with day d".
- Both columns come from the same run. Re-running gives the same numbers (the watch, the feed and the estimator are
  deterministic).

Output of the run on 2026-10-25 (Windows 11, Node v24.11.1, Python 3.14.0):

```
Wishlist watch, 30 days, 4 items, made-up Store Lab prices. Prompt size per day, estimated (chars/4).
before = naive baseline written for this measurement (full history each day); after = Markovian engine.
Same run, same decisions; no model was called.

day | naive prompt | markovian prompt | naive cum. | markovian cum. | ratio (cum.)
----|--------------|------------------|------------|----------------|-------------
  1 |           34 |              178 |         34 |            178 |       0.19x
  2 |          112 |              187 |        146 |            365 |       0.40x
  3 |          191 |              187 |        337 |            552 |       0.61x
  4 |          270 |              187 |        607 |            739 |       0.82x
  5 |          349 |              187 |        956 |            926 |       1.03x
  6 |          428 |              187 |       1384 |           1113 |       1.24x
  7 |          507 |              187 |       1891 |           1300 |       1.45x
  8 |          586 |              187 |       2477 |           1487 |       1.67x
  9 |          665 |              182 |       3142 |           1669 |       1.88x
 10 |          749 |              191 |       3891 |           1860 |       2.09x
 11 |          834 |              192 |       4725 |           2052 |       2.30x
 12 |          918 |              192 |       5643 |           2244 |       2.51x
 13 |         1002 |              192 |       6645 |           2436 |       2.73x
 14 |         1086 |              192 |       7731 |           2628 |       2.94x
 15 |         1171 |              192 |       8902 |           2820 |       3.16x
 16 |         1255 |              192 |      10157 |           3012 |       3.37x
 17 |         1339 |              187 |      11496 |           3199 |       3.59x
 18 |         1424 |              192 |      12920 |           3391 |       3.81x
 19 |         1508 |              192 |      14428 |           3583 |       4.03x
 20 |         1601 |              200 |      16029 |           3783 |       4.24x
 21 |         1694 |              200 |      17723 |           3983 |       4.45x
 22 |         1787 |              200 |      19510 |           4183 |       4.66x
 23 |         1880 |              200 |      21390 |           4383 |       4.88x
 24 |         1982 |              209 |      23372 |           4592 |       5.09x
 25 |         2084 |              205 |      25456 |           4797 |       5.31x
 26 |         2186 |              209 |      27642 |           5006 |       5.52x
 27 |         2288 |              209 |      29930 |           5215 |       5.74x
 28 |         2390 |              210 |      32320 |           5425 |       5.96x
 29 |         2493 |              210 |      34813 |           5635 |       6.18x
 30 |         2595 |              210 |      37408 |           5845 |       6.40x

totals (estimated (chars/4)): naive 37408, markovian 5845, difference 31563 (84.4% of naive)
flat check (estimated (chars/4)): markovian prompt per day min 178, max 210; naive prompt per day min 34, max 2595
chains: 4 (8, 8, 8, 6 chunks; status max-chunks, max-chunks, max-chunks, completed)
alerts: sony day 9 at 209.00, fryer day 19 at 79.00, kindle day 23 at 119.00

Saved bench/out/watch.json
```

What it shows: the naive prompt grows every day (34 to 2595 estimated tokens), so its cumulative cost grows
quadratically; the Markovian prompt stays between 178 and 210 estimated tokens per day. It grows a little only when
items get their first price or an alert is sent, because its size depends on the number of items and alerts, not on the
number of days. Day 1 is larger for Markovian because the engine's first-chunk template is longer than a naive prompt
with no history yet.

## Conformance result

Engine: `backend/engines/world_model_ts/markovian/src/` = Meterless `engines/markovian/reference/src/` at commit
`1742dec` (2026-10-19), 9 files, byte-identical, Apache-2.0. `markovian/index.ts` only re-exports it.
Run on 2026-10-25 with Node v24.11.1, Windows 11.

```
Markovian conformance: implementation = <repo>/backend/engines/world_model_ts/markovian/index.ts

  PASS eff-001 efficiency model reproduces the documented worked example: N=20 -> 86% (within 0.5%)
  PASS snap-001 run records carry a mandatory chunkConfig snapshot
  PASS snap-002 historical efficiency is computed against the run's own snapshot, not current config
  PASS ovl-001 overlap contract: implement [OVERLAP] or reject non-zero; accept-and-ignore fails
  PASS lab-001 token numbers are labeled: runs carry estimated or measured
  PASS ref-001 reflection failure does not fail the run
  PASS mrk-001 markers are cleaned from display content and drive completion
  PASS cfg-001 config bounds reject out-of-range values with a typed error, never silent truncation

  8/8 checks passed
```

Exit code: 0.

### Run it yourself (Windows)

The suite prints only to the terminal; this section is its saved output. It needs the Meterless clone for `runner.ts`
and the sidecar's own `tsx` (no install in the clone needed). Use forward slashes and an absolute path.

Git Bash:

```bash
cd "<meterless-clone>/engines/markovian/conformance"
MARKOVIAN_IMPL="<repo>/backend/engines/world_model_ts/markovian/index.ts" \
  npx --prefix "<repo>/backend/engines/world_model_ts" tsx runner.ts
```

PowerShell:

```powershell
cd "<meterless-clone>\engines\markovian\conformance"
$env:MARKOVIAN_IMPL = "<repo>/backend/engines/world_model_ts/markovian/index.ts"
npx --prefix "<repo>\backend\engines\world_model_ts" tsx runner.ts
```

## Design

| Piece | File | What it does |
|---|---|---|
| Watch job | `backend/watch.py` | Rules, checkpoint, catch-up, alerts, `reset()`, `status()` |
| Price feed | `bench/watch/prices.json` | Made-up Store Lab prices, 4 items x 30 days |
| Engine | `world_model_ts/markovian/src/*.ts` | Meterless reference, unchanged |
| Bridge | `world_model_ts/markovian/chains.ts` | The engine's generator waits for the next chunk from Python |
| Routes | `world_model_ts/server.ts` | `POST /markovian/chains`, `POST /markovian/chains/{id}/chunks`, `GET` and `DELETE /markovian/chains/{id}` |
| Python client | `backend/engines/markovian.py` | `SidecarChain`: chunks on the engine, local when the sidecar is down |
| Measured run | `bench/run_watch.py` | The table above |

**Who owns what.** Python owns the checkpoints and the decisions; the engine only runs the bounded chain (prompt =
goal + carryover, at most 8 chunks per chain, marker handling, token estimates). So the engine can be down without any
effect on alerts or checkpoints.

**The watch.**
- An offer counts when the item has a target (no target = watched, never alerted), the price is at or under it, the
  offer is not held by Pricise, "new only" needs condition `new`, and "trusted only" needs `store_trust: safe`.
- A drop is real after 2 counting checks in a row (a one-day glitch never alerts).
- `advance_to(day)` processes every missed day, one day per chunk, at most 8 chunks per chain; a 30-day gap is 4 chains
  (8, 8, 8, 6). The checkpoint is saved after every chunk, so a restart continues from the last saved day. A day at or
  before the last processed day does nothing.
- One alert per item, at-most-once: the alert is saved as sent before `advance_to()` returns it.
- Editing an item's rule (target or flags) through `sync_items()` resets that item's alert state: if the price still
  qualifies under the new rule, it can alert again (once per rule).
- `reset()` clears the checkpoint, for demo retakes (the next run alerts again, by design).

**Chunks.** Each chunk is `Day d: N items checked, K new alerts.`, then `[STATE_CHECKPOINT]` and the canonical block
(`COMPLETED / REMAINING / DECISIONS / CONTEXT`) rendered from checkpoint facts; the final chunk ends with
`[TASK_COMPLETE]`. The engine takes its next carryover from the text after the marker.

**Engine settings.** `chunkSize` 3000, `maxChunks` 8, `carryoverTokens` 512, `overlapTokens` 0. No `historyDir` (it
would write every chunk's content to disk), no `compressorFn` (a model call), `reflect: false`. A missing price or store
is written as `none` in the carryover, not `-`: the engine strips runs such as `-@-` from a carryover it accepts, and
`none` keeps the watch's text exactly as sent.

## Privacy and safety rules

| Rule | Enforcement | Proved by |
|---|---|---|
| Checkpoints hold facts only | Strict schema on load and before every save: numbers, booleans, ids, enum values; key-like ids refused; a bad checkpoint raises and is left unchanged (never silently reset) | `test_every_checkpoint_string_is_an_id_or_an_allowed_value`, `test_a_tampered_checkpoint_is_refused_loudly_and_left_alone` |
| Page text never enters | Each feed observation is validated; failures are dropped and counted, never stored | `test_untrusted_feed_values_never_reach_the_checkpoint` (9 cases) |
| The rule text is never stored | Only the target and two flags are kept | `test_rule_text_is_parsed_and_never_stored` |
| Chunks are facts only | Built from the checkpoint; the sidecar also refuses text outside a small character set and text the Scout guard flags | `test_only_checkpoint_facts_are_accepted` |
| Local, git-ignored, atomic | `.pricise-data/watch/checkpoint.json`; temp file, fsync, replace; a failed write keeps the old file | `test_checkpoint_folder_is_git_ignored`, `test_a_failed_write_keeps_the_previous_checkpoint` |
| No alert without a rule | No target, no alert | `test_no_watch_rule_means_no_alert` |
| No duplicate alert | Saved as sent before it is returned; going back or resuming never re-sends | `test_an_alert_saved_before_a_crash_is_never_sent_again`, `test_going_back_in_time_never_resends` |
| Sidecar safeguards | Loopback Host, JSON required, Origin refused, field allowlist, 64 KB, no CORS, no body logs; at most 4 open chains; 60 s per chunk | `test_markovian_routes_have_the_sidecar_safeguards`, `test_open_chains_are_limited_and_can_be_closed`, `test_a_chain_that_gets_no_chunk_times_out` |
| Sidecar down loses nothing | `SidecarChain.chunk()` never raises; the rest of the chain runs locally; one warning per outage; 30 s back-off | `test_sidecar_off_gives_the_same_alerts_and_checkpoints_with_one_warning`, `test_losing_the_sidecar_mid_run_loses_nothing`, `test_sidecar_down_costs_one_connection_attempt_for_a_whole_catch_up` |

**Sidecar off vs on (measured once with the final settings, 30-day demo run, Windows 11):** off (loopback, nothing
listening) 2.09 s with 1 HTTP attempt, all 30 chunks local; on 0.54 s with 34 HTTP attempts (4 chain opens + 30
chunks), all 30 chunks on the engine.
The off time is almost all the one refused connection (about 2 s on Windows); the back-off skips the sidecar after that.
Alerts and checkpoint bytes are identical in both (tested).

## Limits

- **At-most-once alerts:** a crash after the alert is saved but before it is returned loses that one notification. The
  item still shows the alert as sent.
- **Carryover budget:** the engine keeps the watch's carryover exactly only while it fits `carryoverTokens` = 512
  estimated tokens. Measured worst case (every item with a 4-digit target and price, both flags and an alert sent):
  20 items = 502 estimated tokens, kept exactly; 21 items = 527, replaced by the engine's own heuristic or truncated
  carryover. `test_twenty_items_fit_the_engine_carryover_with_no_heuristic_fallback` checks both. Above 20 items the
  watch's decisions and checkpoints are still not affected (Python owns them); only the engine's prompts change. To go
  further, raise `carryoverTokens` again (and keep `chunkSize` >= 400 + carryoverTokens + 1200) in
  `markovian/chains.ts`. With 256 (the first setting) only 10 such items fitted.
- **Chains live in memory:** a sidecar restart drops open chains; the next `advance_to()` starts a new chain from the
  checkpoint.
- The engine's first-chunk template is its generic "long build task" text, not a wishlist prompt.
- Token numbers are estimates (chars/4), not provider-measured.
- Prices are made-up fixtures; real daily prices need the Price Agent.

## Test results (2026-10-25, final settings)

| Run | Result |
|---|---|
| `backend/tests/test_watch.py` (no sidecar) | 30 passed |
| `backend/tests/test_markovian_sidecar.py` (real sidecars on free ports) | 16 passed |
| Full suite, nothing on 127.0.0.1:8788 | 372 passed, 2 skipped |
| Full suite, new sidecar running on 127.0.0.1:8788 | 372 passed, 2 skipped |

The 2 skips are the hidden-browser Deal tests (Playwright Chromium not installed for pytest on this machine).

## Notes for other owners (Phase 1B, not started)

- **`/demo/clock` (`stub_server.py`):** call `watch.sync_items(<the wishlist as [{id, target, rule}]>)` and then
  `watch.advance_to(day)`, and return `result.alerts` (structured: item, day, price, store, target; build the message
  text from these). Remove today's scripted Sony branch. Moving the clock backwards no longer re-arms an alert.
- **Reset for demo retakes:** a new endpoint (for example `POST /demo/reset`) that calls `watch.reset()`. Today's demo
  re-arms the Sony alert when the day goes below 9; with the watch, a retake needs this explicit reset.
- **`GET /wishlist`:** read each item's last price, store and alert state from `watch.status()` instead of the
  hard-coded values.
- **`POST /wishlist` and rule edits:** call `watch.sync_items()` after any change. Editing an item's rule counts as a
  new watch for that item: it can alert again if the price still qualifies under the new rule. An item with no target
  is watched but never alerts.
- **Extension:** show "caught up N days" after a clock jump (`len(result.days)`), and use the alert `id`
  (`<item>-d<day>`) as the notification id.
- **Price Agent (Josh):** real daily prices in the feed shape `{price, store, condition, store_trust, held}` in place of
  the made-up fixture.
- **Trust Agent (Raj):** `store_trust` (`safe`, `caution`, `unsafe`, `unknown`) from the Trust verdict, so "trusted
  only" rules use it; `held` from Pricise's own findings (fake discount, unsafe store).
