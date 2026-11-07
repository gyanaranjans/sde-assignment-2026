#!/usr/bin/env bash
# scripts/run_scenario.sh <scenario.yaml> <name> [port]  — fresh sim + fresh controller state, then grade.
set -euo pipefail
SC=$1; NAME=$2; PORT=${3:-8700}
PY=${PYTHON:-.venv/bin/python}
rm -rf "state/$NAME"
$PY -m sim baseline --scenario "$SC" --run-dir "runs/base_$NAME" >/dev/null
$PY -m sim serve --scenario "$SC" --run-dir "runs/mine_$NAME" --port "$PORT" --force > "runs/sim_$NAME.log" 2>&1 &
SIM=$!; trap 'kill $SIM 2>/dev/null' EXIT
until curl -sf "http://127.0.0.1:$PORT/health" >/dev/null; do sleep 0.5; done
CTRL_STATE="state/$NAME" ./run.sh "http://127.0.0.1:$PORT" 2> "runs/ctrl_$NAME.log"
mkdir -p reports
$PY -m sim grade --run "runs/mine_$NAME" --baseline "runs/base_$NAME" --out "reports/$NAME.md" >/dev/null
echo "graded → reports/$NAME.md"
