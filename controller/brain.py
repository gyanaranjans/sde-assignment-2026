"""Per-workflow learning loop: what the controller *wants* the config to be, and why.

The brain never talks to governance. It maintains a persisted `state` (desired roles + evidence) and
renders a desired config from (state, live config). The governance layer (loop.py) decides how — and
whether — that desired config may be reached.

Roles (each owns one slice of the config):
  schedule              hour_weights per cohort, from historical pickup-by-hour per segment
  welcome_message/voice experiment: init → explore → promoted (winners per segment) ; or hold
  caller_id             probe untested DIDs, drop degraded ones, spread load evenly over healthy ones
  prompt                only changes in incident response (hallucination), via approval + validation
A dim touched by a supervisor goes to `hold`: we keep whatever they set and stop managing it.
"""
from __future__ import annotations

import copy
import itertools

from .configs import canon, cohort_of, dist
from .ingest import EXP_DIMS, FEATS
from .stats import bh_threshold, rate, z_binom_excess, z_two_prop

# --- tunables (reasoning in SUBMISSION.md §5)
EXPLORE_CONTROL_SHARE = 0.4      # control keeps the plurality while exploring (0.6 when only 2 options)
HOLDOUT = 0.2                    # after promotion, control keeps 20% so decay stays measurable
MIN_ARM_CONN = 150               # connected calls per arm per segment before a comparison counts
FDR_Q = 0.10                     # Benjamini–Hochberg over every segment×option test of a dimension
MIN_WIN_Z = 2.5                  # ...and never on a single test weaker than this (BH alone admits z≈1.3 under correlated tests)
MIN_REL_LIFT = 0.03              # ignore significant-but-tiny lifts
EXPLORE_MIN_DAYS = 1.5
EXPLORE_MAX_DAYS = 5.0
LOSER_Z = -2.5                   # drop an arm from exploration when it is clearly worse globally
DECAY_Z = -2.0                   # winner significantly below holdout in the recent window
DECAY_DRIFT_Z = -3.0             # winner's own rate fell vs its exploration-time rate, control's did not
DID_PROBE = 0.10
DID_PROBE_TOTAL = 0.15
DID_MIN_DIALS = 150
HAL_MIN_CONN = 150
HAL_Z = 4.0
MAX_COHORTS = 20


def _minute(day, hour):
    return int(day) * 1440 + int(hour) * 60


class Brain:
    def __init__(self, wf: dict, store, ing, now_fn, log):
        self.wf, self.id = wf, wf["workflow_id"]
        self.store, self.ing, self.now, self.log = store, ing, now_fn, log
        self.st = store.wf_state(self.id) or self._init_state()

    def _init_state(self) -> dict:
        d = self.wf["default_config_snapshot"]
        return {
            "dims": {dim: {"mode": "init", "control": max(dist(d, "default", dim).items(), key=lambda x: x[1])[0],
                           "explore_alloc": None, "explore_since": None, "feature": "*", "assign": {},
                           "retired": [], "promoted_at": None, "evidence": None}
                     for dim in EXP_DIMS if len(self.wf["options"][dim]) > 1},
            "schedule": None, "schedule_hold": False, "cohorts_hold": False,
            "did": {"weights": dict(dist(d, "default", "caller_id")), "flagged": [], "hold": False},
            "incident": None, "bad_prompts": [],
        }

    def save(self):
        self.store.save_wf_state(self.id, self.st)

    def decide(self, kind, summary, **detail):
        m = self.now()
        self.store.decide(m, None, self.id, kind, summary, **detail)
        self.log("info", kind, wf=self.id, summary=summary)

    # ------------------------------------------------------------------ helpers over counters
    def _day(self):
        return self.now() // 1440

    def values_of(self, feat) -> dict:
        """feature value -> dials, from the hour-profile counters (history + live)."""
        out = {}
        for (f, v, h), nk in self.ing.view("hr", self.id).items():
            if f == feat:
                out[v] = out.get(v, 0) + nk[0]
        return out

    def arm_table(self, dim, feat, day_from, day_to=10**6) -> dict:
        tab = {}
        for (d, o, f, v, day), nk in self.ing.view("arm", self.id).items():
            if d == dim and f == feat and day_from <= int(day) <= day_to:
                a = tab.setdefault(v, {}).setdefault(o, [0, 0])
                a[0] += nk[0]
                a[1] += nk[1]
        return tab

    # ------------------------------------------------------------------ supervisor edits
    def on_supervisor_edit(self, ev):
        dims = ev["changed_dims"]
        for dim in dims:
            if dim in self.st["dims"] and self.st["dims"][dim]["mode"] != "hold":
                self.st["dims"][dim]["mode"] = "hold"
        if "caller_id" in dims:
            self.st["did"]["hold"] = True
        if "schedule" in dims:
            self.st["schedule_hold"] = True
        if "cohorts" in dims:
            self.st["cohorts_hold"] = True
        self.decide("supervisor_edit", f"supervisor changed {dims} (v{ev['version']}); controller stops "
                    f"managing those dims and rebases pending proposals", event=ev)
        self.save()

    # ------------------------------------------------------------------ schedule (call-time per cohort)
    def init_schedule(self):
        if self.st["schedule"] is not None or self.st["schedule_hold"]:
            return
        lo, hi = self.wf["dialling_window"]
        hours = [str(h) for h in range(lo, hi)]
        hr = self.ing.view("hr", self.id)
        best = None
        for feat in FEATS:
            vals = {}
            for (f, v, h), nk in hr.items():
                if f == feat and h in hours:
                    vals.setdefault(v, {})[h] = nk
            tot = {h: [sum(vals[v].get(h, [0, 0])[0] for v in vals), sum(vals[v].get(h, [0, 0])[1] for v in vals)]
                   for h in hours}
            p_all = {h: rate(tot[h]) for h in hours}
            mean_all = sum(p_all.values()) / len(hours) or 1e-9
            n_tot = sum(t[0] for t in tot.values()) or 1
            H, idx = 0.0, {}
            for v, by_h in vals.items():
                n_v = sum(nk[0] for nk in by_h.values())
                if n_v < 100 * len(hours) or any(by_h.get(h, [0])[0] < 60 for h in hours):
                    continue
                p = {h: rate(by_h.get(h)) for h in hours}
                m = sum(p.values()) / len(hours) or 1e-9
                idx[v] = {h: p[h] / m for h in hours}
                H += n_v / n_tot * sum((idx[v][h] - p_all[h] / mean_all) ** 2 for h in hours) / len(hours)
            if len(idx) >= 2 and (best is None or H > best[0]):
                best = (H, feat, idx, vals)
        if best is None or best[0] < 0.004:
            self.st["schedule"] = {"feature": None}
            self.decide("schedule_skipped", "no segment shows a distinct enough time-of-day pickup profile",
                        heterogeneity=None if best is None else round(best[0], 5))
            self.save()
            return
        H, feat, idx, vals = best
        # group values by which third of the window they peak in
        n_b = 3
        bands = [hours[i * len(hours) // n_b:(i + 1) * len(hours) // n_b] for i in range(n_b)]
        groups = {}
        for v, ix in idx.items():
            groups[v] = ["early", "mid", "late"][max(range(n_b), key=lambda b: sum(ix[h] for h in bands[b]) / len(bands[b]))]
        weights = {}
        for g in set(groups.values()):
            members = [v for v in groups if groups[v] == g]
            pooled = {h: [sum(vals[v][h][0] for v in members), sum(vals[v][h][1] for v in members)] for h in hours}
            p = {h: rate(pooled[h]) for h in hours}
            m = sum(p.values()) / len(hours)
            weights[g] = {h: round(min(max((p[h] / m) ** 2, 0.25), 4.0), 3) for h in hours}
        if len(set(groups.values())) < 2:
            self.st["schedule"] = {"feature": None}
            self.decide("schedule_skipped", f"{feat} segments all peak in the same part of the day")
        else:
            self.st["schedule"] = {"feature": feat, "groups": groups, "weights": weights}
            self.decide("schedule_planned", f"call-time by {feat}: " + ", ".join(
                f"{g}={sorted(v for v in groups if groups[v] == g)}" for g in sorted(set(groups.values()))),
                heterogeneity=round(H, 5), weights=weights)
        self.save()

    # ------------------------------------------------------------------ caller-ID health
    def did_health(self, window_minutes: int, since: dict | None = None):
        """Pickup of each DID relative to the other DIDs dialled in the *same hours* (controls for hour and
        lead mix, since caller IDs are randomised per dial). `since` lets a re-admitted DID be judged only
        on data after it came back."""
        cutoff = self.now() - window_minutes
        by_h = {}
        for (did, day, hour), nk in self.ing.view("did", self.id).items():
            if _minute(day, hour) >= cutoff:
                by_h.setdefault((_minute(day, hour)), {})[did] = nk
        out = {}
        vol = {}
        for (did, day, hour), nk in self.ing.view("did", self.id).items():
            if _minute(day, hour) >= self.now() - 3 * 1440:
                vol[did] = vol.get(did, 0) + nk[0]
        for did in self.wf["options"]["caller_id"]:
            n = k = 0
            exp = 0.0
            start = (since or {}).get(did, -10**9)
            for m, bucket in by_h.items():
                if did not in bucket or m < start:
                    continue
                on = sum(v[0] for d, v in bucket.items() if d != did)
                ok = sum(v[1] for d, v in bucket.items() if d != did)
                if on < 20:
                    continue
                n += bucket[did][0]
                k += bucket[did][1]
                exp += bucket[did][0] * ok / on
            if n:
                p = exp / n
                z = (k - exp) / max((n * p * (1 - p)) ** 0.5, 1e-9)
                out[did] = {"dials": n, "ratio": round(k / exp, 3) if exp else None, "z": round(z, 2),
                            "vol_3d": vol.get(did, 0)}
        return out

    def update_did(self):
        """Caller-ID rotation. A degraded DID is *rested* (weight 0) with exponential back-off (6, 12, 24 days)
        and then re-probed at a small share — pickup loss from overuse recovers with rest, a spam flag does
        not, and the data tells the two apart on re-probe. Load is spread evenly over live DIDs because
        pickup degrades with per-DID volume."""
        s = self.st["did"]
        if s["hold"]:
            return
        s.setdefault("rest", {})      # did -> {"until": minute, "strikes": n}
        s.setdefault("since", {})     # did -> minute it was (re)admitted
        now = self.now()
        opts = self.wf["options"]["caller_id"]
        for did, r in list(s["rest"].items()):
            if r["until"] is not None and now >= r["until"]:
                r["until"] = None
                s["since"][did] = now
                s["weights"][did] = 0  # forces a probe share below
                self.decide("did_readmitted", f"caller ID {did} back on probe after rest (strike {r['strikes']})")
        resting = {d for d, r in s["rest"].items() if r["until"]}
        short = self.did_health(8 * 60, s["since"])
        long_ = self.did_health(3 * 1440, s["since"])
        min_live = 2 if len(opts) >= 3 else 1
        for did in opts:
            if did in resting:
                continue
            h, why = short.get(did), "8h"
            bad = h and h["dials"] >= DID_MIN_DIALS and h["ratio"] is not None and h["ratio"] < 0.7 and h["z"] < -4
            if not bad:
                h, why = long_.get(did), "3d"
                bad = h and h["dials"] >= 2 * DID_MIN_DIALS and h["ratio"] is not None and h["ratio"] < 0.8 \
                    and h["z"] < -3.5
            if not bad or len(opts) - len(resting) - 1 < min_live:
                continue
            # pickup also drops when a DID is over-used; that is fixed by spreading load, not by resting it
            peers = [long_.get(d, {}).get("vol_3d", 0) for d in opts if d != did and d not in resting]
            if peers and h["vol_3d"] > 1.25 * max(peers):
                continue
            r = s["rest"].setdefault(did, {"until": None, "strikes": 0})
            r["strikes"] += 1
            r["until"] = now + 1440 * 6 * 2 ** (r["strikes"] - 1)
            resting.add(did)
            self.decide("did_flagged", f"caller ID {did} picks up at {h['ratio']:.0%} of its peers ({why} window, "
                        f"z={h['z']}, {h['dials']} dials) — resting it for {6 * 2 ** (r['strikes'] - 1)} days", health=h)
        prev = s["weights"]
        live = [d for d in opts if d not in resting]
        probing = set(s.setdefault("probing", []))
        probing |= {d for d in live if prev.get(d, 0) == 0}
        probing &= set(live)
        # a probe graduates only once it has enough dials to be judged, and is not trending bad
        def cleared(d):
            h = long_.get(d, {})
            return h.get("dials", 0) >= 2 * DID_MIN_DIALS and (h.get("ratio") or 0) >= 0.9 and h.get("z", -9) > -1.5
        probing = {d for d in probing if not cleared(d)}
        est = [d for d in live if d not in probing]
        if not est:
            est, probing = live, set()
        share = min(DID_PROBE, DID_PROBE_TOTAL / max(len(probing), 1))
        w = {d: share for d in probing}
        rest = 1 - share * len(probing)
        for d in est:
            w[d] = round(rest / len(est), 4)
        s["probing"] = sorted(probing)
        w = {d: v for d, v in w.items() if v > 0}
        if set(w) != {d for d, v in prev.items() if v > 0} or any(abs(w[d] - prev.get(d, 0)) > 0.02 for d in w):
            self.decide("did_plan", f"caller-ID allocation → {w}", resting=sorted(resting), probing=sorted(probing),
                        health_3d=long_)
        s["weights"], s["flagged"] = w, sorted(resting)
        self.save()

    # ------------------------------------------------------------------ experiments
    def start_exploration(self):
        for dim, r in self.st["dims"].items():
            if r["mode"] != "init":
                continue
            opts = self.wf["options"][dim]
            c = r["control"]
            share = 0.6 if len(opts) == 2 else EXPLORE_CONTROL_SHARE
            others = [o for o in opts if o != c]
            r["explore_alloc"] = {c: share, **{o: round((1 - share) / len(others), 4) for o in others}}
            r["mode"] = "explore"
            self.decide("explore_planned", f"explore {dim}: {r['explore_alloc']} (control {c})")
        self.save()

    def mark_explore_live(self, cfg: dict):
        """Exploration counts from the moment its allocation is actually live, never from the proposal."""
        for dim, r in self.st["dims"].items():
            if r["mode"] == "explore" and r["explore_since"] is None and \
                    set(dist(cfg, "default", dim)) == set(r["explore_alloc"]):
                r["explore_since"] = self.now()
                self.decide("explore_live", f"{dim} exploration is live")
                self.save()

    def _comparisons(self, dim, control, day_from, day_to=10**6, min_n=MIN_ARM_CONN):
        out = []
        for feat in ("*",) + FEATS:
            for v, arms in self.arm_table(dim, feat, day_from, day_to).items():
                c = arms.get(control)
                if not c or c[0] < min_n:
                    continue
                for o, a in arms.items():
                    if o == control or a[0] < min_n:
                        continue
                    rc = rate(c) or 1e-9
                    out.append({"feat": feat, "v": v, "opt": o, "z": round(z_two_prop(a[1], a[0], c[1], c[0]), 3),
                                "lift": round(rate(a) / rc - 1, 4), "arm": a, "ctl": c,
                                "se_rel": ((rate(a) * (1 - rate(a)) / a[0] + rc * (1 - rc) / c[0]) ** 0.5) / rc})
        return out

    def evaluate(self) -> bool:
        """Returns True when the desired state changed."""
        changed = False
        for dim, r in self.st["dims"].items():
            if r["mode"] == "explore" and r["explore_since"] is not None:
                changed |= self._eval_explore(dim, r)
            elif r["mode"] == "promoted":
                changed |= self._eval_decay(dim, r)
        if changed:
            self.save()
        return changed

    def _eval_explore(self, dim, r) -> bool:
        age_days = (self.now() - r["explore_since"]) / 1440
        since_day = r["explore_since"] // 1440
        comps = self._comparisons(dim, r["control"], since_day)
        m = max(len(comps), 1)
        zc = bh_threshold([c["z"] for c in comps], FDR_Q)
        recent = {(c["feat"], c["v"], c["opt"]): c for c in
                  self._comparisons(dim, r["control"], self._day() - 1, min_n=40)}
        # successive elimination: an arm clearly worse overall stops getting traffic
        losers = [c for c in comps if c["feat"] == "*" and c["z"] < LOSER_Z and c["opt"] in r["explore_alloc"]]
        changed = False
        for c in losers:
            r["explore_alloc"].pop(c["opt"])
            self.decide("arm_dropped", f"{dim}={c['opt']} is worse than {r['control']} "
                        f"({c['lift']:+.1%}, z={c['z']}) — removed from exploration", comparison=_slim(c))
            changed = True
        if age_days < EXPLORE_MIN_DAYS:
            return changed
        wins = [c for c in comps if c["z"] > max(zc, MIN_WIN_Z) and c["lift"] > MIN_REL_LIFT
                and recent.get((c["feat"], c["v"], c["opt"]), {"lift": 1})["lift"] > 0
                and self._guardrail_ok(dim, c["opt"], r["control"], since_day)]
        shares = {}
        for feat in ("*",) + FEATS:
            vals = self.values_of(feat) if feat != "*" else {"*": 1}
            tot = sum(vals.values()) or 1
            shares[feat] = {v: n / tot for v, n in vals.items()}
        best = None
        for feat in ("*",) + FEATS:
            assign, gain = {}, 0.0
            for c in wins:
                if c["feat"] != feat:
                    continue
                lb = c["lift"] - 0.84 * c["se_rel"]  # 80% lower bound: winners' curse
                if c["v"] not in assign or lb > assign[c["v"]][1]:
                    assign[c["v"]] = (c["opt"], lb, c)
            gain = sum(shares[feat].get(v, 0) * lb for v, (_, lb, _) in assign.items())
            if assign and (best is None or gain > best[0]):
                best = (gain, feat, assign)
        if best is None and age_days < EXPLORE_MAX_DAYS:
            return changed
        r["mode"] = "promoted"
        r["promoted_at"] = self.now()
        if best is None:
            r["feature"], r["assign"] = "*", {}
            self.decide("experiment_concluded", f"{dim}: no option beat {r['control']} after {age_days:.1f} days "
                        f"(BH q={FDR_Q} over {m} tests); reverting to control",
                        tests=m, top=[_slim(c) for c in sorted(comps, key=lambda c: -c["z"])[:5]])
        else:
            gain, feat, assign = best
            r["feature"] = feat
            r["assign"] = {v: o for v, (o, _, _) in assign.items()}
            r["evidence"] = {"gain_lb": round(gain, 4), "tests": m, "z_threshold": round(zc, 3), "fdr_q": FDR_Q,
                             "winners": [_slim(c) for _, _, c in assign.values()],
                             "explore_rates": {v: {"opt": [assign[v][2]["arm"][0], assign[v][2]["arm"][1]],
                                                   "ctl": [assign[v][2]["ctl"][0], assign[v][2]["ctl"][1]]}
                                               for v in assign}}
            self.decide("winner_selected", f"{dim}: " + ", ".join(
                f"{feat}={v}→{o} ({c['lift']:+.1%}, z={c['z']})" for v, (o, _, c) in assign.items())
                + f"; est. engagement gain ≥{gain:+.1%} (80% LB)", **r["evidence"])
        return True

    def _guardrail_ok(self, dim, opt, control, since_day) -> bool:
        obj = self.wf["objective"]
        if obj["primary"] != "engagement_rate":
            return True  # welcome/voice are heard after pickup; their guardrail is checked via engagement itself
        max_drop = obj.get("guardrail", {}).get("pickup_rate_max_drop", 0.02)
        a, c = [0, 0], [0, 0]
        for (d, o, day), nk in self.ing.view("armp", self.id).items():
            if d == dim and int(day) >= since_day and o in (opt, control):
                t = a if o == opt else c
                t[0] += nk[0]
                t[1] += nk[1]
        if not a[0] or not c[0]:
            return True
        drop = rate(c) - rate(a)
        if drop > max_drop and z_two_prop(a[1], a[0], c[1], c[0]) < -2:
            self.decide("guardrail_veto", f"{dim}={opt} lowers pickup by {drop:.3f} (> {max_drop})")
            return False
        return True

    def _eval_decay(self, dim, r) -> bool:
        if not r["assign"]:
            return False
        since = max(self._day() - 1, r["promoted_at"] // 1440)
        if self.now() - r["promoted_at"] < 1440:
            return False
        feat = r["feature"]
        tab = self.arm_table(dim, feat, since)
        changed = False
        for v, o in list(r["assign"].items()):
            arms = tab.get(v, {})
            a, c = arms.get(o, [0, 0]), arms.get(r["control"], [0, 0])
            if a[0] < 100:
                continue
            z = z_two_prop(a[1], a[0], c[1], c[0]) if c[0] >= 30 else 0.0
            ex = (r.get("evidence") or {}).get("explore_rates", {}).get(v)
            dz = dzc = 0.0
            if ex:
                dz = z_two_prop(a[1], a[0], ex["opt"][1], ex["opt"][0])
                dzc = z_two_prop(c[1], c[0], ex["ctl"][1], ex["ctl"][0]) if c[0] >= 30 else 0.0
            if z < DECAY_Z or (dz < DECAY_DRIFT_Z and dzc > -1.0 and z < 1.0):
                r["assign"].pop(v)
                r["retired"].append(o)
                self.decide("decay_rollback", f"{dim} {feat}={v}: {o} stopped winning (vs holdout z={z:.2f}, "
                            f"vs own exploration-time rate z={dz:.2f}, control drift z={dzc:.2f}) — back to "
                            f"{r['control']}", recent_opt=a, recent_ctl=c)
                changed = True
        return changed

    # ------------------------------------------------------------------ hallucination incidents
    def prompt_rates(self):
        out = {}
        for (p, day, hour), nk in self.ing.view("hal", self.id).items():
            t = out.setdefault(p, [0, 0, -10**9])
            t[0] += nk[0]
            t[1] += nk[1]
            t[2] = max(t[2], _minute(day, hour))
        return out

    def reference_flag_rate(self, rates):
        good = [(rate(v), p) for p, v in rates.items() if v[0] >= 2000 and p not in self.st["bad_prompts"]]
        return min(good)[0] if good else 0.015

    def hallucination_check(self, prompt: str):
        """Returns (alarm, stats) for the live prompt over its most recent data."""
        buckets = sorted(((int(d), int(h), nk) for (p, d, h), nk in self.ing.view("hal", self.id).items()
                          if p == prompt), reverse=True)
        n = k = 0
        for d, h, nk in buckets[:12]:
            n += nk[0]
            k += nk[1]
            if n >= 600:
                break
        r0 = self.reference_flag_rate(self.prompt_rates())
        z = z_binom_excess(k, n, r0)
        alarm = n >= HAL_MIN_CONN and k / max(n, 1) >= max(2 * r0, r0 + 0.01) and z > HAL_Z
        return alarm, {"prompt": prompt, "connected": n, "flags": k, "rate": round(k / max(n, 1), 4),
                       "reference_rate": round(r0, 4), "z": round(z, 2)}

    def fallback_prompts(self, current):
        """Prompts that ran here before with a normal flag rate, most recent first."""
        rates = self.prompt_rates()
        r0 = self.reference_flag_rate(rates)
        ok = [(v[2], p) for p, v in rates.items() if p != current and p not in self.st["bad_prompts"]
              and v[0] >= 1000 and z_binom_excess(v[1], v[0], r0) < 2]
        return [p for _, p in sorted(ok, reverse=True)]

    # ------------------------------------------------------------------ rendering the desired config
    def render(self, cur: dict) -> dict:
        cur = canon(cur)
        new = copy.deepcopy(cur)
        roles = []  # (priority, feature, {value: label}, name)
        sch = self.st["schedule"]
        if sch and sch.get("feature") and not self.st["schedule_hold"]:
            roles.append((0, sch["feature"], sch["groups"], "schedule"))
        for dim, r in self.st["dims"].items():
            if r["mode"] == "promoted" and r["feature"] != "*" and r["assign"]:
                roles.append((1 + (r.get("evidence") or {}).get("gain_lb", 0), r["feature"],
                              {v: r["assign"].get(v, r["control"]) for v in self.values_of(r["feature"])}, dim))
        if self.st["cohorts_hold"]:
            roles = []
        while True:
            cells = self._cells(roles)
            if len(cells) <= MAX_COHORTS:
                break
            roles.remove(min(roles, key=lambda x: x[0]))
        kept = {name for *_, name in roles}
        if not self.st["cohorts_hold"]:
            new["cohorts"] = [{"id": cid, "where": where} for cid, where, _ in cells] if len(cells) > 1 else []
            cells = cells if len(cells) > 1 else []
        else:
            cells = [(c["id"], c["where"], {}) for c in cur["cohorts"]]
        allocs = {"default": {}}
        for dim in EXP_DIMS:
            r = self.st["dims"].get(dim)
            cur_def = dist(cur, "default", dim)
            if r is None or r["mode"] in ("hold", "init"):
                allocs["default"][dim] = cur_def
            elif r["mode"] == "explore":
                allocs["default"][dim] = r["explore_alloc"]
            else:
                g = r["assign"].get("*") if r["feature"] == "*" else None
                allocs["default"][dim] = self._with_holdout(g, r["control"])
        allocs["default"]["caller_id"] = dist(cur, "default", "caller_id") if self.st["did"]["hold"] \
            else self.st["did"]["weights"]
        for cid, where, labels in cells:
            a = {}
            for dim in EXP_DIMS:
                r = self.st["dims"].get(dim)
                if r is None:
                    continue
                if r["mode"] == "hold":
                    if dim in cur["allocations"].get(cid, {}):
                        a[dim] = cur["allocations"][cid][dim]
                elif r["mode"] == "promoted" and dim in kept:
                    a[dim] = self._with_holdout(labels[dim], r["control"])
            if "caller_id" in cur["allocations"].get(cid, {}) and self.st["did"]["hold"]:
                a["caller_id"] = cur["allocations"][cid]["caller_id"]
            if a:
                allocs[cid] = a
        new["allocations"] = allocs
        if not self.st["schedule_hold"]:
            hw = {}
            if "schedule" in kept:
                for cid, where, labels in cells:
                    hw[cid] = sch["weights"][labels["schedule"]]
            new["schedule"]["hour_weights"] = hw
        return canon(new)

    @staticmethod
    def _with_holdout(opt, control):
        if not opt or opt == control:
            return {control: 1.0}
        return {opt: 1 - HOLDOUT, control: HOLDOUT}

    def _cells(self, roles):
        by_feat = {}
        for _, feat, mapping, name in roles:
            for v, lab in mapping.items():
                by_feat.setdefault(feat, {}).setdefault(v, {})[name] = lab
        per_feat = []
        for feat, vals in sorted(by_feat.items()):
            groups = {}
            for v, labs in vals.items():
                groups.setdefault(tuple(sorted(labs.items())), []).append(v)
            per_feat.append([(feat, sorted(vs), dict(k)) for k, vs in sorted(groups.items())])
        cells = []
        for combo in itertools.product(*per_feat):
            where = {feat: vs for feat, vs, _ in combo}
            labels = {}
            for _, _, lab in combo:
                labels.update(lab)
            cid = ";".join(f"{feat}={'+'.join(vs)}" for feat, vs, _ in combo)
            cells.append((cid, where, labels))
        return cells or [("default", {}, {})]

    # ------------------------------------------------------------------ estimates for proposals
    def sample_people(self, k=4000):
        out = []
        for f in reversed(self.ing.persons.values()):
            if f[4] == self.id:
                out.append(dict(zip(FEATS, f[:4])))
                if len(out) >= k:
                    break
        return out

    def lift_model(self, dim):
        """Relative engagement of each option vs control, per value of the dim's evaluation feature,
        shrunk towards the workflow-wide estimate (m=200 pseudo-calls)."""
        r = self.st["dims"].get(dim)
        if r is None or r["explore_since"] is None:
            return None, {}
        since = r["explore_since"] // 1440
        feat = r["feature"] if r["feature"] != "*" else "*"
        glob = self.arm_table(dim, "*", since).get("*", {})
        tab = self.arm_table(dim, feat, since) if feat != "*" else {"*": glob}
        g_rate = {o: rate(nk) for o, nk in glob.items()}
        model = {}
        for v, arms in tab.items():
            shr = {o: (arms.get(o, [0, 0])[1] + 200 * g_rate.get(o, 0)) / (arms.get(o, [0, 0])[0] + 200)
                   for o in g_rate}
            c = shr.get(r["control"]) or 1e-9
            model[v] = {o: shr[o] / c for o in shr}
        return feat, model

    def estimate(self, cur: dict, new: dict, people) -> dict:
        """Dry run: replay the population we actually dial against both configs, using lifts measured
        on calls that really happened. Options never tried are assumed equal to control (lift 1)."""
        rel = {}
        for dim in EXP_DIMS:
            feat, model = self.lift_model(dim)
            if not model:
                continue
            tot = {"cur": 0.0, "new": 0.0}
            for f in people:
                m = model.get(f.get(feat, "*") if feat != "*" else "*") or model.get("*") or {}
                for tag, cfg in (("cur", cur), ("new", new)):
                    d = dist(cfg, cohort_of(cfg, f), dim)
                    tot[tag] += sum(w * m.get(o, 1.0) for o, w in d.items())
            if tot["cur"]:
                rel[dim] = tot["new"] / tot["cur"]
        eng = 1.0
        for v in rel.values():
            eng *= v
        h = self.did_health(3 * 1440)
        pk = {}
        for tag, cfg in (("cur", cur), ("new", new)):
            pk[tag] = sum(w * (h.get(o, {}).get("ratio") or 1.0) for o, w in dist(cfg, "default", "caller_id").items())
        changed = sum(1 for f in people if any(
            dist(cur, cohort_of(cur, f), d) != dist(new, cohort_of(new, f), d) for d in ("welcome_message", "voice", "caller_id"))
            or cohort_of(cur, f) != cohort_of(new, f))
        return {"engagement_rel_change": round(eng - 1, 4), "by_dim": {k: round(v - 1, 4) for k, v in rel.items()},
                "pickup_rel_change_from_caller_id": round(pk["new"] / (pk["cur"] or 1) - 1, 4),
                "share_of_people_affected": round(changed / max(len(people), 1), 3),
                "method": "replay of the most recent dialled population against per-segment lifts measured "
                          "on live calls since exploration began (shrunk m=200); untested options assumed neutral"}


def _slim(c):
    return {k: c[k] for k in ("feat", "v", "opt", "z", "lift", "arm", "ctl")}
