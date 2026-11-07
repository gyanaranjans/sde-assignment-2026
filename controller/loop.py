"""The control loop and the governance client.

  ingest → (per workflow) reconcile proposals → learn → render desired config → reach it legally → advance

Governance rules this file enforces (see SUBMISSION.md §6):
  * Every proposal row is written to SQLite *before* the HTTP call, keyed by a unique proposal_id. On restart
    a 'submitting' row is reconciled against GET /approvals (or config history for auto-apply), so nothing is
    double-submitted, double-applied or forgotten.
  * At most one routine approval and one urgent (incident) approval in flight per workflow.
  * Routine requests: only inside approver hours and not before 11:00; one request slot per 24h is reserved
    for incidents.
  * Auto-apply is used voluntarily only for operational dims (caller_id, schedule) even where the policy allows
    more; customer-facing changes always get a named human. Steps respect max_shift.
  * Never auto-apply while an approval is in flight (it would stale the approval) unless a caller ID is
    actively failing.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import resource
import sys
import time
from pathlib import Path

from .api import Api
from .brain import Brain
from .configs import canon, changed_dims, diff, dist, max_shift, tv
from .ingest import Ingestor
from .store import Store

STEP_MINUTES = 60
OPERATIONAL_DIMS = {"caller_id", "schedule"}
VALIDATION_N = 500
CREDIT_FLOOR_SHARE = 0.15       # keep this share of the budget for the next incident
ROUTINE_EARLIEST_HOUR = 11      # Kestrel ops standups; harmless for Northwind
REJECT_BACKOFF_MIN = 180


def log(level, kind, **kw):
    print(json.dumps({"t": time.strftime("%H:%M:%S"), "level": level, "kind": kind, **kw}, default=str),
          file=sys.stderr, flush=True)


def fmt(minute):
    d, m = divmod(minute, 1440)
    return f"D{d} {m // 60:02d}:{m % 60:02d}"


class Controller:
    def __init__(self, url: str, state_dir: str):
        self.api = Api(url, log)
        self.store = Store(Path(state_dir) / "controller.db")
        self.state_dir = Path(state_dir)
        st = self.api.ok("GET", "/sim/state")
        self.now_min = st["sim_minute"]
        if self.store.get("d0_date") is None:
            d = dt.date.fromisoformat(st["ts"][:10]) - dt.timedelta(days=st["sim_minute"] // 1440)
            self.store.put("d0_date", d.isoformat())
            self.store.put("credits_initial", st["credits_remaining"])
            self.store.put("scenario", st["scenario"])
        self.credits = st["credits_remaining"]
        self.ing = Ingestor(self.store, self.api, log)
        self.wfs = {w["workflow_id"]: w for w in self.api.ok("GET", "/workflows")}
        self.brains = {}
        for wid, w in self.wfs.items():
            if self.store.wf_state(wid) is None:
                w["default_config_snapshot"] = canon(self.api.ok("GET", f"/workflows/{wid}/config")["config"])
            self.brains[wid] = Brain(w, self.store, self.ing, lambda: self.now_min, log)
            self.brains[wid].save()
        self.seq = self.store.get("seq", 0)

    # ------------------------------------------------------------------ main loop
    def run(self):
        restarted = self.store.get("started", False)
        self.store.put("started", True)
        if restarted:
            self.store.decide(self.now_min, fmt(self.now_min), None, "controller_restart",
                              "controller restarted; resuming from durable state",
                              last_chunk=self.ing.last_chunk())
        n, _, secs = self.ing.catch_up()
        log("info", "caught_up", events=n, seconds=round(secs, 1))
        while True:
            t0 = time.time()
            st = self.api.ok("GET", "/sim/state")
            self.now_min, self.credits = st["sim_minute"], st["credits_remaining"]
            if st["finished"]:
                break
            td = time.time()
            for wid in self.wfs:
                try:
                    self.step_workflow(wid)
                except Exception as e:  # one workflow's bug must not stall the platform
                    self.alert("page", wid, f"workflow step failed: {e!r}")
                    raise
            decide_s = time.time() - td
            r = self.api.post("/sim/advance", {"minutes": STEP_MINUTES})
            if r[0] == 409:
                break
            n, _, ingest_s = self.ing.catch_up()
            self.now_min = r[1]["sim_minute"]
            self.store.metric(minute=self.now_min, wall_s=time.time() - t0, events=n, ingest_s=ingest_s,
                              decide_s=decide_s, credits=self.credits,
                              rss_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1 << 20 if sys.platform == "darwin" else 1 << 10))
            if self.now_min % 1440 == 0:
                self.daily_report()
            if os.environ.get("CTRL_CRASH_AT") and self.now_min >= int(os.environ["CTRL_CRASH_AT"]):
                log("warn", "simulated_crash", minute=self.now_min)
                os._exit(137)
        # final reconcile so nothing approved is left hanging, then report
        for wid in self.wfs:
            self.reconcile(wid)
        self.daily_report()
        log("info", "finished", minute=self.now_min, credits=self.credits)

    def alert(self, severity, wid, msg, **kw):
        self.store.decide(self.now_min, fmt(self.now_min), wid, f"alert_{severity}", msg, **kw)
        log("error" if severity == "page" else "warn", "alert", severity=severity, wf=wid, msg=msg)

    # ------------------------------------------------------------------ per-workflow step
    def step_workflow(self, wid):
        b, w = self.brains[wid], self.wfs[wid]
        self.handle_supervisor_edits(wid)
        self.reconcile(wid)
        cur = self.api.ok("GET", f"/workflows/{wid}/config")
        cfg, ver = canon(cur["config"]), cur["version"]
        b.mark_explore_live(cfg)
        b.update_did()
        self.incident_step(wid, cfg, ver)
        hour = (self.now_min % 1440) // 60
        lo, hi = w["governance"]["approver_hours"]
        routine_window = max(lo, ROUTINE_EARLIEST_HOUR) <= hour < hi - 1
        if routine_window:
            b.init_schedule()
            b.start_exploration()
            b.evaluate()
        target = b.render(cfg)
        dims = changed_dims(cfg, target)
        if not dims:
            return
        inflight = self.inflight(wid)
        gov = w["governance"]
        auto_dims = set(gov["auto_apply"]["allowed_dims"]) & OPERATIONAL_DIMS if gov["auto_apply"]["enabled"] else set()
        # operational part first: caller_id (+ schedule when the cohort set is unchanged)
        part = copy.deepcopy(cfg)
        part["allocations"]["default"]["caller_id"] = target["allocations"]["default"]["caller_id"]
        if target["cohorts"] == cfg["cohorts"]:
            part["schedule"] = target["schedule"]
        pdims = changed_dims(cfg, part)
        did_urgent = any(cfg["allocations"]["default"]["caller_id"].get(d, 0) > 0 for d in b.st["did"]["flagged"])
        if pdims and set(pdims) <= auto_dims:
            if not inflight["any"] or did_urgent:
                self.auto_step(wid, cfg, ver, part, pdims)
                return
            if set(dims) <= auto_dims:
                return  # operational-only: wait for the in-flight approval instead of spending approver attention
        if inflight["routine"] or not routine_window:
            return
        last_rej = self.store.db.execute(
            "SELECT MAX(updated_min) FROM proposals WHERE wf=? AND status IN ('rejected','expired','lapsed','stale')",
            (wid,)).fetchone()[0]
        if last_rej is not None and self.now_min - last_rej < REJECT_BACKOFF_MIN:
            return
        reserve = 1 if gov["max_requests_per_24h"] >= 2 else 0
        if self.requests_24h(wid) >= gov["max_requests_per_24h"] - reserve:
            return
        self.submit_approval(wid, cfg, ver, target, kind="routine")

    # ------------------------------------------------------------------ incidents
    def incident_step(self, wid, cfg, ver):
        b, w = self.brains[wid], self.wfs[wid]
        inc = b.st["incident"]
        p = cfg["prompt_version"]
        if inc is None:
            alarm, stats = b.hallucination_check(p)
            if not alarm:
                return
            inc = b.st["incident"] = {"status": "open", "prompt": p, "opened": self.now_min, "stats": stats,
                                      "tried": [], "llm": None, "validated": None, "fix_applied_at": None}
            if p not in b.st["bad_prompts"]:
                b.st["bad_prompts"].append(p)
            b.decide("incident_opened", f"hallucination flags on {p}: {stats['rate']:.1%} over "
                     f"{stats['connected']} connected calls vs {stats['reference_rate']:.1%} reference (z={stats['z']})",
                     **stats)
            self.alert("page", wid, f"hallucination incident on prompt {p}", **stats)
            b.save()
        if inc["status"] == "open" and p != inc["prompt"]:
            inc.update(status="verifying", fix=p, fix_applied_at=self.now_min)
            b.decide("incident_fix_live", f"prompt moved {inc['prompt']}→{p}; verifying")
        if inc["status"] == "open":
            if inc["validated"] is None:
                self.find_fix(wid, inc)
            if inc["validated"] and not self.inflight(wid)["urgent"]:
                lo, hi = w["governance"]["approver_hours"]
                hour = (self.now_min % 1440) // 60
                if lo <= hour < hi and self.requests_24h(wid) < w["governance"]["max_requests_per_24h"]:
                    target = copy.deepcopy(cfg)
                    target["prompt_version"] = inc["validated"]["prompt"]
                    self.submit_approval(wid, cfg, ver, target, kind="urgent", incident=inc)
        elif inc["status"] == "verifying":
            since = inc["fix_applied_at"]
            n = sum(nk[0] for (pp, d, h), nk in self.ing.view("hal", wid).items()
                    if pp == p and int(d) * 1440 + int(h) * 60 >= since)
            if n >= 300:
                alarm, stats = b.hallucination_check(p)
                if alarm:
                    b.st["bad_prompts"].append(p)
                    inc.update(status="open", prompt=p, validated=None)
                    b.decide("incident_fix_failed", f"{p} is also hallucinating; reopening", **stats)
                else:
                    b.decide("incident_resolved", f"flag rate on {p} back to {stats['rate']:.1%} over {n} calls "
                             f"(opened {fmt(inc['opened'])}, MTTR {(self.now_min - inc['opened']) / 60:.1f}h)",
                             **stats)
                    b.st["incident"] = None
        b.save()

    def find_fix(self, wid, inc):
        b, w = self.brains[wid], self.wfs[wid]
        min_pass = w["governance"].get("min_validation_pass_rate", 0.97)
        floor = self.store.get("credits_initial") * CREDIT_FLOOR_SHARE
        cands = [c for c in b.fallback_prompts(inc["prompt"]) if c not in inc["tried"]]
        if not cands and inc["llm"] is None and self.credits - 3 >= floor / 2:
            s, r = self.api.post("/llm/suggest-fixes", {
                "workflow_id": wid, "problem": f"Prompt {inc['prompt']} shows hallucination-flag rate "
                f"{inc['stats']['rate']:.1%} vs {inc['stats']['reference_rate']:.1%} baseline; need a safer script "
                f"that keeps engagement."})
            inc["llm"] = r.get("suggestions", []) if s == 200 else []
            b.decide("llm_suggest", f"asked LLM for fixes: {[x['prompt_version'] for x in inc['llm']]}",
                     suggestions=inc["llm"], status=s)
        if not cands:
            cands = [x["prompt_version"] for x in inc["llm"] or [] if x["prompt_version"] not in inc["tried"]
                     and x["prompt_version"] not in b.st["bad_prompts"]]
        if not cands:
            self.alert("page", wid, "hallucination incident with no viable candidate prompt — needs a human")
            inc["status"] = "stuck"
            return
        cand = cands[0]
        cost = -(-VALIDATION_N // 100)
        if self.credits - cost < floor / 2:
            self.alert("page", wid, "credits too low to validate an incident fix", credits=self.credits)
            return
        s, r = self.api.post("/validation/runs", {"workflow_id": wid, "prompt_version": cand, "n": VALIDATION_N})
        if s != 200:
            self.alert("warn", wid, f"validation call failed {s}: {r}")
            return
        self.credits = r["credits_remaining"]
        inc["tried"].append(cand)
        ok = r["pass_rate"] >= min_pass
        b.decide("validation", f"validated {cand}: pass {r['pass_rate']:.3f} on n={r['n']} "
                 f"({'meets' if ok else 'below'} {min_pass})", run=r)
        if ok:
            inc["validated"] = {"prompt": cand, "run_id": r["validation_run_id"], "pass_rate": r["pass_rate"]}
        else:
            b.st["bad_prompts"].append(cand)

    # ------------------------------------------------------------------ governance client
    def inflight(self, wid):
        rows = self.store.proposals(wid, ["submitting", "pending", "approved"])
        return {"routine": [r for r in rows if r["kind"] == "routine" and r["path"] == "approval"],
                "urgent": [r for r in rows if r["kind"] == "urgent"],
                "any": rows}

    def requests_24h(self, wid):
        return self.store.db.execute(
            "SELECT COUNT(*) FROM proposals WHERE wf=? AND path='approval' AND status!='abandoned' AND created_min>?",
            (wid, self.now_min - 1440)).fetchone()[0]

    def next_pid(self, wid, kind):
        self.seq += 1
        self.store.put("seq", self.seq)
        return f"ctl-{self.store.get('scenario')}-{wid}-{kind}-{self.now_min}-{self.seq}"

    def reconcile(self, wid):
        for p in self.store.proposals(wid, ["submitting", "pending", "approved"]):
            pid = p["proposal_id"]
            if p["status"] == "submitting":
                if p["path"] == "approval":
                    s, lst = self.api.get("/approvals", workflow_id=wid)
                    hit = next((a for a in lst if a["proposal_id"] == pid), None)
                    if hit:
                        self.store.update_proposal(pid, self.now_min, status=hit["status"], approval_id=hit["approval_id"])
                        p.update(status=hit["status"], approval_id=hit["approval_id"])
                    else:
                        self.store.update_proposal(pid, self.now_min, status="abandoned")
                        continue
                else:
                    hist = self.api.ok("GET", f"/workflows/{wid}/config/history")
                    hit = next((v for v in hist if v["proposal_id"] == pid), None)
                    self.store.update_proposal(pid, self.now_min, status="applied" if hit else "abandoned",
                                               applied_version=hit and hit["version"])
                    continue
            if p["status"] == "pending":
                s, a = self.api.get(f"/approvals/{p['approval_id']}")
                if s != 200 or a["status"] == "pending":
                    continue
                self.store.update_proposal(pid, self.now_min, status=a["status"], reasons=a.get("reasons"))
                self.store.decide(self.now_min, fmt(self.now_min), wid, f"approval_{a['status']}",
                                  f"{p['approval_id']} ({p['summary']}) → {a['status']}",
                                  approval=a, proposal_id=pid)
                p["status"] = a["status"]
            if p["status"] == "approved":
                s, r = self.api.post(f"/workflows/{wid}/apply", {"approval_id": p["approval_id"]})
                if s == 200:
                    self.store.update_proposal(pid, self.now_min, status="applied", applied_version=r["version"])
                    self.store.decide(self.now_min, fmt(self.now_min), wid, "applied",
                                      f"applied {p['approval_id']} as v{r['version']}: {p['summary']}",
                                      proposal_id=pid, version=r["version"], dims=r["changed_dims"])
                    b = self.brains[wid]
                    if p["kind"] == "urgent" and b.st["incident"]:
                        b.st["incident"].update(status="verifying", fix_applied_at=self.now_min)
                        b.save()
                else:
                    status = {410: "lapsed"}.get(s, "stale" if (isinstance(r, dict) and r.get("error") == "stale_base_version") else "failed")
                    self.store.update_proposal(pid, self.now_min, status=status, reasons=[r])
                    self.store.decide(self.now_min, fmt(self.now_min), wid, f"apply_{status}",
                                      f"could not apply {p['approval_id']}: {r}; will re-propose on fresh data")

    def _active_leads(self, wid):
        cad = len(self.wfs[wid]["cadence"])
        day = self.now_min // 1440
        rows = self.store.db.execute("SELECT day, SUM(leads) FROM campaigns WHERE wf=? AND day>? GROUP BY day",
                                     (wid, day - cad)).fetchall()
        cc = self.ing.view("cc", wid)
        return sum(max(n - cc.get((str(d),), [0, 0])[1], 0) for d, n in rows)

    def _rationale(self, wid):
        last = self.store.db.execute("SELECT MAX(created_min) FROM proposals WHERE wf=? AND path='approval' "
                                     "AND status IN ('applied','pending','approved')", (wid,)).fetchone()[0] or -10**9
        rows = self.store.db.execute(
            "SELECT id, ts, kind, summary FROM decisions WHERE wf=? AND minute>=? AND kind IN "
            "('schedule_planned','explore_planned','winner_selected','experiment_concluded','decay_rollback',"
            "'arm_dropped','did_plan','did_flagged','incident_opened','validation','supervisor_edit') ORDER BY id",
            (wid, last)).fetchall()
        return rows[-12:]

    def submit_approval(self, wid, cfg, ver, target, kind, incident=None):
        b, w = self.brains[wid], self.wfs[wid]
        dims = changed_dims(cfg, target)
        people = b.sample_people()
        est = b.estimate(cfg, target, people)
        active = self._active_leads(wid)
        affected = int(round(active * est["share_of_people_affected"])) if kind == "routine" else active
        primary = w["objective"]["primary"]
        if kind == "urgent":
            impact = 0.9
            summary = (f"Incident fix: switch prompt {cfg['prompt_version']}→{target['prompt_version']} "
                       f"(hallucination flags {incident['stats']['rate']:.1%} vs {incident['stats']['reference_rate']:.1%} normal)")
            rationale = (f"Live prompt {cfg['prompt_version']} is hallucinating: {incident['stats']}. "
                         f"{target['prompt_version']} passed validation ({incident['validated']['pass_rate']:.3f}, "
                         f"{incident['validated']['run_id']}) and has a clean record on this workflow. Only the prompt changes.")
            evidence = {"validation_run_id": incident["validated"]["run_id"], "incident": incident["stats"]}
        else:
            gain = est["engagement_rel_change"] if primary == "engagement_rate" else est["pickup_rel_change_from_caller_id"]
            impact = round(min(1.0, max(gain, 0) / 0.10) * 0.9 + 0.05, 3)
            parts = []
            for dim in dims:
                if dim in ("welcome_message", "voice"):
                    r = b.st["dims"][dim]
                    parts.append(f"{dim}: " + (("drop losing arm, keep testing" if r["explore_since"] else "start A/B test")
                                               if r["mode"] == "explore" else
                                               "promote winners" if r["assign"] else "end test, back to control"))
                elif dim == "caller_id":
                    parts.append(f"caller IDs → {target['allocations']['default']['caller_id']}")
                elif dim == "schedule":
                    parts.append("call-time weights per cohort")
                elif dim == "cohorts":
                    parts.append(f"{len(target['cohorts'])} cohorts")
            summary = "; ".join(parts)
            rationale = " | ".join(f"[#{i}] {s}" for i, _, _, s in self._rationale(wid)) or "see decision log"
            evidence = {"decision_ids": [i for i, *_ in self._rationale(wid)]}
        pid = self.next_pid(wid, kind)
        body = {"proposal_id": pid, "workflow_id": wid, "base_version": ver, "proposed_config": target,
                "change_summary": summary, "diff": diff(cfg, target), "affected_leads": int(affected),
                "dry_run": {**est, "active_leads": active, "people_sampled": len(people)},
                "impact_score": impact, "rationale": rationale, "evidence": evidence}
        self.store.add_proposal(pid, wid, "approval", kind, ver, dims, summary,
                                {k: v for k, v in body.items()}, self.now_min)
        s, r = self.api.post("/approvals", body)
        if s == 200:
            self.store.update_proposal(pid, self.now_min, status=r["status"], approval_id=r["approval_id"])
            self.store.decide(self.now_min, fmt(self.now_min), wid, "approval_requested",
                              f"{r['approval_id']}: {summary}", proposal_id=pid, dims=dims, impact=impact,
                              affected=affected, dry_run=est)
        elif s == 409 and r.get("error") == "duplicate_proposal_id":
            self.reconcile(wid)
        else:
            self.store.update_proposal(pid, self.now_min, status="abandoned", reasons=[r])
            self.alert("warn", wid, f"approval request refused {s}: {r}")

    def auto_step(self, wid, cfg, ver, part, pdims):
        """Moves toward `part` by at most max_shift (TV) per step."""
        w = self.wfs[wid]
        ms = w["governance"]["auto_apply"]["max_shift"]
        step = copy.deepcopy(part)
        a, t = cfg["allocations"]["default"]["caller_id"], part["allocations"]["default"]["caller_id"]
        d = tv(a, t)
        if d > ms + 1e-9:
            alpha = (ms - 1e-4) / d
            mixed = {o: a.get(o, 0) + alpha * (t.get(o, 0) - a.get(o, 0)) for o in set(a) | set(t)}
            step["allocations"]["default"]["caller_id"] = {o: round(v, 5) for o, v in mixed.items() if v > 1e-6}
        step = canon(step)
        pdims = changed_dims(cfg, step)
        pid = self.next_pid(wid, "auto")
        b = self.brains[wid]
        rationale = " | ".join(s for _, _, k, s in self._rationale(wid) if k in ("did_plan", "did_flagged", "schedule_planned")) \
            or "operational rebalancing"
        body = {"proposal_id": pid, "base_version": ver, "proposed_config": step, "rationale": rationale,
                "evidence": {"did_health_3d": b.did_health(3 * 1440)}}
        summary = f"auto: {pdims} " + (f"caller_id→{step['allocations']['default']['caller_id']}" if "caller_id" in pdims else "")
        self.store.add_proposal(pid, wid, "auto", "auto", ver, pdims, summary, {"diff": diff(cfg, step),
                                "rationale": rationale, "max_shift": max_shift(cfg, step)}, self.now_min)
        s, r = self.api.put(f"/workflows/{wid}/config", body)
        if s == 200:
            self.store.update_proposal(pid, self.now_min, status="applied", applied_version=r["version"])
            self.store.decide(self.now_min, fmt(self.now_min), wid, "auto_applied", f"v{r['version']} {summary}",
                              proposal_id=pid, diff=diff(cfg, step), rationale=rationale)
        else:
            self.store.update_proposal(pid, self.now_min, status="abandoned", reasons=[r])
            self.alert("warn", wid, f"auto-apply refused {s}: {r}")

    # ------------------------------------------------------------------ supervisor edits & reporting
    def handle_supervisor_edits(self, wid):
        seen = self.store.get(f"sup_seen:{wid}", 0)
        rows = self.store.db.execute("SELECT version, dims, ts FROM config_versions WHERE wf=? AND actor='supervisor' "
                                     "AND version>? ORDER BY version", (wid, seen)).fetchall()
        for v, dims, ts in rows:
            if ts and dt.date.fromisoformat(ts[:10]) < dt.date.fromisoformat(self.store.get("d0_date")):
                pass  # history edits are part of the baseline we inherited
            else:
                self.brains[wid].on_supervisor_edit({"version": v, "changed_dims": json.loads(dims), "ts": ts})
            self.store.put(f"sup_seen:{wid}", v)

    def daily_report(self):
        from .view import render_view
        (self.state_dir / "supervisor_view.md").write_text(render_view(self.store, self.now_min))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--state", default=os.environ.get("CTRL_STATE", "state"))
    a = ap.parse_args()
    Controller(a.url, a.state).run()


if __name__ == "__main__":
    main()
