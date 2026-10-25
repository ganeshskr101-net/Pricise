# bench/

Labelled test pages for the accuracy number on the proof screen. Made-up stores only.
**Owners:** Abel + Josh (bench as a whole), Emmanuel (`deal/`).

| Path | What it is |
|---|---|
| `deal/` | 26 one-file pages for the Deal Agent: fake discount (7), resetting timer (6), drip fees (7), pre-ticked add-on (6), each with honest controls |
| `trust/` | 12 made-up stores for the Trust Agent (7 risky, 5 legitimate), each a folder with `about.json`, `returns.html`, `listings.json`, `reviews.json`; regenerate with `trust/make_fixtures.py` |
| `run_trust.py` | Vets every `trust/` store and the Store Lab demo stores, prints accuracy and vet time; details go to `bench/out/trust.csv`. No browser needed, a few seconds |
| `labels.csv` | `page, trick, expected (1 = trick present), note` for every bench page |
| `run_deal.py` | Runs the Deal Agent on every `deal/` page and prints accuracy per trick; details go to `bench/out/deal.csv` (not committed) |
| `out/` | Generated results (git-ignored) |

**Run it:** `.venv/bin/python bench/run_deal.py` (Windows: `.venv\Scripts\python.exe bench\run_deal.py`). Takes about a minute
and needs Playwright's Chromium (`scripts/setup.sh` installs it). The served copy is at `http://localhost:8787/bench/deal/`.
Fake-discount pages read their price history from `backend/data/fixtures/price_history.json`.

## Full bench (run_bench.py)

`run_bench.py` runs every page in `labels.csv` through the real orchestrator path (`AGENT_ORCH=on`, `AGENT_DEAL=real`,
`AGENT_TRUST=real`, World Model sidecar optional), one page at a time, and scores it against the labels.

```
python bench/run_bench.py            # full mode: hidden-browser run-ahead and timer test on, model calls allowed
python bench/run_bench.py --sample   # sample mode: Chromium only reads the pages; no run-ahead, timer test or model calls
```

- **Caught** = a finding of the labelled type is `confirmed` and has evidence. Today the Skeptic only checks evidence (no rechecker).
- **Trick detection** counts planted pages (`expected=1`) only; honest controls (`expected=0`) are scored in false alarms.
  Every labelled page is either scored in one metric or listed as excluded with its reason ("Page accounting" in the
  output, `accounting` in `results.json`).
- **False alarm** = a confirmed `fake_discount`, `resetting_timer`, `drip_fees` or `prechecked_addon` finding on a clean page
  (`expected=0` rows plus any `clean_control` rows). Trust and `not_connected` findings do not count.
- **Trust** (`risky_store`; `scam_store` is accepted as an alias): Trust findings are never `confirmed`, so the card decides.
  Scam detection = risky stores (`expected=1`) whose `card.safe` is caution or bad (unsafe); **Trust false alarms** = legitimate
  stores (`expected=0`) flagged the same way. Trust needs no browser, so both modes score it. A store whose verdict disagrees
  with its label is listed under "Trust mismatches" (and `trust_mismatches` in `results.json`) for Raj to decide.
- **n/a**: a type whose agent isn't connected (look-alike listing), or that sample mode turns off (resetting timer,
  drip fees), shows as `n/a (reason)` with its page count. It is never scored as 0.
- **Time** = page load (`page.goto`) to the last final card; read and check time are shown separately. Timer pages take
  longer in full mode (3 visits `TIMER_GAP_S` apart; default 3 s here).
- Output: a table on screen and `bench/results.json` (`--out` to change): mode, date, per-type `k/n` rates, false alarms,
  timing, the n/a types, and for every page each final finding's status and evidence count plus the card's tone and tricks.
  Traces go to `bench/out/traces/`. Targets are printed for reference only.
- Chromium comes from `PLAYWRIGHT_CHROMIUM_PATH` if set, else Playwright's own. If it can't start, the run stops and scores nothing.
