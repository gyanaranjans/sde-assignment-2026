#!/usr/bin/env bash
# Kill -9 the controller at random moments (N times), restart it each time, let it finish, then verify:
#   * counters == ground-truth event counts (every chunk ingested exactly once)
#   * no approval applied twice, none lost (lapsed/never applied), no duplicate proposal ids
#   * outcome report, for comparison with an uninterrupted run
# usage: scripts/crash_test.sh [scenario] [kills]
set -euo pipefail
SC=${1:-scenarios/public_02_incidents.yaml}; KILLS=${2:-8}; NAME=crash; PORT=8711
PY=${PYTHON:-.venv/bin/python}
rm -rf state/$NAME; mkdir -p reports
$PY -m sim baseline --scenario "$SC" --run-dir runs/base_$NAME >/dev/null
$PY -m sim serve --scenario "$SC" --run-dir runs/mine_$NAME --port $PORT --force > runs/sim_$NAME.log 2>&1 &
SIM=$!; trap 'kill $SIM 2>/dev/null' EXIT
until curl -sf http://127.0.0.1:$PORT/health >/dev/null; do sleep 0.3; done
LOG=reports/crash_recovery.log; : > $LOG
END=$(curl -s http://127.0.0.1:$PORT/sim/state | $PY -c 'import json,sys; print(json.load(sys.stdin)["end_minute"])')
for i in $(seq 1 $KILLS); do
  # kill at a random sim minute in the i-th slice of the run, plus a random sub-second jitter so the kill
  # lands anywhere inside a step (mid-ingest, between proposal write and POST, mid-apply, ...)
  TARGET=$($PY -c "import random; print(int(($i - 1 + random.random()) * $END / $KILLS))")
  CTRL_STATE=state/$NAME ./run.sh http://127.0.0.1:$PORT 2>> runs/ctrl_$NAME.log &
  C=$!
  until [ "$(curl -s http://127.0.0.1:$PORT/sim/state | $PY -c 'import json,sys; print(json.load(sys.stdin)["sim_minute"])')" -ge "$TARGET" ] || ! kill -0 $C 2>/dev/null; do :; done
  sleep "$($PY -c 'import random; print(round(random.uniform(0, 0.05), 3))')"
  if kill -9 $C 2>/dev/null; then
    echo "kill #$i at $(curl -s http://127.0.0.1:$PORT/sim/state | $PY -c 'import json,sys; print(json.load(sys.stdin)["ts"])')" | tee -a $LOG
  fi
  wait $C 2>/dev/null || true
done
CTRL_STATE=state/$NAME ./run.sh http://127.0.0.1:$PORT 2>> runs/ctrl_$NAME.log
echo "final run finished: $(curl -s http://127.0.0.1:$PORT/sim/state)" | tee -a $LOG
$PY scripts/verify_crash.py state/$NAME/controller.db runs/mine_$NAME | tee -a $LOG
$PY -m sim grade --run runs/mine_$NAME --baseline runs/base_$NAME --out reports/crash_recovery_grade.md >/dev/null
sed -n 5,11p reports/crash_recovery_grade.md | tee -a $LOG
