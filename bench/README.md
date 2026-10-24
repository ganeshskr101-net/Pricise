# bench/

Labelled test pages for the accuracy number on the proof screen. Made-up stores only.
**Owners:** Abel + Josh (bench as a whole), Emmanuel (`deal/`).

| Path | What it is |
|---|---|
| `deal/` | 26 one-file pages for the Deal Agent: fake discount (7), resetting timer (6), drip fees (7), pre-ticked add-on (6), each with honest controls |
| `labels.csv` | `page, trick, expected (1 = trick present), note` for every bench page |
| `run_deal.py` | Runs the Deal Agent on every `deal/` page and prints accuracy per trick; details go to `bench/out/deal.csv` (not committed) |
| `out/` | Generated results (git-ignored) |

**Run it:** `.venv/bin/python bench/run_deal.py` (Windows: `.venv\Scripts\python.exe bench\run_deal.py`). Takes about a minute
and needs Playwright's Chromium (`scripts/setup.sh` installs it). The served copy is at `http://localhost:8787/bench/deal/`.
Fake-discount pages read their price history from `backend/data/fixtures/price_history.json`.
