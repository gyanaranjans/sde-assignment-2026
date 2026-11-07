"""One screen per workflow for a client supervisor: what's being tested, what's winning, what we need from
you, what changed today. Rendered from the controller's own store.

    python -m controller.view --db state/controller.db
"""
from __future__ import annotations

import argparse
import json

from .store import Store


def _fmt(minute):
    d, m = divmod(minute, 1440)
    return f"D{d} {m // 60:02d}:{m % 60:02d}"


def render_view(store: Store, now: int) -> str:
    db = store.db
    L = [f"# Supervisor view — {store.get('scenario')} at {_fmt(now)}", ""]
    wfs = [r[0] for r in db.execute("SELECT wf FROM wf_state ORDER BY wf")]
    for wf in wfs:
        st = store.wf_state(wf)
        L.append(f"## {wf}")
        tests = []
        for dim, r in st["dims"].items():
            if r["mode"] == "explore":
                tests.append(f"testing **{dim}** {r['explore_alloc']} (control {r['control']})")
            elif r["mode"] == "promoted":
                w = ", ".join(f"{r['feature']}={v}→{o}" for v, o in r["assign"].items()) or f"none — kept {r['control']}"
                tests.append(f"**{dim}** winners: {w}" + (f"; retired after decay: {r['retired']}" if r["retired"] else ""))
            elif r["mode"] == "hold":
                tests.append(f"**{dim}** held: set by your team, not changed by the controller")
        L.append("- Experiments: " + ("; ".join(tests) or "none"))
        did = st["did"]
        L.append(f"- Caller IDs: {did['weights']}" + (f" — rotated out: {did['flagged']}" if did["flagged"] else ""))
        if st["incident"]:
            i = st["incident"]
            L.append(f"- **Open incident**: hallucination on {i['prompt']} since {_fmt(i['opened'])} — {i['status']}")
        waiting = store.proposals(wf, ["pending", "approved"])
        for p in waiting:
            L.append(f"- **Needs you**: {p['approval_id']} — {p['summary']} (status {p['status']})")
        changes = db.execute("SELECT version, actor, ts, dims FROM config_versions WHERE wf=? AND minute>=? "
                             "ORDER BY version", (wf, now - 1440)).fetchall()
        for v, actor, ts, dims in changes:
            L.append(f"- Changed (last 24h): v{v} by {actor} at {ts[11:16]} D{now // 1440 if ts else ''} — {json.loads(dims)}")
        alerts = db.execute("SELECT ts, summary FROM decisions WHERE wf=? AND kind LIKE 'alert_%' AND minute>=?",
                            (wf, now - 1440)).fetchall()
        for ts, s in alerts:
            L.append(f"- Alert: {s}")
        kp = {}
        for key, n, k in db.execute("SELECT key, n, k FROM counters WHERE key LIKE ?", (f"pk|{wf}|%",)):
            kp.setdefault(int(key.split("|")[2]), [0, 0, 0, 0])[:2] = [n, k]
        for key, n, k in db.execute("SELECT key, n, k FROM counters WHERE key LIKE ?", (f"eg|{wf}|%",)):
            kp.setdefault(int(key.split("|")[2]), [0, 0, 0, 0])[2:] = [n, k]
        days = sorted(d for d in kp if d >= 0)[-3:]
        if days:
            L.append("- KPIs (pickup / engagement) by day: " + ", ".join(
                f"D{d}: {kp[d][1] / max(kp[d][0], 1):.3f} / {kp[d][3] / max(kp[d][2], 1):.3f}" for d in days))
        L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="state/controller.db")
    a = ap.parse_args()
    s = Store(a.db)
    now = s.db.execute("SELECT MAX(minute) FROM loop_metrics").fetchone()[0] or 0
    print(render_view(s, now))
