"""Durable state: one SQLite file (WAL). Everything the controller knows or decided lives here, so a
restart resumes from it and the audit trail never depends on the simulator's logs.

Invariant: the ingest cursor (`meta.last_chunk`) and the counter deltas from that chunk commit in the
same transaction, so a chunk is counted exactly once no matter where the process dies."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS counters (key TEXT PRIMARY KEY, n INTEGER NOT NULL, k INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS persons (pid TEXT PRIMARY KEY, f TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS campaigns (cid TEXT PRIMARY KEY, wf TEXT, day INTEGER, leads INTEGER,
                                      version INTEGER);
CREATE TABLE IF NOT EXISTS wf_state (wf TEXT PRIMARY KEY, state TEXT NOT NULL);
-- every proposal we ever made (approval path or auto-apply), written BEFORE the HTTP call
CREATE TABLE IF NOT EXISTS proposals (
  proposal_id TEXT PRIMARY KEY, wf TEXT, path TEXT, status TEXT, approval_id TEXT,
  base_version INTEGER, applied_version INTEGER, dims TEXT, summary TEXT, request TEXT,
  reasons TEXT, created_min INTEGER, updated_min INTEGER, kind TEXT);
-- config versions as observed from config_changed events (who changed what, when)
CREATE TABLE IF NOT EXISTS config_versions (wf TEXT, version INTEGER, actor TEXT, ts TEXT, minute INTEGER,
  dims TEXT, proposal_id TEXT, approval_id TEXT, PRIMARY KEY (wf, version));
-- decision log: every non-trivial thing the controller concluded, with its evidence
CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, minute INTEGER, ts TEXT,
  wf TEXT, kind TEXT, summary TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS loop_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, minute INTEGER,
  wall_s REAL, events INTEGER, ingest_s REAL, decide_s REAL, credits REAL, rss_mb REAL);
CREATE INDEX IF NOT EXISTS ix_dec_wf ON decisions (wf, minute);
"""


class Store:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)

    # -- meta
    def get(self, k, default=None):
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else default

    def put(self, k, v):
        self.db.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                        (k, json.dumps(v)))

    # -- ingestion commit (atomic)
    def commit_chunk(self, chunk_no: int, deltas: dict, persons: list, campaigns: list, versions: list):
        db = self.db
        db.execute("BEGIN")
        try:
            db.executemany("INSERT INTO counters VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET "
                           "n=n+excluded.n, k=k+excluded.k",
                           [("|".join(map(str, key)), v[0], v[1]) for key, v in deltas.items()])
            db.executemany("INSERT OR IGNORE INTO persons VALUES (?,?)", persons)
            db.executemany("INSERT OR IGNORE INTO campaigns VALUES (?,?,?,?,?)", campaigns)
            db.executemany("INSERT OR IGNORE INTO config_versions VALUES (?,?,?,?,?,?,?,?)", versions)
            db.execute("INSERT INTO meta VALUES ('last_chunk',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                       (json.dumps(chunk_no),))
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise

    def load_counters(self) -> dict:
        out = {}
        for key, n, k in self.db.execute("SELECT key, n, k FROM counters"):
            out[tuple(key.split("|"))] = [n, k]
        return out

    def load_persons(self) -> dict:
        return dict(self.db.execute("SELECT pid, f FROM persons"))

    # -- workflow controller state (desired config, experiment phase, ...)
    def wf_state(self, wf) -> dict | None:
        r = self.db.execute("SELECT state FROM wf_state WHERE wf=?", (wf,)).fetchone()
        return json.loads(r[0]) if r else None

    def save_wf_state(self, wf, st: dict):
        self.db.execute("INSERT INTO wf_state VALUES (?,?) ON CONFLICT(wf) DO UPDATE SET state=excluded.state",
                        (wf, json.dumps(st)))

    # -- proposals
    def add_proposal(self, pid, wf, path, kind, base_version, dims, summary, request, minute):
        self.db.execute("INSERT INTO proposals (proposal_id, wf, path, status, base_version, dims, summary, "
                        "request, created_min, updated_min, kind) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (pid, wf, path, "submitting", base_version, json.dumps(dims), summary,
                         json.dumps(request), minute, minute, kind))

    def update_proposal(self, pid, minute, **kw):
        sets = ", ".join(f"{k}=?" for k in kw)
        vals = [json.dumps(v) if isinstance(v, (list, dict)) else v for v in kw.values()]
        self.db.execute(f"UPDATE proposals SET {sets}, updated_min=? WHERE proposal_id=?", (*vals, minute, pid))

    def proposals(self, wf=None, statuses=None) -> list[dict]:
        q, a = "SELECT * FROM proposals WHERE 1=1", []
        if wf:
            q += " AND wf=?"
            a.append(wf)
        if statuses:
            q += f" AND status IN ({','.join('?' * len(statuses))})"
            a += list(statuses)
        cur = self.db.execute(q + " ORDER BY created_min", a)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    # -- decisions / metrics
    def decide(self, minute, ts, wf, kind, summary, **detail):
        self.db.execute("INSERT INTO decisions (minute, ts, wf, kind, summary, detail) VALUES (?,?,?,?,?,?)",
                        (minute, ts, wf, kind, summary, json.dumps(detail, default=str)))

    def metric(self, **m):
        self.db.execute("INSERT INTO loop_metrics (minute, wall_s, events, ingest_s, decide_s, credits, rss_mb) "
                        "VALUES (:minute,:wall_s,:events,:ingest_s,:decide_s,:credits,:rss_mb)", m)
