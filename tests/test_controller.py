"""Controller unit tests: the parts where a silent bug costs a client incident.
Run: python -m pytest -q tests"""
import json

from controller import stats
from controller.brain import Brain
from controller.configs import canon, changed_dims, cohort_of, max_shift
from controller.ingest import Ingestor
from controller.store import Store

BASE = {"cohorts": [], "allocations": {"default": {"welcome_message": {"W1": 1}, "voice": {"V1": 1},
                                                    "caller_id": {"D1": 0.5, "D2": 0.5}}},
        "prompt_version": "P1", "schedule": {"hour_weights": {}, "lead_preferred_hour": {}}}


def test_changed_dims_and_shift_match_contract():
    new = json.loads(json.dumps(BASE))
    new["allocations"]["default"]["caller_id"] = {"D1": 0.3, "D2": 0.3, "D3": 0.4}
    assert changed_dims(BASE, new) == ["caller_id"]
    assert abs(max_shift(BASE, new) - 0.4) < 1e-9
    # weights are normalised, so a rescaled distribution is not a change
    same = json.loads(json.dumps(BASE))
    same["allocations"]["default"]["caller_id"] = {"D1": 2, "D2": 2}
    assert changed_dims(BASE, same) == []


def test_cohort_first_match_wins():
    cfg = {"cohorts": [{"id": "a", "where": {"age_band": ["60+"]}}, {"id": "b", "where": {"lang": ["ta"]}}]}
    assert cohort_of(cfg, {"age_band": "60+", "lang": "ta"}) == "a"
    assert cohort_of(cfg, {"age_band": "18-30", "lang": "ta"}) == "b"
    assert cohort_of(cfg, {"age_band": "18-30", "lang": "hi"}) == "default"


def test_bh_threshold():
    assert stats.bh_threshold([0.1, -1.0, 0.3], 0.1) == float("inf")
    thr = stats.bh_threshold([4.0, 3.0, 0.1, -0.5], 0.1)
    assert 2.9 < thr < 3.0  # both strong tests pass, nulls don't


class FakeApi:
    """Serves pre-baked NDJSON chunks; can fail mid-stream once to exercise the retry path."""

    def __init__(self, chunks, fail_once=None):
        self.chunks, self.fail_once = chunks, fail_once

    def get(self, path, **kw):
        kind, after = kw["kind"], kw["after"]
        return 200, [{"chunk": i, "kind": k} for i, (k, _) in enumerate(self.chunks) if k == kind and i > after]

    def stream_chunk(self, n):
        import httpx
        for i, line in enumerate(self.chunks[n][1]):
            if self.fail_once == (n, i):
                self.fail_once = None
                raise httpx.ReadError("boom")
            yield json.dumps(line)


def _events():
    lead = {"type": "lead", "ts": "2027-01-04T08:00:00+05:30", "workflow_id": "w", "campaign_id": "w:c1",
            "lead_id": "w:l1", "person_id": "w:p1",
            "features": {"lang": "hi", "city_tier": "1", "age_band": "60+", "value_band": "mid"}}
    call = {"type": "call", "ts": "2027-01-04T10:07:00+05:30", "workflow_id": "w", "campaign_id": "w:c1",
            "lead_id": "w:l1", "person_id": "w:p1", "attempt_no": 1, "campaign_day_index": 0, "config_version": 1,
            "cohort_id": "default", "variant": {"welcome_message": "W1", "voice": "V1", "caller_id": "D1",
                                                "prompt_version": "P1"},
            "connected": True, "engaged": True, "disposition": "concluded_interested", "duration_s": 90,
            "turns": 8, "qa": {"hallucination_flag": False, "flow_adherence": 0.9}}
    return [("history", [lead]), ("events", [call, call])]


def _ing(tmp_path, api):
    s = Store(tmp_path / "c.db")
    s.put("d0_date", "2027-01-04")
    return s, Ingestor(s, api, lambda *a, **k: None)


def test_ingest_is_exactly_once_across_restart_and_stream_failure(tmp_path):
    api = FakeApi(_events(), fail_once=(1, 1))
    s, ing = _ing(tmp_path, api)
    n, _, _ = ing.catch_up()
    assert n == 3
    assert ing.counters[("pk", "w", "0")] == [2, 2]
    assert ing.counters[("arm", "w", "voice", "V1", "age_band", "60+", "0")] == [2, 2]
    # restart: a fresh Ingestor over the same DB must not recount anything
    _, ing2 = _ing(tmp_path, FakeApi(_events()))
    assert ing2.catch_up()[0] == 0
    assert ing2.counters[("pk", "w", "0")] == [2, 2]
    assert ing2.persons["w:p1"][:4] == ("hi", "1", "60+", "mid")


def _brain(tmp_path, ing, store, now=600):
    wf = {"workflow_id": "w", "options": {"welcome_message": ["W1", "W2"], "voice": ["V1", "V2"],
                                          "caller_id": ["D1", "D2", "D3"], "prompt_version": ["P1", "P3"]},
          "dialling_window": [9, 21], "objective": {"primary": "engagement_rate",
                                                    "guardrail": {"pickup_rate_max_drop": 0.02}},
          "default_config_snapshot": canon(BASE)}
    return Brain(wf, store, ing, lambda: now, lambda *a, **k: None)


def test_render_respects_supervisor_hold_and_never_touches_prompt(tmp_path):
    s, ing = _ing(tmp_path, FakeApi(_events()))
    b = _brain(tmp_path, ing, s)
    b.start_exploration()
    cur = canon(BASE)
    cur["prompt_version"] = "P3"  # someone else's prompt
    t = b.render(cur)
    assert t["prompt_version"] == "P3"
    assert t["allocations"]["default"]["voice"] == {"V1": 0.6, "V2": 0.4}
    b.on_supervisor_edit({"version": 5, "changed_dims": ["voice"], "ts": None})
    cur["allocations"]["default"]["voice"] = {"V2": 1.0}
    t = b.render(cur)
    assert t["allocations"]["default"]["voice"] == {"V2": 1.0}   # supervisor's choice stands
    assert t["allocations"]["default"]["welcome_message"] == {"W1": 0.6, "W2": 0.4}


def test_promoted_winner_keeps_holdout(tmp_path):
    s, ing = _ing(tmp_path, FakeApi(_events()))
    b = _brain(tmp_path, ing, s)
    r = b.st["dims"]["voice"]
    r.update(mode="promoted", feature="*", assign={"*": "V2"})
    t = b.render(canon(BASE))
    assert t["allocations"]["default"]["voice"] == {"V1": 0.2, "V2": 0.8}


def test_hallucination_alarm_needs_population_not_anecdote(tmp_path):
    s, ing = _ing(tmp_path, FakeApi([]))
    b = _brain(tmp_path, ing, s)
    ing.idx[("hal", "w")] = {("P1", "-3", "10"): [5000, 75],     # reference ~1.5%
                             ("P2", "3", "12"): [40, 4]}         # 10% but only 40 calls
    assert b.hallucination_check("P2")[0] is False
    ing.idx[("hal", "w")][("P2", "3", "13")] = [300, 18]
    alarm, st = b.hallucination_check("P2")
    assert alarm and st["connected"] == 340
    assert b.fallback_prompts("P2") == ["P1"]
