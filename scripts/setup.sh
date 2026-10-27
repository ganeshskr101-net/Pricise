#!/usr/bin/env bash
# One-time setup (macOS / Linux): virtual env, Python packages, Playwright's Chromium, and .env from .env.example.
# Usage: scripts/setup.sh            (use another Python with: PYTHON=/path/to/python3.11 scripts/setup.sh)
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  echo "Pricise needs Python 3.11 or newer (found: $("$PY" --version 2>&1 || echo none))."
  echo "Install it (e.g. brew install python@3.13), or run: PYTHON=/path/to/python3.11 scripts/setup.sh"
  exit 1
fi

[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r backend/requirements-dev.txt
# The hidden browser for Store Lab checks. Only the real Deal Agent needs it, so a failed download doesn't stop setup.
if ! .venv/bin/python -m playwright install chromium; then
  echo "WARNING: couldn't download Playwright's Chromium. Stub mode works without it; for the real Deal Agent run:"
  echo "         .venv/bin/python -m playwright install chromium"
fi
# The engine sidecar (World Model, Scout, H-MEM, Markovian). Optional: needs Node 20+; the backend works without it.
if command -v node >/dev/null 2>&1 && [ "$(node -p 'process.versions.node.split(".")[0]')" -ge 20 ]; then
  (cd backend/engines/world_model_ts && npm install --ignore-scripts --no-audit --no-fund) \
    || echo "WARNING: npm install failed in backend/engines/world_model_ts; the engine sidecar won't start."
else
  echo "NOTE: Node 20+ not found, so the optional engine sidecar is not installed (https://nodejs.org)."
fi
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example (stub mode, no keys needed)."
fi
echo
echo "Setup done. Start the backend:  scripts/run.sh        (sample mode; full mode: SAMPLE_MODE=false in .env or scripts/run.sh --real)"
echo "Run the tests:                  scripts/test.sh"
