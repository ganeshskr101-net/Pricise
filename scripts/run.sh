#!/usr/bin/env bash
# Starts the Pricise backend on http://localhost:8787 (the port the extension calls).
# Usage: scripts/run.sh [--real | --stub] [--reload]
#   SAMPLE_MODE=true (in .env or the shell; the .env.example default): sample mode, every agent stub, fixture data, no
#             Zetaris / Nightshift / SerpApi calls. Set SAMPLE_MODE=false in .env for full mode.
#   (full mode) everything real: AGENT_DEAL/TRUST/PRICE=real, AGENT_ORCH=on, DATA_SOURCE=zetaris (falls back to the fixture
#             data if Zetaris doesn't answer). A switch already set in your shell wins; these defaults beat .env.
#   --real    full mode even when .env says SAMPLE_MODE=true
#   --stub    the scripted demo: every agent stub, AGENT_ORCH=off, DATA_SOURCE=fixture
#   --reload  restart automatically when backend code changes
set -euo pipefail
cd "$(dirname "$0")/.."

EXTRA=()
SAMPLE="${SAMPLE_MODE:-$(grep -E '^SAMPLE_MODE=' .env 2>/dev/null | tail -1 | cut -d= -f2 | tr -d '"'"'"' ')}"
case "$(echo "$SAMPLE" | tr 'A-Z' 'a-z')" in true|1|yes|on) MODE=sample ;; *) MODE=real ;; esac
for arg in "$@"; do
  case "$arg" in
    --real) MODE=real; export SAMPLE_MODE=false ;;
    --stub) MODE=stub ;;
    --reload) EXTRA+=(--reload) ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg (try --help)"; exit 2 ;;
  esac
done
[ -x .venv/bin/python ] || { echo "Run scripts/setup.sh first."; exit 1; }
if [ "$MODE" = sample ]; then
  export SAMPLE_MODE=true AGENT_DEAL=stub AGENT_TRUST=stub AGENT_PRICE=stub AGENT_ORCH=off DATA_SOURCE=fixture
elif [ "$MODE" = stub ]; then
  export AGENT_DEAL=stub AGENT_TRUST=stub AGENT_PRICE=stub AGENT_ORCH=off DATA_SOURCE=fixture
else
  export AGENT_DEAL="${AGENT_DEAL:-real}" AGENT_TRUST="${AGENT_TRUST:-real}" AGENT_PRICE="${AGENT_PRICE:-real}" \
         AGENT_ORCH="${AGENT_ORCH:-on}" DATA_SOURCE="${DATA_SOURCE:-zetaris}"
fi

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

echo "Pricise backend on http://localhost:8787  ($MODE mode)"
echo "  AGENT_DEAL=$AGENT_DEAL AGENT_TRUST=$AGENT_TRUST AGENT_PRICE=$AGENT_PRICE AGENT_ORCH=$AGENT_ORCH DATA_SOURCE=$DATA_SOURCE"
echo "  (check: curl -s localhost:8787/health)"
.venv/bin/python -m uvicorn backend.stub_server:app --port 8787 ${EXTRA[@]+"${EXTRA[@]}"}
