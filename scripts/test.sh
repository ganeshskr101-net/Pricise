#!/usr/bin/env bash
# Runs the backend tests. Extra arguments go to pytest, e.g. scripts/test.sh -k chat -x
# Tests that need the hidden browser skip themselves if Chromium isn't installed.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -x .venv/bin/python ] || { echo "Run scripts/setup.sh first."; exit 1; }
exec .venv/bin/python -m pytest backend/tests "$@"
