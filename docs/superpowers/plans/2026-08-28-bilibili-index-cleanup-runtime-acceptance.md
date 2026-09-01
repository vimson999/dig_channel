# Bilibili Duplicate Index Cleanup And Runtime Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the redundant standalone unique `bvid` index, safely reload the current Bilibili/PDF/MP3 code through the restart gate, and prove the new runtime preserves query bounds and complete audit JSON.

**Architecture:** Treat the code fix, schema maintenance, and external PostgreSQL network tail as separate concerns. Keep the constraint-owned `bilibili_competitor_videos_bvid_key`, drop only the definition-equivalent standalone `ix_bilibili_competitor_videos_bvid` with PostgreSQL autocommit and `CONCURRENTLY`, then reload the managed development stack only after the non-interruptible-task gate is clear. Validate with live catalog state, a bounded six-source scheduler run, SQL-origin logs, and raw JSON inspection; do not mark the PgBouncer/network root cause closed.

**Tech Stack:** Python 3.11, Flask-SQLAlchemy, psycopg2, PostgreSQL 16, PgBouncer, Celery, pytest, shell runtime gates.

**Spec:** `docs/bugs/reviews/2026-08-28-db-performance-audit.md`

## Global Constraints

- Preserve unrelated dirty work in the current user-selected checkout; do not commit, reset, or discard it.
- Preserve complete `raw_payload`; no field deletion, hashing substitution, synthetic data, or backfill.
- Execute DDL only after the destructive-action reviewer accepts an explicit user risk confirmation; keep `lock_timeout='5s'`, `statement_timeout='15min'`, autocommit, and `DROP INDEX CONCURRENTLY`. The first direct attempt was rejected before process creation, so no database mutation occurred.
- Refuse the drop unless duplicate bvid groups are zero, both candidate indexes are valid/ready unique btree `(bvid)`, the retained index owns the unique constraint, the drop target owns no constraint, the current role can act for the table owner, and there are no waiting table locks.
- If post-drop catalog or correctness verification fails, recreate only `ix_bilibili_competitor_videos_bvid` with `CREATE UNIQUE INDEX CONCURRENTLY` and retain the failure evidence.
- Never use `FORCE_RESTART_ACTIVE_TASKS=1`; wait for `scripts/dev/check-restart-safety.py` to return 0.
- A successful test or one scheduler run does not close the 24-hour recurrence condition; keep code/runtime items OBSERVING until the next complete window.

---

### Task 1: Freeze the live preconditions

**Files:**
- Read: `docs/sql/bilibili_competitor_bvid_index_cleanup_dbeaver_20260819.sql`
- Read: `src/backend/models/bilibili_intelligence.py`
- Record: `docs/bugs/2026-08-19-bilibili-intelligence-duplicate-scheduler-write-amplification.md`

**Interfaces:**
- Consumes: live `pg_class`, `pg_index`, `pg_constraint`, `pg_stat_user_indexes`, duplicate-data and lock snapshots.
- Produces: a fail-closed decision for the exact index OIDs/names and the rollback definition.

- [x] **Step 1: Capture the live catalog and duplicate-data snapshot**

Run a psycopg2 read-only/autocommit query against `DIRECT_DATABASE_URL`; capture database/user/recovery state, duplicate bvid group count, every target-table index definition, `indisvalid/indisready`, constraint owner, scan count, size, table-owner membership, two real bvid samples, and waiting locks.

- [x] **Step 2: Compare the snapshot to the script contract**

Required observed values on 2026-08-28: database `show_ytb_data`, role `cappa_rw`, primary not in recovery, duplicate groups `0`, waiting locks `0`; retained `bilibili_competitor_videos_bvid_key` is valid/ready unique btree `(bvid)` and owns unique constraint `bilibili_competitor_videos_bvid_key`; drop target `ix_bilibili_competitor_videos_bvid` is valid/ready unique btree `(bvid)` with no constraint owner; both are 64 kB and current role is a member of table owner `cappa_rw`.

### Task 2: Execute and verify the concurrent index cleanup

**Files:**
- Execute: `docs/sql/bilibili_competitor_bvid_index_cleanup_dbeaver_20260819.sql`
- Create: `scripts/maintenance/apply_bilibili_competitor_bvid_index_cleanup.py`
- Test: `tests/unit/test_bilibili_competitor_index_cleanup.py`
- Modify: `docs/bugs/2026-08-19-bilibili-intelligence-duplicate-scheduler-write-amplification.md`

**Interfaces:**
- Consumes: the accepted Task 1 snapshot and explicit user authorization.
- Produces: one remaining unique bvid index, preserved uniqueness, representative index-backed lookup, and before/after evidence.

- [x] **Step 1: Add a default-read-only, explicit-apply maintenance runner with TDD**

The regression suite first failed with five `FileNotFoundError` cases. The implementation now exposes `evaluate_preconditions()`, `evaluate_postconditions()`, `capture_snapshot()`, `verify_lookup()`, and `run_cleanup(connection, apply: bool)`; default invocation cannot execute DROP/CREATE, while `--apply` rechecks live state and protects the retained constraint. It recreates the standalone unique index only if both unique indexes unexpectedly disappear after the drop.

- [x] **Step 2: Run the live default dry-run**

`venv/bin/python scripts/maintenance/apply_bilibili_competitor_bvid_index_cleanup.py` returned `status=ready`, 1,106 rows, 0 duplicate groups, 0 waiting locks, the expected constraint owner/standalone target pair, and no DDL.

The direct `--apply` request was rejected by the destructive-action reviewer before process creation. No database mutation occurred, and Tasks 2.3-2.6 remain pending a new explicit confirmation of the exact non-transactional drop risk.

- [ ] **Step 3: Recheck the destructive-action gates immediately before DDL**

Repeat duplicate groups, index definitions/constraint owners, validity/readiness, table-owner membership, and waiting-lock queries in the same maintenance connection. Abort without mutation on any drift.

- [ ] **Step 4: Drop only the redundant standalone index**

Use a direct psycopg2 connection with `autocommit=True` and execute these statements individually:

```sql
SET lock_timeout = '5s';
SET statement_timeout = '15min';
DROP INDEX CONCURRENTLY IF EXISTS public.ix_bilibili_competitor_videos_bvid;
```

- [ ] **Step 5: Verify catalog, uniqueness, query plan, and data count**

Require the drop target to be absent, the constraint-owned index to remain valid/ready/unique, duplicate groups to remain zero, row count unchanged, and an `EXPLAIN (ANALYZE, BUFFERS)` lookup using the two captured real bvids to return both rows with the retained index available to the planner.

- [ ] **Step 6: Apply rollback only if verification contradicts the contract**

If the retained index or query correctness is invalid, run with the same timeouts:

```sql
CREATE UNIQUE INDEX CONCURRENTLY ix_bilibili_competitor_videos_bvid
  ON public.bilibili_competitor_videos USING btree (bvid);
```

Then re-run the catalog and duplicate-data verification and leave the cleanup OPEN with the exact failure.

Pre-execution safety hardening added two red/green cases for a retained index that remains present but invalid and
for a representative EXPLAIN that does not use the retained index. All post-drop catalog, result, and plan failures
now share one rollback boundary and recreate the standalone index whenever it is absent before re-raising the original
verification failure. The actual DDL/rollback steps remain unchecked until explicit risk confirmation permits `--apply`.

### Task 3: Replace the managed runtime through the safety gate

**Files:**
- Execute: `scripts/dev/check-restart-safety.py`
- Execute: `scripts/dev/restart-dev-stack.sh`
- Read: `logs/backend.log`, `logs/celery/*.log`, `runtime/*.pid`

**Interfaces:**
- Consumes: current dirty code already covered by regression tests and the live non-interruptible-task gate.
- Produces: new backend, worker, watchdog, and scheduler process boundaries without interrupting business work.

- [x] **Step 1: Run the initial restart safety gate**

Observed at 08:56 +0800: blocked by publication task `45fdc15d-04fb-49c7-be53-e05e841cf4bb`, feature `content_pdf_clean`, step `local_cleaning`, progress 45%. At 09:01 it had advanced to publish child `cccefede-a811-4f95-bded-f84af803fc86`; at 09:05 a second PDF task `e6aef634-7d1c-40f9-ba15-ec816fe151b2` was also cleaning. Do not override.

- [x] **Step 2: Wait by task state, not a fixed blind delay**

Poll the same safety gate at bounded intervals while useful SQL/test work continues. Proceed only when it returns `OK: no non-interruptible processing tasks found.`

The gate protected both publish children through their real external upload/retry work. `cccefede...` completed first; queued child `946e1525...` then ran for 229.544 seconds and completed at 09:17:50. The final gate returned OK at 09:18:43. No force override was used.

- [x] **Step 3: Run the standard managed restart**

Run `scripts/dev/restart-dev-stack.sh` without force. Let it freeze new consumption, repeat the gate, drain any race winner naturally, and start the complete managed stack.

The standard restart repeated the gate before and after freezing consumption, stopped old backend PID 70203, and started backend PID 21133 without losing queued work.

- [x] **Step 4: Verify new process boundaries and readiness**

Require a new backend PID owning port 5001, `/health` healthy, Celery broker healthy, all configured worker services running, watchdog running, and exactly one Bilibili intelligence scheduler process with a new start time.

Verified: health healthy, broker OK, 17/17 services running and none paused, watchdog PID 21685, and exactly one Bilibili scheduler PID 21707.

### Task 4: Run bounded real workload acceptance

**Files:**
- Execute: `src/backend/run_bilibili_intelligence_scheduler.py`
- Read: `logs/bilibili_intelligence_scheduler.log`, `logs/slow_query.log`, `logs/publication_tasks_query.log`
- Modify: `docs/bugs/2026-08-19-bilibili-intelligence-duplicate-scheduler-write-amplification.md`

**Interfaces:**
- Consumes: new runtime PID boundary and retained unique constraint index.
- Produces: one six-source sync report, bounded SQL counts, absence of the old wide-read/per-row-update shapes, and audit JSON preservation evidence.

- [x] **Step 1: Run one real scheduler `once` cycle**

Run:

```bash
PYTHONPATH=src/backend venv/bin/python src/backend/run_bilibili_intelligence_scheduler.py once
```

Require 6/6 sources or record each external failure; do not publish external content.

Run `bilibili-intelligence-20260828T012100Z` completed 6/6 sources, 180 fetched, 0 created, 180 updated, 0 failed in 9.749 seconds.

- [x] **Step 2: Correlate SQL after the new runtime cutover**

From the cycle start/end and process ID, count target-table SELECT/UPDATE/INSERT statements. Require the identity SELECT to contain only `id,bvid`, existing-row writes to remain at most two bounded conditional UPDATE shapes per source, and the old wide identity-read and per-video update fingerprints to be zero after cutover.

Observed 6 target SELECT, 12 bounded UPDATE, and 0 INSERT. SELECT/UPDATE maxima were 177.00/116.85ms; no target slow SQL, old wide identity read, or per-row update appeared after cutover.

- [x] **Step 3: Verify audit JSON and representative lookup**

Read two created/updated rows through a read-only direct connection; require non-empty `raw_payload`, core source keys (`bvid`, `aid`, `title`, `description`, play/comment/video-review equivalents), and a successful bvid point lookup using the retained constraint index.

Both sampled rows retained 38 source keys and all seven required core keys; the representative lookup returned 2/2 in 0.037ms. Because DDL is still pending, the planner used the redundant standalone index, so retained-index post-drop acceptance remains part of Task 2.5.

### Task 5: Run complete gates and update closure records

**Files:**
- Test: `tests/unit/test_bilibili_intelligence_scheduler.py`
- Test: `tests/unit/test_db_projection_discipline.py`
- Modify: `docs/bugs/recurrence-registry.json`
- Modify: `docs/bugs/reviews/2026-08-28-db-performance-audit.md`
- Modify: `/Users/v9/.codex/automations/db/memory.md`

**Interfaces:**
- Consumes: DDL, runtime, scheduler, SQL-shape, JSON, and test evidence.
- Produces: an updated root-cause chain and explicit next-window acceptance without false closure.

- [x] **Step 1: Run directed regression and syntax gates**

```bash
PYTHONPATH=src/backend venv/bin/python -m pytest -q \
  tests/unit/test_bilibili_intelligence_scheduler.py \
  tests/unit/test_db_projection_discipline.py
venv/bin/python -m compileall -q src/backend
git diff --check
```

Final directed slice after rollback hardening: 101 passed and 3 subtests passed. `compileall` and `git diff --check` exited 0.

- [x] **Step 2: Run the complete unit and Bug-record gates**

```bash
PYTHONPATH=src/backend venv/bin/python -m pytest -q tests/unit
jq empty docs/bugs/recurrence-registry.json
PYTHONPATH=src/backend venv/bin/python scripts/dev/review_bug_records.py --strict
```

Final complete unit run after rollback hardening: 1,419 passed, 3 skipped, 305 subtests passed. Registry JSON was valid and Bug strict classified 174/174 records across nine recurrence clusters.

- [x] **Step 3: Update evidence records**

Record exact before/after index definitions and scan counts, DDL duration, row/duplicate counts, EXPLAIN plan, old/new PIDs, health/worker readiness, scheduler source counts/duration, SQL-shape counts, raw JSON keys, tests, remaining PgBouncer risk, and the next complete-window condition.

Updated the Bilibili Bug, daily audit follow-up, Bug README, recurrence registry, this plan, and automation memory. There is intentionally no after-DDL definition or DDL duration because the destructive reviewer rejected the mutation before process creation.

- [x] **Step 4: Keep statuses honest**

Mark only the duplicate-index maintenance action complete when catalog/runtime evidence passes. Keep Bilibili wide-read code `RUNTIME_REPLACED / OBSERVING` until a complete post-restart 24-hour window has zero old fingerprints; keep PgBouncer/network `OPEN / REPEATED` while connect timeouts persist.

## Self-review

- Spec coverage: covers explicit SQL authorization, precondition/rollback safety, restart discipline, current code loading, real scheduler/API/SQL/runtime evidence, full tests, and recurrence records. PgBouncer networking remains a separate OPEN workstream.
- Placeholder scan: no deferred implementation placeholders; every mutation, verification, rollback, command, file, and acceptance condition is explicit.
- Type consistency: the retained/drop index names, scheduler entry point, task gate command, SQL field shapes, and record paths are consistent across all tasks.
- Execution mode: the user explicitly chose inline execution in this session; no additional execution-choice prompt is required.
