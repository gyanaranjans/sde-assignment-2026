"""Audit queries over the controller's own store (never the simulator's logs).

  python -m controller.audit --db state/X/controller.db why nw_renewal "D3 14:00"
  python -m controller.audit --db ... heard nw_renewal nw_renewal:p0004567 "D3 14:00"
  python -m controller.audit --db ... timeline ks_cancel
  python -m controller.audit --db ... health
"""
from __future__ import annotations

import argparse
import json

from .configs import cohort_of, dist
from .ingest import FEATS
from .store import Store


def parse_t(s: str) -> int:
    d, hm = s.split()
    h, m = hm.split(":")
    return int(d.lstrip("Dd")) * 1440 + int(h) * 60 + int(m)


def fmt(m):
    d, r = divmod(m, 1440)
    return f"D{d} {r // 60:02d}:{r % 60:02d}"


def _kpis(store, wf, day):
    out = {}
    for t in ("pk", "eg"):
        r = store.db.execute("SELECT n, k FROM counters WHERE key=?", (f"{t}|{wf}|{day}",)).fetchone()
        out[t] = r or (0, 0)
    pk, eg = out["pk"], out["eg"]
    return {"day": f"D{day}", "dials": pk[0], "pickup": round(pk[1] / pk[0], 4) if pk[0] else None,
            "engagement": round(eg[1] / eg[0], 4) if eg[0] else None}


def version_at(store, wf, t):
    return store.db.execute("SELECT version, actor, ts, minute, dims, proposal_id, approval_id FROM config_versions "
                            "WHERE wf=? AND minute<=? ORDER BY version DESC LIMIT 1", (wf, t)).fetchone()


def why(store, wf, t):
    v = version_at(store, wf, t)
    if not v:
        return {"answer": "business default config (no change before this time)"}
    version, actor, ts, minute, dims, pid, aid = v
    out = {"live_version": version, "changed_at": fmt(minute), "actor": actor, "changed_dims": json.loads(dims)}
    if actor == "supervisor":
        out["why"] = "client supervisor edit (bypasses governance); controller recorded it and stopped managing these dims"
    if pid:
        p = store.db.execute("SELECT * FROM proposals WHERE proposal_id=?", (pid,)).fetchone()
        cols = [c[0] for c in store.db.execute("SELECT * FROM proposals LIMIT 0").description]
        p = dict(zip(cols, p))
        req = json.loads(p["request"])
        out["proposal"] = {"proposal_id": pid, "path": p["path"], "summary": p["summary"],
                           "proposed_at": fmt(p["created_min"]), "approval_id": aid,
                           "impact_score": req.get("impact_score"), "affected_leads": req.get("affected_leads"),
                           "dry_run": req.get("dry_run"), "rationale": req.get("rationale"), "diff": req.get("diff")}
        dec = store.db.execute("SELECT ts, kind, summary, detail FROM decisions WHERE wf=? AND kind LIKE 'approval_%' "
                               "AND detail LIKE ?", (wf, f'%{pid}%')).fetchall()
        for _, kind, s, d in dec:
            if kind in ("approval_approved", "approval_rejected", "approval_expired"):
                a = json.loads(d).get("approval", {})
                out["approval"] = {"status": a.get("status"), "decided_at": a.get("decided_at"),
                                   "approver": f"{wf} client approver (governance policy)"}
        ids = (req.get("evidence") or {}).get("decision_ids") or []
        if ids:
            rows = store.db.execute(f"SELECT id, minute, kind, summary FROM decisions WHERE id IN "
                                    f"({','.join('?' * len(ids))})", ids).fetchall()
            out["evidence"] = [{"id": i, "at": fmt(m), "kind": k, "summary": s} for i, m, k, s in rows]
    day = minute // 1440
    out["kpis_before_after"] = [_kpis(store, wf, d) for d in (day - 1, day, day + 1, day + 2)]
    nxt = store.db.execute("SELECT minute, kind, summary FROM decisions WHERE wf=? AND minute>? AND minute<=? AND kind IN "
                           "('decay_rollback','incident_opened','incident_resolved','did_flagged','applied',"
                           "'auto_applied','supervisor_edit','winner_selected','experiment_concluded') ORDER BY id",
                           (wf, minute, minute + 2 * 1440)).fetchall()
    out["what_happened_next_48h"] = [f"{fmt(m)} {k}: {s}" for m, k, s in nxt]
    return out


def heard(store, wf, person, t, api_cfg=None):
    """Which variants a person could have heard at time t, and why that allocation was live."""
    f = store.db.execute("SELECT f FROM persons WHERE pid=?", (person,)).fetchone()
    if not f:
        return {"error": "unknown person"}
    feats = dict(zip(FEATS, f[0].split("|")))
    v = version_at(store, wf, t)
    pid = v[5] if v else None
    req = json.loads(store.db.execute("SELECT request FROM proposals WHERE proposal_id=?", (pid,)).fetchone()[0]) if pid else None
    cfg = (req or {}).get("proposed_config")
    out = {"person": person, "features": feats, "config_version": v[0] if v else 1, "set_by": v[1] if v else "default"}
    if cfg:
        c = cohort_of(cfg, feats)
        out["cohort"] = c
        out["allocation"] = {d: dist(cfg, c, d) for d in ("welcome_message", "voice", "caller_id")}
        out["prompt_version"] = cfg["prompt_version"]
    else:
        out["note"] = "config set by supervisor/business default; see `why` for the version"
    out["why_this_version"] = why(store, wf, t)
    return out


def timeline(store, wf):
    return [f"{fmt(m)} [{k}] {s}" for m, k, s in store.db.execute(
        "SELECT minute, kind, summary FROM decisions WHERE wf=? ORDER BY id", (wf,))]


def health(store):
    r = store.db.execute("SELECT COUNT(*), SUM(events), SUM(ingest_s), SUM(decide_s), SUM(wall_s), MAX(rss_mb), "
                         "MIN(credits), MAX(minute) FROM loop_metrics").fetchone()
    n, ev, ing, dec, wall, rss, cred, last = r
    days = (last or 0) / 1440
    per_day = store.db.execute("SELECT minute/1440, SUM(events), SUM(wall_s), SUM(ingest_s) FROM loop_metrics "
                               "GROUP BY minute/1440 ORDER BY 1").fetchall()
    decisions = store.db.execute("SELECT kind, COUNT(*) FROM decisions GROUP BY kind ORDER BY 2 DESC").fetchall()
    return {"steps": n, "events_ingested": ev, "ingest_events_per_s": round(ev / ing) if ing else None,
            "wall_s_total": round(wall or 0, 1), "decide_s_total": round(dec or 0, 1),
            "max_decide_s_per_step": store.db.execute("SELECT ROUND(MAX(decide_s),2) FROM loop_metrics").fetchone()[0],
            "peak_rss_mb": round(rss or 0), "credits_remaining": cred,
            "per_sim_day": [{"day": d, "events": e, "wall_s": round(w, 1), "ingest_s": round(i, 1)} for d, e, w, i in per_day],
            "decisions_by_kind": dict(decisions),
            "alerts": [s for (s,) in store.db.execute("SELECT summary FROM decisions WHERE kind LIKE 'alert_%'")]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="state/controller.db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("why")
    w.add_argument("wf")
    w.add_argument("at")
    h = sub.add_parser("heard")
    h.add_argument("wf")
    h.add_argument("person")
    h.add_argument("at")
    t = sub.add_parser("timeline")
    t.add_argument("wf")
    sub.add_parser("health")
    a = ap.parse_args()
    s = Store(a.db)
    if a.cmd == "why":
        out = why(s, a.wf, parse_t(a.at))
    elif a.cmd == "heard":
        out = heard(s, a.wf, a.person, parse_t(a.at))
    elif a.cmd == "timeline":
        print("\n".join(timeline(s, a.wf)))
        return
    else:
        out = health(s)
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
