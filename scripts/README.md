# scripts/

One-command setup, run and test, for macOS/Linux (`.sh`) and Windows PowerShell (`.ps1`). **Owner:** Shriganesh.

| Script | What it does |
|---|---|
| `setup.sh` / `setup.ps1` | Checks Python 3.11+, creates `.venv`, installs `backend/requirements-dev.txt`, installs Playwright's Chromium, copies `.env.example` to `.env` if missing |
| `run.sh` / `run.ps1` | Starts the backend on http://localhost:8787. `--real` / `-Real` = real Deal Agent, `--reload` / `-Reload` = restart on code changes |
| `test.sh` / `test.ps1` | Runs pytest; extra arguments are passed through (e.g. `-k chat -x`) |

On Windows, run them as `powershell -ExecutionPolicy Bypass -File scripts\setup.ps1` (the default policy blocks local scripts).
