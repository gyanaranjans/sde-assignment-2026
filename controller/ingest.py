"""Event ingestion: chunk → aggregated counters (never raw events).

We keep sufficient statistics (n, k) per key instead of storing 850k events/day; every decision the
controller makes is a function of these counters, so they are also what we persist and replay.

Counter keys (all elements are strings; day = sim day index, may be negative for history):
  pk  wf day                         n=dials        k=connected
  eg  wf day                         n=connected    k=engaged
  arm wf dim opt feat fval day       n=connected    k=engaged     (feat='*' = whole workflow)
  armp wf dim opt day                n=dials        k=connected   (guardrail: does a variant move pickup?)
  did wf did day hour                n=dials        k=connected
  hr  wf feat fval hour              n=dials        k=connected   (time-of-day profile per segment)
  hal wf prompt day hour             n=connected    k=hallucination flags
  coh wf cohort day                  n=connected    k=engaged     (supervisor view, by live cohort id)
  cc  wf campaign_day                n=0            k=leads concluded
"""
from __future__ import annotations

import datetime as dt
import json
import time

FEATS = ("lang", "city_tier", "age_band", "value_band")
EXP_DIMS = ("welcome_message", "voice")


class Ingestor:
    def __init__(self, store, api, log):
        self.store, self.api, self.log = store, api, log
        self.counters: dict[tuple, list] = store.load_counters()
        # (type, wf) -> {rest_of_key: [n, k]} — same list objects as `counters`, so updates show up in both
        self.idx: dict[tuple, dict] = {}
        for key, v in self.counters.items():
            self.idx.setdefault(key[:2], {})[key[2:]] = v
        self.persons: dict[str, tuple] = {p: tuple(f.split("|")) for p, f in store.load_persons().items()}
        self.d0 = dt.date.fromisoformat(store.get("d0_date"))
        self._daycache: dict[str, str] = {}
        self.events_total = 0

    def _day(self, ts: str) -> str:
        d = ts[:10]
        r = self._daycache.get(d)
        if r is None:
            r = self._daycache[d] = str((dt.date.fromisoformat(d) - self.d0).days)
        return r

    def view(self, typ: str, wf: str) -> dict:
        return self.idx.get((typ, wf), {})

    def last_chunk(self) -> int:
        return self.store.get("last_chunk", -1)

    def catch_up(self) -> tuple[int, list, float]:
        """Ingests every chunk after the cursor. Returns (#events, control events, seconds)."""
        t0 = time.time()
        s, chunks = self.api.get("/events/chunks", kind="history", after=self.last_chunk())
        s2, ev_chunks = self.api.get("/events/chunks", kind="events", after=self.last_chunk())
        todo = sorted({c["chunk"] for c in chunks + ev_chunks})
        n_ev, control = 0, []
        for n in todo:
            ne, ctl = self._ingest_chunk(n)
            n_ev += ne
            control += ctl
        self.events_total += n_ev
        return n_ev, control, time.time() - t0

    def _ingest_chunk(self, n: int):
        delta: dict[tuple, list] = {}
        C = self.counters
        persons_new, campaigns, versions, control = [], [], [], []
        P = self.persons

        def inc(key, a, b):
            d = delta.get(key)
            if d is None:
                delta[key] = [a, b]
            else:
                d[0] += a
                d[1] += b

        lines = self.api.chunk_lines(n)
        loads = json.loads
        for line in lines:
            if not line:
                continue
            ev = loads(line)
            t = ev["type"]
            if t == "call":
                wf = ev["workflow_id"]
                ts = ev["ts"]
                day = self._day(ts)
                hour = ts[11:13].lstrip("0") or "0"
                conn = ev["connected"]
                eng = ev["engaged"]
                c, e = int(conn), int(eng)
                var = ev["variant"]
                f = P.get(ev["person_id"])
                inc(("pk", wf, day), 1, c)
                inc(("did", wf, var["caller_id"], day, hour), 1, c)
                for dim in EXP_DIMS:
                    inc(("armp", wf, dim, var[dim], day), 1, c)
                if f is not None:
                    for i, feat in enumerate(FEATS):
                        inc(("hr", wf, feat, f[i], hour), 1, c)
                if conn:
                    inc(("eg", wf, day), 1, e)
                    inc(("coh", wf, ev["cohort_id"], day), 1, e)
                    inc(("hal", wf, var["prompt_version"], day, hour), 1, int(ev["qa"]["hallucination_flag"]))
                    for dim in EXP_DIMS:
                        o = var[dim]
                        inc(("arm", wf, dim, o, "*", "*", day), 1, e)
                        if f is not None:
                            for i, feat in enumerate(FEATS):
                                inc(("arm", wf, dim, o, feat, f[i], day), 1, e)
                if eng:
                    inc(("cc", wf, str(int(day) - ev["campaign_day_index"])), 0, 1)
            elif t == "lead":
                pid = ev["person_id"]
                if pid not in P:
                    fe = ev["features"]
                    tup = tuple(fe[k] for k in FEATS) + (ev["workflow_id"],)
                    P[pid] = tup
                    persons_new.append((pid, "|".join(tup)))
            elif t == "campaign_created":
                campaigns.append((ev["campaign_id"], ev["workflow_id"], int(self._day(ev["ts"])), ev["leads"],
                                  ev["config_version_at_creation"]))
            elif t == "config_changed":
                day = int(self._day(ev["ts"]))
                minute = day * 1440 + int(ev["ts"][11:13]) * 60 + int(ev["ts"][14:16])
                versions.append((ev["workflow_id"], ev["version"], ev["actor"], ev["ts"], minute,
                                 json.dumps(ev["changed_dims"]), ev.get("proposal_id"), ev.get("approval_id")))
                control.append(ev)
            elif t == "approval_decided":
                control.append(ev)
        self.store.commit_chunk(n, delta, persons_new, campaigns, versions)
        for k, v in delta.items():
            cur = C.get(k)
            if cur is None:
                C[k] = v
                self.idx.setdefault(k[:2], {})[k[2:]] = v
            else:
                cur[0] += v[0]
                cur[1] += v[1]
        return len(lines), control
