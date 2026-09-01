# 2026-08-26 Daily DB Performance Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the 24-hour database/API/task audit, repair any evidence-backed root cause safe for unattended code changes, and keep every item without later runtime non-recurrence evidence OPEN/OBSERVING.

**Architecture:** Reuse the repository log analyzer for fixed-window SQL, HTTP, task, lifecycle, and PgBouncer metrics; validate top fingerprints against raw request IDs and call origins; then use read-only PostgreSQL catalog and `EXPLAIN (ANALYZE, BUFFERS)` evidence. Any behavior change follows red-green TDD, while the audit report, Bug records, recurrence registry, and automation memory preserve the evidence chain for the next run.

**Tech Stack:** Python 3, pytest, Flask/SQLAlchemy, PostgreSQL/PgBouncer, JavaScript contract tests, Markdown/JSON operational records.

**Spec:** Automation prompt `每日 DB 慢查询与索引巡检`, automation id `db`.

## Global Constraints

- Do not execute production DDL, backfills, destructive operations, or locking schema changes unattended.
- Do not claim CLOSED without a real post-fix runtime path and a later non-recurrence window.
- Preserve raw JSON audit data and business field completeness.
- Run `scripts/dev/check-restart-safety.py` before any backend, Celery, scheduler, or full-stack restart.
- Preserve unrelated dirty-worktree files, including the pre-existing untracked `artifacts/` tree.

---

### Task 1: Fixed-window baseline and recurrence keys

**Files:**
- Read: `/Users/v9/.codex/automations/db/memory.md`
- Read: `docs/bugs/reviews/2026-08-25-db-performance-audit.md`
- Read: `docs/rules/database-session-lifecycle-discipline.md`
- Read: `docs/bugs/README.md`
- Read: `docs/bugs/recurrence-registry.json` and its relevant Bug records
- Produce: `/private/tmp/db-audit-20260826-analysis.txt`

**Interfaces:**
- Consumes: the previous fixed-window metrics and explicit next-run acceptance conditions.
- Produces: current and comparable prior-window SQL/HTTP/task/lifecycle/PgBouncer metrics plus recurrence verdicts.

- [ ] Run the existing analyzer for the latest complete 24-hour log window over current and rotated logs.
- [ ] Re-run the prior audit window with the same analyzer version for an apples-to-apples comparison.
- [ ] Cross-check top fingerprints, application SQL errors, timeouts, long holds, unmatched HTTP latency, low-frequency tails, and background-only holds against raw log lines.

### Task 2: Executor, table, and index evidence

**Files:**
- Read: `scripts/diagnostics/db_bottleneck_report.py`
- Produce: `/private/tmp/db-audit-20260826-bottleneck.txt`

**Interfaces:**
- Consumes: the highest-impact real SQL shapes and origins from Task 1.
- Produces: read-only table/index statistics, activity/lock state, and representative executor evidence.

- [ ] Run read-only PostgreSQL/PgBouncer diagnostics and representative `EXPLAIN (ANALYZE, BUFFERS)` checks.
- [ ] Compare target-table sizes, seq/index scans, duplicate/overlapping indexes, write amplification, locks, idle transactions, and executor times with the previous baseline.
- [ ] Generate only DBeaver-safe manual SQL, with timeouts, verification, and rollback, if real SQL plus catalog and EXPLAIN evidence justifies a DDL recommendation.

### Task 3: Evidence-backed root-cause repair

**Files:**
- Modify: only the minimal source file proven responsible by Tasks 1–2.
- Test: the matching `tests/unit/` or frontend behavior contract.
- Document: the matching `docs/bugs/2026-08-26-*.md` record and `docs/bugs/recurrence-registry.json` entry.

**Interfaces:**
- Consumes: one specific root-cause hypothesis backed by a reproducible call chain.
- Produces: a failing regression, minimal root-cause fix, passing targeted suite, and recurrence guardrail.

- [ ] Write the smallest regression test and run it to verify the expected RED failure.
- [ ] Implement only the confirmed root-cause correction; do not mask it with added retries, timeouts, or log suppression.
- [ ] Run the targeted test and neighboring module suite to verify GREEN.
- [ ] If runtime replacement is required, run the restart-safety gate first; restart only on a green gate, then validate health and a real API/page/worker path.

### Task 4: Full verification and durable handoff

**Files:**
- Create: `docs/bugs/reviews/2026-08-26-db-performance-audit.md`
- Update: `docs/bugs/README.md` and `docs/bugs/recurrence-registry.json` when recurrence or a new defect requires it.
- Update: `/Users/v9/.codex/automations/db/memory.md`

**Interfaces:**
- Consumes: all fixed-window, catalog, change, test, and runtime evidence.
- Produces: the Chinese audit report, root-cause closure table, explicit risk boundaries, and next-window acceptance plan.

- [ ] Run targeted tests, the complete deterministic unit suite, `compileall`, `git diff --check`, recurrence JSON validation, and strict Bug record review.
- [ ] Write the report with before/after metrics, endpoint/task/SQL correlations, index evidence, manual actions, unobserved boundaries, and explicit OPEN/OBSERVING states.
- [ ] Re-read the automation specification and verify each required section is present without placeholders.
- [ ] Update automation memory with the current run time, decisions, blockers, and exact next-window acceptance conditions.
