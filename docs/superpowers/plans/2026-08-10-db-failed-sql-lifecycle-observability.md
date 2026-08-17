# Failed SQL Lifecycle Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure failed database statement time is included in connection-lifecycle SQL totals so PgBouncer timeouts are not misclassified as non-SQL Session holds.

**Architecture:** Keep the existing SQLAlchemy event model: `before_cursor_execute` pushes a start timestamp, `handle_error` consumes it, and checkout lifecycle counters aggregate both successful and failed statements. Repair only the broken timestamp accessor and protect the observable failed-statement duration with a focused unit test.

**Tech Stack:** Python 3, SQLAlchemy events, unittest.

## Global Constraints

- Preserve business fields, raw JSON, and audit payloads.
- Do not execute production DDL, configuration changes, or destructive actions.
- A passing test is not runtime closure; deployment and a later failure sample remain separate acceptance stages.

---

### Task 1: Failed statement duration regression

**Files:**
- Modify: `tests/unit/test_database_connection_lifecycle.py`
- Modify: `src/backend/middleware/database_logger.py`

**Interfaces:**
- Consumes: `DatabaseLoggerMiddleware._query_start_times_quietly(connection)` and `_handle_error(exception_context)`.
- Produces: failed SQL duration included in `dig_ytb_checkout_sql_total_ms` and `dig_ytb_checkout_sql_max_ms`.

- [x] **Step 1: Write the failing test**

  Add a test with a real `connection.info` timestamp stack and a minimal SQLAlchemy error context. Patch wall clock values to span 20 seconds, invoke `_handle_error`, and assert the checkout SQL count is 1 with total/max duration 20,000ms.

- [x] **Step 2: Run test to verify it fails**

  Run: `venv/bin/python -m unittest tests.unit.test_database_connection_lifecycle.DatabaseConnectionLifecycleTest.test_failed_sql_duration_is_aggregated_for_current_checkout -v`

  Expected: FAIL because `_query_start_times_quietly()` returns `None` for a live connection, so duration is recorded as 0ms.

- [x] **Step 3: Write minimal implementation**

  Restore the `try/except` return of `connection.info.setdefault("query_start_time", [])` inside `_query_start_times_quietly()` and remove the unreachable misplaced block from `_record_checkout_sql_duration()`.

- [x] **Step 4: Run focused tests**

  Run: `venv/bin/python -m unittest tests.unit.test_database_connection_lifecycle tests.unit.test_runtime_initialization_idempotency -v`

  Expected: PASS.

- [x] **Step 5: Run project verification**

  Run the relevant DB analyzer tests, the full unit suite, `compileall`, `git diff --check`, recurrence-registry JSON parsing, and strict Bug record validation.

### Task 2: Daily audit evidence and recurrence tracking

**Files:**
- Create: `docs/bugs/2026-08-10-failed-sql-lifecycle-duration-misclassified.md`
- Create: `docs/bugs/reviews/2026-08-10-db-performance-audit.md`
- Modify: `docs/bugs/README.md`
- Modify: `docs/bugs/recurrence-registry.json`
- Modify: automation memory after verification.

**Interfaces:**
- Consumes: fixed 24-hour analyzer output, request/task IDs, PgBouncer event timestamps, live table/index statistics, and EXPLAIN evidence.
- Produces: explicit OPEN/OBSERVING status, recurrence guardrail, runtime boundary, residual risks, and next-window acceptance conditions.

- [x] **Step 1: Record pre-fix evidence**

  Document request `3b1274e9`, the 03:03 PgBouncer timeout, the false 0ms SQL error duration, and the resulting 29.671s false non-SQL attribution.

- [x] **Step 2: Record fix and tests**

  Link the exact code/test files and list focused/full verification results without calling the network root cause resolved.

- [x] **Step 3: Set runtime acceptance boundary**

  Keep the observability fix at CODE_MODIFIED or RUNTIME_REPLACED depending on safe restart evidence. Require a future real failed-statement sample to show nonzero SQL duration and accurate non-SQL remainder.

- [x] **Step 4: Update recurrence guardrail**

  Extend `DB-SESSION-LIFECYCLE` so failed SQL timing must be included before classifying a long checkout as external I/O or application Session hold.

### Task 3: Finalization polling and list-query regression

**Files:**
- Modify: `tests/unit/test_report_audio_finalization.py`
- Modify: `tests/unit/test_report_audio_workflow_api.py`
- Modify: `src/backend/services/report_audio_finalization.py`
- Modify: `src/backend/api/report_audio_workflow.py`

**Interfaces:**
- Consumes: stale active finalization rows and detailed report-audio list pages.
- Produces: no periodic JSON predicate over completed finalizations; one bounded, field-level summary projection per detailed page instead of one full-payload query per job.

- [x] **Step 1: Write failing watchdog and batching tests**

  Assert that an unversioned completed legacy coordinator is not polled forever, while active stale coordinators remain eligible. Assert that a detailed list calls one batch-summary loader and never the single-audio loader.

- [x] **Step 2: Run the tests red**

  Run only the new test methods and capture the expected failures against the current completed-row JSON predicate and N+1 loader.

- [x] **Step 3: Implement the bounded projection**

  Restrict periodic repair to stale active rows. Add a batch summary query that selects structured columns plus only required JSON subfields, preserving all response fields and stored audit JSON. Pass those summaries into list payload construction.

- [x] **Step 4: Run focused and grouped regressions**

  Verify watchdog eligibility, summary fidelity, list call count, and the complete finalization/report-audio API modules.
