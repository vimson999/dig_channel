# 2026-08-20 Daily DB Performance Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the fixed-window database/API/task audit, repair any evidence-backed application root cause that is safe to change unattended, and preserve every unverified item as OPEN/OBSERVING.

**Architecture:** Use the existing log analyzer for the fixed `2026-08-19 06:02:19`–`2026-08-20 06:02:19` window, correlate its output with raw logs and the prior recurrence keys, then use read-only PostgreSQL diagnostics for executor/index evidence. Any code change follows red-green TDD; the report, Bug record, registry, and automation memory carry the evidence chain into the next run.

**Tech Stack:** Python 3, pytest, Flask/SQLAlchemy, PostgreSQL/PgBouncer, Markdown/JSON operational records.

**Spec:** Automation prompt `每日 DB 慢查询与索引巡检`, automation id `db`.

## Global Constraints

- Do not execute production DDL, backfills, destructive operations, or locking schema changes unattended.
- Do not claim CLOSED without post-fix runtime evidence and a later non-recurrence window.
- Preserve raw JSON and business field completeness.
- Run `scripts/dev/check-restart-safety.py` before any local service restart.
- Preserve unrelated dirty-worktree changes.

---

### Task 1: Fixed-window evidence and recurrence baseline

**Files:**
- Read: `/Users/v9/.codex/automations/db/memory.md`
- Read: `docs/bugs/reviews/2026-08-19-db-performance-audit.md`
- Read: `docs/rules/database-session-lifecycle-discipline.md`
- Read: `docs/bugs/recurrence-registry.json`
- Produce: `/private/tmp/db-audit-20260820-analysis.txt`

**Interfaces:**
- Consumes: fixed window and previous acceptance conditions.
- Produces: SQL/HTTP/task/lifecycle/PgBouncer metrics and recurrence verdicts.

- [ ] Run `venv/bin/python scripts/diagnostics/analyze_db_performance_logs.py --since '2026-08-19 06:02:19' --until '2026-08-20 06:02:19'` over all discovered current and rotated logs.
- [ ] Re-run the previous window with the same analyzer version to create an apples-to-apples comparison.
- [ ] Cross-check top fingerprints, errors, timeouts, long holds, low-frequency high latency, unmatched HTTP latency, and background-only holds against raw request/task/origin lines.

### Task 2: PostgreSQL executor and index evidence

**Files:**
- Read: `scripts/diagnostics/db_bottleneck_report.py`
- Produce: `/private/tmp/db-audit-20260820-bottleneck.txt`

**Interfaces:**
- Consumes: top real SQL shapes from Task 1.
- Produces: table/index statistics, activity/lock state, and representative `EXPLAIN (ANALYZE, BUFFERS)` evidence.

- [ ] Run the existing read-only bottleneck report with representative ZSXQ group IDs and `--explain`.
- [ ] Compare table sizes, seq/index scans, overlapping indexes, write amplification, locks, idle transactions, and executor times with 2026-08-19.
- [ ] Emit only DBeaver-safe manual SQL when real SQL + catalog stats + EXPLAIN justify an index change.

### Task 3: Root-cause repair with TDD when justified

**Files:**
- Modify: only the minimal service/diagnostic file proven responsible by Tasks 1–2.
- Test: the matching `tests/unit/` or frontend contract file.
- Document: matching `docs/bugs/2026-08-20-*.md` and `docs/bugs/recurrence-registry.json`.

**Interfaces:**
- Consumes: one confirmed root-cause hypothesis and a reproducible failing behavior.
- Produces: failing regression, minimal fix, passing targeted tests, and a recurrence guardrail.

- [ ] Write one failing regression test and confirm it fails for the expected behavior.
- [ ] Implement the smallest root-cause fix without timeout/retry masking.
- [ ] Re-run the targeted test and relevant module suite.
- [ ] If runtime replacement is required, run restart safety first; restart only when the gate is green, then verify health and a real API/worker/page path.

### Task 4: Full verification and durable handoff

**Files:**
- Create: `docs/bugs/reviews/2026-08-20-db-performance-audit.md`
- Update: `docs/bugs/README.md`
- Update: `docs/bugs/recurrence-registry.json`
- Update: `/Users/v9/.codex/automations/db/memory.md`

**Interfaces:**
- Consumes: all Task 1–3 evidence.
- Produces: Chinese audit report, closure table, and next-window acceptance plan.

- [ ] Run targeted tests, the complete deterministic unit suite, `compileall`, `git diff --check`, recurrence JSON validation, and strict Bug review.
- [ ] Write the report with before/after metrics, endpoint/task/SQL correlation, index evidence, unobserved boundaries, and explicit OPEN/OBSERVING states.
- [ ] Update memory with the current run time, decisions, blockers, and the exact next-window acceptance conditions.
