#!/usr/bin/env bash
# Starts the controller against a running simulator and blocks until the scenario finishes.
# State (SQLite) lives in $CTRL_STATE (default ./state); re-running resumes from it, so a crash/restart
# is just "run this again". Python 3.11+ with requirements.txt installed; no other services.
set -euo pipefail
SIM_URL="${1:?usage: ./run.sh <SIM_URL>}"
cd "$(dirname "$0")"
PY="${PYTHON:-$( [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3 )}"
exec "$PY" -m controller "$SIM_URL" --state "${CTRL_STATE:-state}"
