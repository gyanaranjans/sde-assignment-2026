"""Pure functions over workflow configs: canonical form, diff, shift, cohort membership.
Mirrors the documented contract (docs/SIMULATOR.md §3/§4.2), not the simulator's code."""
from __future__ import annotations

import copy

AGENT_DIMS = ("welcome_message", "voice", "caller_id")
CHANGE_DIMS = ("cohorts", "welcome_message", "voice", "caller_id", "prompt_version", "schedule")


def _norm(d: dict) -> dict:
    s = float(sum(d.values()))
    return {o: round(w / s, 6) for o, w in sorted(d.items()) if w > 0}


def canon(cfg: dict) -> dict:
    out = copy.deepcopy(cfg)
    out.setdefault("cohorts", [])
    out.setdefault("schedule", {})
    out["schedule"].setdefault("hour_weights", {})
    out["schedule"].setdefault("lead_preferred_hour", {})
    out["allocations"] = {k: {dim: _norm(w) for dim, w in v.items() if w} for k, v in out["allocations"].items()}
    out["schedule"]["hour_weights"] = {k: {str(int(h)): round(float(w), 4) for h, w in t.items()}
                                       for k, t in out["schedule"]["hour_weights"].items()}
    return out


def dist(cfg: dict, cohort: str, dim: str) -> dict:
    return (cfg["allocations"].get(cohort) or {}).get(dim) or cfg["allocations"]["default"][dim]


def changed_dims(old: dict, new: dict) -> list[str]:
    old, new = canon(old), canon(new)
    ch = set()
    if old["cohorts"] != new["cohorts"]:
        ch.add("cohorts")
    keys = set(old["allocations"]) | set(new["allocations"])
    for dim in AGENT_DIMS:
        if any(dist(old, k, dim) != dist(new, k, dim) for k in keys):
            ch.add(dim)
    if old.get("prompt_version") != new.get("prompt_version"):
        ch.add("prompt_version")
    if old["schedule"] != new["schedule"]:
        ch.add("schedule")
    return [d for d in CHANGE_DIMS if d in ch]


def tv(a: dict, b: dict) -> float:
    return 0.5 * sum(abs(a.get(o, 0) - b.get(o, 0)) for o in set(a) | set(b))


def max_shift(old: dict, new: dict) -> float:
    old, new = canon(old), canon(new)
    keys = {"default"} | {c["id"] for c in old["cohorts"]} | {c["id"] for c in new["cohorts"]}
    return max((tv(dist(old, k, d), dist(new, k, d)) for k in keys for d in AGENT_DIMS), default=0.0)


def cohort_of(cfg: dict, f: dict) -> str:
    for c in cfg.get("cohorts", []):
        if all(f.get(feat) in vals for feat, vals in c["where"].items()):
            return c["id"]
    return "default"


def diff(old: dict, new: dict) -> dict:
    """Human-readable diff: only the parts that change."""
    old, new = canon(old), canon(new)
    out = {}
    if old["cohorts"] != new["cohorts"]:
        out["cohorts"] = {"before": [c["id"] for c in old["cohorts"]], "after": new["cohorts"]}
    keys = sorted(set(old["allocations"]) | set(new["allocations"]))
    for dim in AGENT_DIMS:
        per = {k: {"before": dist(old, k, dim), "after": dist(new, k, dim)}
               for k in keys if dist(old, k, dim) != dist(new, k, dim)}
        if per:
            out[dim] = per
    if old.get("prompt_version") != new.get("prompt_version"):
        out["prompt_version"] = {"before": old.get("prompt_version"), "after": new.get("prompt_version")}
    if old["schedule"]["hour_weights"] != new["schedule"]["hour_weights"]:
        out["schedule.hour_weights"] = {"after": new["schedule"]["hour_weights"]}
    return out
