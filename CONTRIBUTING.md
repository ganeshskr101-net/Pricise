# Contributing to Pricise

Read [AGENTS.md](AGENTS.md) first: it holds the rules every person and coding agent follows (contracts, secrets, real-site safety).

## Workflow

1. **Branch from `main`**, named `<name>/<feature>`: `raj/trust-domain-age`, `josh/zetaris-history`, `shriganesh/quick-card`.
2. **Keep `main` working.** Anything that isn't finished goes behind a switch that defaults to the stub
   (`AGENT_<NAME>=stub|real`), so the demo never breaks.
3. **Small PRs.** One feature or fix per PR, easy to review in a few minutes. Tag the title with the tool you used:
   `[cursor]`, `[claude-code]` or `[codex]`.
4. **Run the tests before opening a PR:** `scripts/test.sh` (Windows: `scripts\test.ps1`). Add or update tests for what you
   changed (pytest, in `backend/tests/`). GitHub runs the same tests on every pull request.
5. **Fill in the PR template:** what changed, how you tested it, screenshots for anything visible.

## Who reviews what

From the ownership table in AGENTS.md. Ask the owner of the files you touch; if you touch several areas, ask each owner.

| Area | Reviewer |
|---|---|
| `extension/`, `storelab/audiodealz/`, `README.md`, `scripts/`, `docs/` | Shriganesh |
| `backend/agents/trust.py` | Raj |
| `backend/agents/deal.py`, `backend/tools/` | Emmanuel |
| `backend/agents/price.py`, `storelab/` stores A–I, `backend/data/` | Josh |
| `backend/orchestrator.py`, `backend/agents/skeptic.py`, `backend/llm/`, `backend/engines/` | Abel |
| **`backend/contracts.py` or any API shape** (`/check`, the events stream, `/chat`, …) | **Abel must approve** before merge: every agent and the extension depend on it |

## Never commit secrets

- Keys and tokens live only in your own `.env` (git-ignored). `.env.example` has placeholders only: if you add a setting,
  add it there with a one-line comment and a fake value.
- Never print or log keys. If a key ends up in a commit, tell the team and rotate it: deleting the line isn't enough.

## Working on the extension

There is no build step: plain HTML, CSS and ES modules. After any change:

1. `chrome://extensions` → click the reload button on the Pricise card.
2. Refresh the shop tab you're testing.
3. Close and reopen the side panel.

UI rules: colours and sizes from `extension/shared/tokens.css` only, icons from `extension/shared/icons.js`, text at least
12 px, colour only for meaning (red tricks, amber unverified, green good, grey not checked).

## Working on the backend

`scripts/run.sh --reload` restarts the server on every change (`--real` for the real Deal Agent). Every model call goes
through `backend/llm/meterless.py`; every network call has a timeout (max 8 s); the hidden browser only runs on Store Lab
pages and never clicks pay. Price-history data goes through `backend/data/history.py`.
