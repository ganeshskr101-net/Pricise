#!/usr/bin/env bash
# Starts the Pricise backend on http://localhost:8787 (the port the extension calls).
# Usage: scripts/run.sh [--real | --stub] [--reload]
#   --real    AGENT_DEAL=real: the real Deal Agent (hidden browser on Store Lab pages)
#   --stub    AGENT_DEAL=stub: the scripted demo (the default when .env doesn't say otherwise)
#   --reload  restart automatically when backend code changes
set -euo pipefail
cd "$(dirname "$0")/.."

EXTRA=()
for arg in "$@"; do
  case "$arg" in
    --real) export AGENT_DEAL=real ;;
    --stub) export AGENT_DEAL=stub ;;
    --reload) EXTRA+=(--reload) ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg (try --help)"; exit 2 ;;
  esac
done
[ -x .venv/bin/python ] || { echo "Run scripts/setup.sh first."; exit 1; }

echo "Pricise backend on http://localhost:8787  (AGENT_DEAL=${AGENT_DEAL:-from .env, default stub})"
exec .venv/bin/python -m uvicorn backend.stub_server:app --port 8787 ${EXTRA[@]+"${EXTRA[@]}"}
