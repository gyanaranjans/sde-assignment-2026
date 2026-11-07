"""Post-hoc verification for the crash test. Reads the simulator's run dir — allowed here because this is
a test harness, not the controller."""
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

db, run = sqlite3.connect(sys.argv[1]), Path(sys.argv[2])
truth = Counter()
for sub in ("history", "events"):
    for f in sorted((run / sub).glob("*.jsonl")):
        for line in f.open():
            ev = json.loads(line)
            if ev["type"] == "call":
                truth[ev["workflow_id"]] += 1
ours = Counter()
for key, n, _ in db.execute("SELECT key, n, k FROM counters WHERE key LIKE 'pk|%'"):
    ours[key.split("|")[1]] += n
log = [json.loads(l) for l in (run / "sim_log.jsonl").open()]
kinds = Counter(r["kind"] for r in log)
applied = Counter(r["approval_id"] for r in log if r["kind"] == "approval_applied")
approved = {r["approval_id"] for r in log if r["kind"] == "approval_decided" and r["status"] == "approved"}
stale = {r["approval_id"] for r in log if r["kind"] == "apply_conflict"}  # base moved (supervisor edit): legit
pids = Counter(p for (p,) in db.execute("SELECT proposal_id FROM proposals"))
restarts = db.execute("SELECT COUNT(*) FROM decisions WHERE kind='controller_restart'").fetchone()[0]
checks = {
    "dials counted exactly once (ours == truth)": dict(ours) == dict(truth),
    "no approval applied twice": all(v == 1 for v in applied.values()),
    "every approved request applied or provably stale (none lapsed/lost)":
        approved <= set(applied) | stale and kinds["approval_lapsed"] == 0,
    "no duplicate proposal ids": all(v == 1 for v in pids.values()),
}
print(json.dumps({"restarts_recorded": restarts, "dials_truth": dict(truth), "dials_ours": dict(ours),
                  "approved": len(approved), "applied": sum(applied.values()),
                  "stale_apply_conflicts": kinds["apply_conflict"], "checks": checks}, indent=2))
sys.exit(0 if all(checks.values()) else 1)
