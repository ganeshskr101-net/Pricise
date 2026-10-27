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

# The engine sidecar on 127.0.0.1:8788 (same as: cd backend/engines/world_model_ts && npx tsx server.ts), started here
# and stopped when the backend stops. Optional: without Node 20+ the backend works on its own.
SIDECAR_PID=""
TSX=backend/engines/world_model_ts/node_modules/tsx/dist/cli.mjs
if curl -s -m 1 http://127.0.0.1:8788/health >/dev/null 2>&1; then
  echo "Engine sidecar already running on http://127.0.0.1:8788"
elif command -v node >/dev/null 2>&1 && [ -f "$TSX" ] && [ "$(node -p 'process.versions.node.split(".")[0]')" -ge 20 ]; then
  # Its own process group (job control on for this one launch): tsx runs server.ts in a CHILD node process, so killing
  # only the tsx process left the server running. The trap stops the whole group.
  set -m
  (cd backend/engines/world_model_ts && exec node node_modules/tsx/dist/cli.mjs server.ts) &
  SIDECAR_PID=$!
  set +m
  trap 'kill -TERM -- "-$SIDECAR_PID" 2>/dev/null' EXIT
  echo "Engine sidecar on http://127.0.0.1:8788 (stops with the backend)"
else
  echo "Engine sidecar not started (needs Node 20+ and scripts/setup.sh); the backend works without it."
fi

echo "Pricise backend on http://localhost:8787  (AGENT_DEAL=${AGENT_DEAL:-from .env, default stub})"
.venv/bin/python -m uvicorn backend.stub_server:app --port 8787 ${EXTRA[@]+"${EXTRA[@]}"}
