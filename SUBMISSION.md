# Outcome-First Agent Loop — Submission

**Name:** Gyanaranjan Sahoo · **Date:** 2026-11-07 · **Hours actually spent:** 4 · **Language / storage:** Python 3.11+ (httpx, stdlib `sqlite3`, `statistics`) / one SQLite file (WAL)

---

## 0. TL;DR
- `./run.sh <SIM_URL>` starts an always-on controller that ingests every event chunk into durable counters, learns per workflow (caller-ID health, call-time per cohort, welcome × voice experiments with decay rollback, hallucination incidents), renders a *desired* config, and reaches it only through `/approvals` or policy-permitted auto-apply.
- public_01: **conclusion +13.8%**, pickup +8.0%, engagement +4.5% vs no-op; 18 changes (10 beneficial / 6 neutral / 2 harmful), 0 credits.
- public_02: **conclusion +28.9%**, pickup +23.8%, engagement +1.8%; flagged caller ID out within a day; supervisor's bad prompt fixed in **150 min** (5 credits); 0 lapsed, 0 expired.
- public_03 (load, 840k events/day): ~0.7 s wall per sim hour, **~9.5 s per sim day** including the simulator, 214k events/s ingest, 248 MB peak RSS. Kill -9 ×10 mid-run → identical final outcome to an uninterrupted run.
- Deliberately not built: per-person call time, proactive prompt optimisation, cross-tenant credit/attention fairness (C15), an HTTP UI.

## 1. Assumptions
1. The API contract (docs/SIMULATOR.md) is the only truth. The controller never reads `runs/`, `sim/` or scenario files. (`scripts/verify_crash.py` reads `runs/` — it is a test harness, not the controller.)
2. Welcome message and voice are heard *after* pickup, so they can only move engagement; pickup is moved by caller ID and call time. For a pickup-primary workflow (KYC) the A/B tests therefore optimise its guardrail KPI (engagement), which is allowed and raises conclusions.
3. `guardrail.pickup_rate_max_drop: 0.02` is read as **absolute** (2 percentage points). A variant whose pickup is lower by more than that with z < −2 is vetoed.
4. Caller-ID pickup also degrades with how heavily a number is used (observed in data: the DID that carried 100% of KYC history picks up ~60% as well as fresh ones). I treat "over-used" and "flagged" differently — see §5.
5. A supervisor edit expresses intent for the dims it touched. The controller stops managing those dims for the rest of the run (they become `hold`) instead of re-proposing over them.
6. Kestrel ops' "two requests a day, never before 11:00" is applied to *every* workflow as a routine-request ceiling. Incidents may use the policy's remaining quota and start at approver-hours open.
7. "Credits are expensive in production": the LLM is only called when no previously-safe prompt exists for the workflow.
8. Approver identity: the API only exposes the decision, not a person; the audit records "{workflow} client approver" plus decision time. In production this would be the approver's user id.
9. One controller process per deployment; SQLite is single-writer. Horizontal scale = shard workflows across processes (§8).

## 2. Scope decisions
| # | Capability | Built / Partial / Cut | Why | What it would take to finish |
|---|---|---|---|---|
| C1 | Ingestion at load scale | **Built** | Everything depends on it | — (Postgres for multi-process, §8) |
| C2 | Shared cohort object | **Built** | Product memo; detection/experiments/view use the same cohort ids written into config | Cohort registry table with versioned definitions |
| C3 | Bootstrap from history | **Built** (partial use) | Hour-of-day profiles + caller-ID baselines + hallucination reference rate come from history; variant effects can't (history is single-variant) | — |
| C4 | Call-time optimisation | **Partial: per cohort** | Per-person data is 1–3 connects per person: noise. Cohort profiles from 14 days of history are tight | Per-person with hierarchical shrinkage toward cohort profile; requires returning-person volume |
| C5 | Caller-ID health | **Built** | Biggest pickup lever (+11–38% pickup) and a regulated-risk item | Carrier spam-API signal instead of inference |
| C6 | Welcome/voice experiments | **Built** | Primary engagement lever | Adaptive (Thompson) allocation where governance allows frequent changes |
| C7 | Winner promotion, decay, rollback | **Built** | Decaying winners exist in both public scenarios | Faster decay detection via sequential tests |
| C8 | Hallucination incident response | **Built** | Compliance-critical; MTTR is graded | Engagement-aware fix selection beyond "last known good" |
| C9 | Governance client | **Built** | Constraint #1 | Approver-specific latency modelling |
| C10 | Impact, affected leads, dry run | **Built (honest, approximate)** | Product surface | Off-policy replay with propensities per call |
| C11 | Supervisor view | **Partial: Markdown, one screen per workflow** | Ops asked for one screen; no UI framework to defend | Small read-only HTTP page over the same query |
| C12 | Audit store | **Built** | Floor + the live review | — |
| C13 | Crash recovery, demonstrated | **Built** | SRE non-negotiable | — |
| C14 | Loop observability | **Partial** | `loop_metrics` table + `audit health`; alerts as decisions with severity | Export to Prometheus, real paging |
| C15 | Fairness across tenants | **Cut** | Credits never ran short (5/200, 0/400 used); per-workflow approval caps already bound attention | Global credit scheduler with per-tenant budgets |

Principle: build the loop end-to-end on the levers the data can actually move (caller ID, call time, two agent dims, incidents), make every step auditable and crash-safe, and cut anything whose value depends on data we don't have (per-person timing) or problems we don't have yet (credit contention).

## 3. Stakeholders

### 01 — Nikhil Rao, Sales
- **Accepted:** per-client lift numbers exist (grade + `audit health`/view KPIs), reported per KPI.
- **Declined:** a single blended score (Product forbids blending pickup and engagement); "winning by lunch, live by evening" (a lunch-time lead at ~150 calls/arm is mostly noise — min 1.5 days + BH-controlled tests); pushing winners straight through auto-apply; rolling out the persuasive prompt.
- **Wrong claims:** "Kestrel has auto-apply for everything" — `ks_cancel` allows only `schedule, caller_id, welcome_message, voice` with `max_shift 0.2`; no prompt, no cohorts. "Northwind is fine with auto-apply on KYC" — `nw_kyc.auto_apply.enabled = false`, and Northwind's own Compliance memo says the opposite. The "persuasive" prompt (P4) is described as "freer… richer benefit explanations": exactly the hallucination risk Compliance is worried about.
- **Reply:** "Nikhil — you'll get a per-client number for the summit: e.g. on our test workload conclusions rose 14–29% vs the untouched agent, with pickup and engagement shown separately (Product won't let us blend them, and neither would a compliance head). It won't be 'by evening': a variant needs ~1.5 days of real calls before we trust it, and customer-facing changes go through each client's approver — that is what lets us say 'every change was approved and explainable' on stage. Kestrel's auto-apply only covers small shifts, not 'everything', and Northwind KYC has none. We won't roll out the persuasive prompt: it's the riskiest one for hallucinations."

### 02 — Meera Iyer, Compliance (Northwind)
- **Accepted, all five:** customer-facing changes (welcome, voice, prompt) always go through approval — and I extend that voluntarily to Kestrel even though its policy allows auto-apply for welcome/voice. Prompt changes carry `evidence.validation_run_id` (validated n=500). Operational auto-applies (caller ID, schedule) are logged with rationale and appear in the view (weekly summary = the view + `audit timeline`). The "why did this customer hear X" question is a command: `python -m controller.audit heard <wf> <person_id> "D3 14:00"` → cohort, allocation, config version, who approved it and the evidence. Requests only inside 10–19 (KYC 10–18), and routine ones from 11:00.
- **Pushback:** none on substance. Exploration itself means a minority of customers hear an untested variant; that exploration is itself an approved request with a diff and a dry run, and arms that do clearly worse are dropped early.
- **Claims vs policy:** correct — her memo matches `governance` exactly.
- **Reply:** "Meera — agreed on all five. Nothing your customers hear (opening line, voice, script) changes without your approver's yes; script changes include a validation run id. Call timing and caller-ID rotation use the latitude in your signed policy and are listed, with reasons, in the weekly summary. For auditors, we can answer 'why did customer X hear variant Y on day Z' from our own records in minutes, not 24 hours: the variant, the cohort, the approval and the evidence behind it. Tests start on a minority of calls and are stopped early if an arm underperforms."

### 03 — Kabir Menon, Platform & SRE
- **Accepted:** kill/restart test (`make crash-test`: 10 random kill -9s, invariants checked, final outcome identical to an uninterrupted run); idempotency keys on every proposal written *before* the call; loop health without logs (`audit health`, `loop_metrics`); throughput: one sim day of public_03 in ~9.5 s wall (≪ 10 min).
- **Pushback:** "no new stateful infra" — I used an embedded SQLite file, not a server. Nobody operates it at 2 a.m.: it's a file on the controller's volume; losing it means relearning from the simulator's chunk history. For production I'd move the same schema to the existing Postgres (tables are plain `INSERT … ON CONFLICT`), which also unlocks multiple controller processes.
- **Alerts:** only pages that need a human now (hallucination incident opened, incident with no viable fix, credits below the floor, a workflow step throwing). DID rotation, experiment results, approval rejections are not pages.
- **Reply:** "Kabir — kill/restart is demonstrated, not promised: `scripts/crash_test.sh` kill -9s the controller 10 times at random points; it resumes from its own store, counts every event exactly once, never double-applies or loses an approval, and finishes with the same outcome as an uninterrupted run. Load: one simulated day of public_03 takes ~10 s wall (sim included), 214k events/s ingest, 250 MB RSS. State is one SQLite file — no new server; it ports straight to our Postgres when we run more than one controller. It pages only for open hallucination incidents, credits below floor, or a crashing workflow loop."

### 04 — Anita Desai, Ops Supervisor (Kestrel)
- **Accepted:** her prompt edits are never undone silently — her dim goes to `hold`; if her prompt is hallucinating, the controller *asks her approver* to revert, with evidence. Requests batched (one request carries every pending change), ≤2 routine per day, never before 11:00. One screen per workflow (`supervisor_view.md`). The "word-for-word" fix: the incident path prefers the last prompt that ran on *her* workflow with a normal flag rate (it has real engagement history), not the strictest prompt the LLM offers.
- **Pushback:** "tell me before anything changes on a running campaign" — customer-facing changes are always asked first; caller-ID rotation when a number is being spam-flagged is applied immediately (policy permits it, and waiting costs pickup) and shows in her view afterwards.
- **Wrong claims:** her P2 edit is exactly what hallucinated (5% flags vs 1.6%) — "that's my job" still needs a safety net. Policy allows Kestrel 4 requests/day; her ask of 2 is stricter, and we honour hers.
- **Reply:** "Anita — we won't undo your edits behind your back: anything you change, we stop touching. If one of your prompt changes starts hallucinating, you'll get one request to revert it with the numbers attached — you or your approver decide. You'll get at most two routine requests a day, none before 11:00, each bundling everything we want. Your one screen shows what's being tested, what's winning, what's waiting for you and what changed. The one thing we change without asking is the outbound number when it's getting spam-flagged; you'll see it on the screen."

### 05 — Tara Kulkarni, Product
- **Accepted:** separate KPIs everywhere; one cohort object (ids like `age_band=46-60+60+` are written into the config, and the same ids appear in call events, experiment results and the view); proposals carry impact, diff, affected leads, dry run; expired/rejected/stale ⇒ re-proposed later from fresh data under a new id; learning only from calls that actually ran (variant is read from each call event; an experiment's clock starts when its allocation is *observed live*, not when proposed).
- **Minimum size / window, answered:** ≥150 connected calls per arm per segment, ≥1.5 days, BH q=0.10 across all segment×option tests of a dimension plus z ≥ 2.5 per test, max 5 days. Cost: small segments (e.g. a 15% age band with a 1.2× effect) often don't reach significance within 5 days — we leave lift on the table rather than ship false winners.
- **Pushback:** three P0s — I built caller-ID health and the consolidated view; per-person call-time I replaced with per-cohort call-time (per-person data is 1–3 connects per person, the estimate would be noise).
- **Reply:** "Tara — pickup/engagement are never blended, cohorts are one object with one id everywhere, and every proposal has impact/diff/affected/dry-run. My answer on sizing: 150 connected calls per arm per segment, at least 1.5 days, false-discovery control across segments; the price is that small segments with modest effects often don't resolve within a week. Caller-ID health and the one-screen view are done. Per-person call time isn't: one person gives us 1–3 answered calls, so I shipped per-cohort call time from 14 days of history instead. Per-person is the next step once returning-person data accumulates."

### 06 — Aryamann, CTO
- **Accepted:** explainability over raw lift (every change links to decision rows with numbers), "best compute is no compute" (5 credits used across all public runs; the LLM is skipped when a known-safe prompt exists), scale-first design (§8), explicit choices (§2).
- **Reply:** "I chose the levers the data can move and made each one auditable: caller-ID health, cohort call-time, welcome×voice tests with decay rollback, and hallucination response — all through the governance boundary and crash-safe. I said no to per-person timing, prompt optimisation and cross-tenant fairness, and told Sales no on speed and the persuasive prompt. What breaks first at 10× is single-process ingestion; partition by workflow (§8)."

## 4. Architecture

```
            ┌──────────────────────────── controller process (python -m controller) ───────────────────────────┐
 sim ──────▶│ Ingestor: stream chunk → counters Δ ──(1 txn: Δ + persons + cursor)──▶ SQLite (WAL)              │
 /events    │                                                    counters, persons, campaigns, config_versions │
            │ per workflow, every sim hour:                                                                    │
            │   reconcile proposals ◀──────── GET /approvals, /config/history                                  │
            │   Brain: update_did · init_schedule · start_exploration · evaluate (winners/decay) · incidents   │
            │     └─ state (roles, evidence) ──▶ wf_state ; every conclusion ──▶ decisions                     │
            │   render(desired, live config) → target                                                          │
            │   Governance: operational part → PUT /config (≤max_shift)  |  rest → POST /approvals → apply     │
            │     └─ proposal row written BEFORE the HTTP call ──▶ proposals                                   │
            │   POST /sim/advance 60 ── loop_metrics                                                           │
            └───────────────────────────────────────────────────────────────────────────────────────────────────┘
 CLIs over the same DB: controller.audit (why / heard / timeline / health), controller.view (supervisor screen)
```

Code map: `controller/ingest.py` (counters), `brain.py` (learning + desired config), `loop.py` (loop + governance client + incidents), `configs.py` (diff/shift/cohort helpers), `store.py` (schema), `audit.py`, `view.py`, `stats.py`.

**On restart:** counters and the persons map are loaded from SQLite; ingestion resumes after `meta.last_chunk`; per-workflow learning state (`wf_state`) is loaded; proposals in `submitting` are reconciled against `GET /approvals` (approval path) or config history (auto path); `pending/approved` continue. Supervisor edits are detected from the persisted `config_versions` table, not from in-flight events, so none are missed.

## 5. The learning loop
- **Cohorts.** One partition, built from *roles*: call-time (feature with the most distinct hour-of-day pickup profile, values grouped by which third of the window they peak in) and each promoted experiment (feature whose per-value winners give the largest lower-bound gain, values grouped by winning option). The cohort set is the cross-product of grouped values (≤20; lowest-value role dropped if exceeded), so cohorts only split where a role needs a different treatment — no fragmentation by default. Minimum evidence is per arm per segment (150 connected), not per cohort.
- **Experiment design.** Factorial: welcome and voice randomised independently in the same calls, so each dimension's marginal comparison is unbiased and gets the full traffic; interaction is averaged over the other dim's mix (not estimated — see weaknesses). Control keeps 40% (60% when there are only 2 options); others split the rest. Arms clearly worse overall (z < −2.5) are dropped early (successive elimination) to cap exploration harm.
- **Deciding a winner.** Deterministic two-proportion z-tests of every option vs control in every segment value of every feature (and overall). Benjamini–Hochberg at q=0.10 across that family, plus z ≥ 2.5 and lift ≥ 3% per test, plus the last-day lift must still be positive, plus the pickup guardrail. Among features, pick the one with the largest share-weighted 80%-lower-bound gain. Earliest at 1.5 days of live exploration, latest 5 days (then: winners if any, else back to control). The LLM has no role here.
- **Decay and rollback.** Winners ship at 80% with a 20% control holdout, so they stay measurable. Each evaluation compares the winner to the holdout over the last ~2 days (rollback if z < −2) and to *its own exploration-time rate* while control is stable (rollback if that drop is z < −3 and the winner isn't still clearly ahead). Observed: public_01 W3 decay rolled back ~2 days after onset; public_02 ~2 days.
- **Call time and caller ID.** History gives per-segment pickup by hour; weights = (pickup index)² clipped to [0.25, 4] — relative weights only reorder *who* is dialled in an hour and never zero an hour (no wasted capacity). Caller IDs: untested DIDs get a probe share (≤10% each, ≤15% total), graduate only after 300 dials with ratio ≥ 0.9 to peers; a DID whose pickup is < 70% of peers dialled in the same hours (8 h, z < −4) or < 80% over 3 days (z < −3.5) is rested for 6/12/24 days (then re-probed) — unless it is simply over-used (its 3-day volume > 1.25× any peer's), which even spreading fixes. Healthy DIDs share load equally (pickup falls with per-number volume).
- **Guardrail.** For engagement-primary workflows a variant whose pickup drops more than `pickup_rate_max_drop` (z < −2) is vetoed. Caller-ID and schedule changes move pickup but not engagement; for the pickup-primary KYC flow, experiments move engagement (its guardrail) only upward by construction (winners must beat control).

## 6. Governance
```
            ┌─(crash)─▶ reconcile: found → pending/… ; not found → abandoned
 submitting ┤
            └─ 200 ─▶ pending ─┬─ approved ─┬─ apply 200 ─▶ applied
                               │            ├─ 409 stale ─▶ stale ──▶ re-render from fresh data, new id
                               │            ├─ 410 ─────▶ lapsed ──▶ same
                               │            └─ held while an incident fix is pending (fix wins)
                               ├─ rejected ─▶ backoff 3h ─▶ re-propose with fresh data
                               └─ expired  ─▶ treated as rejection (same)
```
- One routine and one urgent approval in flight per workflow; routine ≤ min(policy−1, 2)/24h, 11:00 ≤ t < close−1h; urgent may use the reserved slot from approver-hours open. Everything pending is batched into one request (the target config is the full desired state).
- **Auto-apply** is used only for `caller_id` and `schedule` (∩ policy), stepped so TV ≤ `max_shift`, and never while an approval is in flight (it would stale it) — except to pull a failing caller ID. Customer-facing dims always ask a human, even on Kestrel.
- **Impact / affected / dry run.** Dry run replays the 4,000 most recent people actually dialled against current vs proposed config using per-segment lifts measured on live calls since exploration began (shrunk toward the workflow-wide lift, m=200); untried options count as neutral. Impact = clip(estimated primary-KPI gain / 10%) — exploration proposals honestly score ~0.05; incident fixes 0.9. Affected leads = active leads × share of sampled people whose cohort/allocation changes. It is an estimate, not a promise, and says so in the `method` field.
- **Supervisor edit mid-experiment:** dims they touched go to `hold`; pending approvals fail `stale_base_version` and are re-rendered on top of their config; experiment data already collected stays valid (variants are per call).

## 7. Incidents
- **Hallucination:** live prompt's flag rate over its last ≤12 hourly buckets (≥150 connected) vs the lowest flag rate of any prompt with ≥2,000 historical calls. Alarm if rate ≥ max(2×ref, ref+1pt) and binomial z > 3.5 (≈2×10⁻⁴ one-sided per check; 0 false alarms across public_01/03 = 15 workflow-runs).
- **Fix:** last prompt that ran on this workflow with a normal rate (no LLM credits); else `/llm/suggest-fixes` (3 credits) and validate candidates. Validate n=500 (5 credits) against `min_validation_pass_rate`. Credits floor: keep 15% of the budget for the next incident.
- **Verify:** after ≥300 connected calls on the fix, same test; failure reopens with the fix marked bad.
- **public_02 timeline (ks_cancel):** D3 11:30 supervisor ships P2 → 12:00 edit recorded, prompt dim → hold → **13:00** alarm (5.0% flags vs 1.6%, 161 calls, z=3.5), page, P1 validated 0.986, urgent request → **14:00** approved & applied (grader MTTR **150 min**) → routine promotion requested after → 18:00 verified (1.1% over 347 calls). Caller ID D2 flagged D2 10:00 → rested D2 12:00 (8h window, 61% of peers), stepped out under max_shift 0.2 by 13:00; share 9.5% on D2, 0% on D3–D7.

## 8. Scale (public_03_load, M-series laptop, single process)
| metric | value |
|---|---|
| history bootstrap | 5.54 M events in 24 s |
| steady ingest | 214k events/s (streamed, 840k events/sim-day) |
| wall per sim hour (ingest + decide + sim advance) | 0.69 s mean, 1.2 s max |
| wall per sim day | ~9.5 s (SRE target: 600 s) |
| decide time per step (12 workflows) | 27 ms mean, 240 ms max |
| decision lag | ≤ 1 sim hour (step size) + <1 s wall |
| peak RSS | 248 MB (2M people interned) |
| SQLite size | 156 MB |

**At 10×** (≈8.4M events/day, ~2.5k workflows' worth of people): first to break is single-threaded JSON parsing (~40 s/day — still fine) and then the in-memory person map (~2.5 GB) and SQLite's single writer. Plan: partition by workflow (hash of `workflow_id` → controller shard; every decision is per-workflow already, the only shared resources are credits and the event feed), move state to Postgres, replace the person map with a features column joined at ingest from a lead table. Credits become a small central budget service (C15).

## 9. Reliability
- **Test:** `make crash-test` → `scripts/crash_test.sh` kill -9s the controller at 10 random sim times (with random sub-second jitter so kills land mid-ingest, mid-proposal, mid-apply), restarts it, then `scripts/verify_crash.py` checks: dials counted exactly once (ours == ground truth per workflow), no approval applied twice, every approved request applied or provably stale, no duplicate proposal ids. All pass; the grade equals the uninterrupted run (`reports/crash_recovery.log`, `reports/crash_recovery_grade.md`).
- **What can be lost:** a decision-log line may be written twice if killed between `decide()` and `save()`; a validation run (5 credits) may be repeated if killed between the call and persisting its id. No config, approval or counter state is lost.
- **Idempotency:** `proposal_id = ctl-{scenario}-{wf}-{kind}-{minute}-{seq}`, stored before sending; `409 duplicate_proposal_id` triggers reconciliation. Chunk cursor + counter deltas commit atomically.
- **HTTP errors:** transport errors and 5xx retried with backoff; 4xx are answers (409 stale → re-propose; 410 → lapsed; 403 auto-apply → logged + alert; 402 → incident path pages).

## 10. Audit & observability
```
$ python -m controller.audit --db state/public_02/controller.db why ks_cancel "D3 21:00"
→ live_version 10, changed D3 16:00 by controller_approved; proposal apr_00009 "3 cohorts; voice: promote winners;
  call-time weights per cohort", proposed D3 14:00, approved 15:39 by ks_cancel's approver; impact 0.07,
  affected 3,583, dry run +0.2% engagement; evidence: decision #58 "voice: age_band=31-45→V4 (+26.3%, z=2.80)";
  KPIs D2..D5; next 48h: D3 18:00 incident resolved; D5 11:00 decay_rollback V4 (vs own exploration-time rate
  z=−3.77) → applied D5 13:00.
```
Full output: `reports/audit_example.txt`. Also `audit heard <wf> <person> <time>` (what a customer heard and why), `audit timeline <wf>`, `audit health` (`reports/public_03_loop_health.json`).
- **Page:** hallucination incident opened; incident with no viable fix; credits below floor; a workflow step throwing; (prod) ingest lag > 2 steps.
- **Don't page:** caller-ID rotation, experiment results, approval rejections/expiries, stale applies — they're in the view.

## 11. Results
| scenario | conclusion Δ | pickup Δ | engagement Δ | gap captured | changes (ben/neu/harm) | credits |
|---|---|---|---|---|---|---|
| public_01 | +13.8% | +8.0% | +4.5% | 21% | 10 / 6 / 2 | 0 |
| public_02 | +28.9% | +23.8% | +1.8% | 24% | 15 / 6 / 3 | 5 |
| public_03 | +20.9% | +11.6% | +6.0% | 35% | 37 / 19 / 0 | 0 |

Per-workflow tables: `reports/public_0{1,2,3}.md`.
- **Helped:** caller-ID load spreading + flagged-number rotation (KYC pickup +11–23%); call-time cohorts; decaying W3 caught and rolled back; incident fix in 150 min, hallucinated calls 202 vs 431.
- **Harmful (5 over two scenarios) and why:** (a) *ending* an experiment with no significant winner reverts to control, which by truth was slightly worse than the exploration mix (ks_cancel W2 for tier-1 is a 6% effect on 20% of people — under-powered) ×2; (b) probing/re-admitting a flagged caller ID (renewal D4, ks_cancel D2) ×2 — the price of being able to recover a number; (c) KYC exploration of a welcome message that is 8% worse (W2) — exploration cost on a 2-option dim.
- **One false winner:** public_02 ks_cancel promoted V4 for age 31-45 (+26%, z=2.8) — no such effect exists. The 20% holdout + drift check rolled it back two days later (D5 11:00). That's the cost of q=0.10; the holdout is what makes it recoverable.
- **KYC engagement −2.7% in public_02:** `next_campaign_only` locks the exploration allocation per campaign for 3 days, so the bad arm keeps running after it's dropped. I'd use smaller exploration shares on locked workflows.

## 12. Trade-offs & rejected alternatives
| Option | Why considered | Why rejected / what instead |
|---|---|---|
| Thompson sampling per segment | More lift, less regret | Each reallocation needs an approval (2/day) or tiny auto-steps; fixed-split tests + one promotion fit governance |
| Bonferroni | Strict FWER | No power across ~40 correlated tests; BH + per-test floor z≥2.5 |
| Per-person call time | Product headline | 1–3 connects/person: noise; per-cohort from 14 days of history |
| Auto-apply welcome/voice on Kestrel | Policy allows | Customer-facing; Ops asked to be told first; approvals are cheap there |
| Postgres/Redis | SRE already runs them | Embedded SQLite keeps `run.sh` dependency-free; schema ports as-is |
| Raw event store | Full replay | 850k events/day; counters are the sufficient statistics for every decision |
| LLM-first incident fix | Obvious path | Reverting to the last known-good prompt is cheaper and has real engagement history |

## 13. Known weaknesses
- Welcome×voice interactions are not estimated; cohorts are cross-products of ≤2–3 grouped features.
- Under-powered segments: small cohorts with ~5–10% effects rarely resolve; "no winner → back to control" can lose real lift.
- Caller-ID "over-use vs flagged" is a heuristic (volume parity), not a model of load; re-probing a truly flagged number costs a little pickup.
- Supervisor `hold` is permanent for the run; a supervisor who edits once loses automated optimisation of that dim.
- Prompt choice in incidents doesn't consider engagement beyond "previously ran here"; with LLM-only candidates it could pick the strict script Anita fears.
- Decision rows may duplicate after a crash; validation credits may be spent twice.
- No HTTP view; Markdown file + CLI only.
- Tuned constants (thresholds, shares) were set on two public scenarios — conservative choices, but still a risk on hidden ones.

## 14. If this went to production at Formi next month
1. Move the store to the existing Postgres; shard controllers by workflow; add a lead/feature table instead of the in-memory map.
2. Shadow mode for a week on 3 workflows: render desired configs and dry runs, send nothing; compare to what supervisors actually do.
3. Wire alerts to PagerDuty for the four page conditions; dashboard from `loop_metrics`.
4. Replace DID inference with the carrier's spam-reputation feed where available; keep inference as fallback.
5. Off-policy evaluation with per-call propensities so dry runs and impact scores are calibrated; review calibration weekly.
6. Small HTTP supervisor view with approve/decline deep-links (the approver UI is the client's, the context should be ours).
7. Then per-person call time, once returning-person histories are deep enough.

---
### How to run
```
make setup                      # venv + requirements
make all-public                 # fresh sim + controller + grade for public_01/02/03 → reports/
make crash-test                 # kill -9 ×10, verify invariants
make test                       # unit tests (sim + controller)
./run.sh http://127.0.0.1:8700  # against any running simulator; state in $CTRL_STATE (default ./state)
python -m controller.view  --db state/public_02/controller.db
python -m controller.audit --db state/public_02/controller.db why ks_cancel "D3 21:00"
```
