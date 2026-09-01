# 2026-08-31 Daily DB Performance Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 对 2026-08-30 06:02:19 至 2026-08-31 06:02:19 的数据库、页面/API 和后台任务做全链路巡检，修复可证实的应用根因，并为所有未满足后续运行窗口的问题保留 OPEN/OBSERVING。

**Architecture:** 先用现有分析器恢复固定窗口的 SQL、HTTP、Session、PgBouncer 和 Celery 基线，再用 request/task id、SQL origin、运行态只读数据库统计与 EXPLAIN 将 executor、连接/网络、外部 I/O 和应用持有分层。只有证据确认应用根因时才走 TDD；索引变更必须满足真实 SQL、pg_stat 与 EXPLAIN 三证，且无人值守只生成 DBeaver 脚本。

**Tech Stack:** Python 3 / Flask / SQLAlchemy / PostgreSQL / PgBouncer / Celery / unittest / Node contract tests

**Spec:** `docs/superpowers/specs/2026-08-29-db-root-cause-closure-design.md`，本轮自动化指令，以及 `docs/rules/database-session-lifecycle-discipline.md`

## Global Constraints

- 不删除原始 JSON，不牺牲字段完整性，不用假数据、放宽超时或无限重试掩盖问题。
- 未拿到真实 worker/API/页面/任务后续窗口证据时，不得标记 CLOSED。
- CREATE/DROP INDEX、ALTER TABLE、回填、生产配置和可能锁表的动作只生成带验证与回滚的人工 SQL。
- 只有 `scripts/dev/check-restart-safety.py` 确认无不可中断任务时才允许运行 `scripts/dev/restart-dev-stack.sh`。
- 保留当前 dirty worktree 中所有既有用户/自动化改动，只做本轮必要的最小增量。

---

### Task 1: Restore baseline and collect the fixed window

**Files:**
- Read: `/Users/v9/.codex/automations/db/memory.md`
- Read: `docs/bugs/reviews/2026-08-30-db-performance-audit.md`
- Read: `docs/bugs/recurrence-registry.json`
- Read: `docs/rules/database-session-lifecycle-discipline.md`
- Output: `/private/tmp/db-audit-20260831-fixed.txt`

**Interfaces:**
- Consumes: 2026-08-30 baseline metrics and open recurrence keys.
- Produces: fixed-window SQL/HTTP/Session/PgBouncer/Celery evidence for ranking.

- [x] **Step 1: Run the fixed-window analyzer**

```bash
venv/bin/python scripts/diagnostics/analyze_db_performance_logs.py \
  --since '2026-08-30 06:02:19' --until '2026-08-31 06:02:19' --limit 60
```

- [x] **Step 2: Capture focused raw-log correlations**

Use `rg` against `logs/slow_query.log`, `logs/backend.log`, `logs/app.log`,
`logs/scheduler.log`, `logs/pgbouncer.log`, and `logs/celery/*.log` for every top
fingerprint, request id, timeout, long Session and task failure found by Step 1.

- [x] **Step 3: Compare to the previous report**

Classify every material signal as NEW, WORSENED, IMPROVED, REPEATED, or STILL OPEN,
using cumulative duration, P95/max, failure rate, user/page impact and recurrence count.

### Task 2: Gather live database and runtime evidence

**Files:**
- Read: `scripts/dev/check-runtime-schema.py`
- Read: existing diagnostics and service routes used by the 2026-08-30 report.

**Interfaces:**
- Consumes: top fingerprints and endpoint gaps from Task 1.
- Produces: table/index statistics, EXPLAIN evidence, runtime health and E2E timings.

- [x] **Step 1: Run read-only PostgreSQL diagnostics**

Within one read-only application context, collect `pg_stat_activity`,
`pg_stat_user_tables`, `pg_stat_user_indexes`, exact duplicate indexes, the six required
table sizes, notification deliveries, natural MP3 source identities, and targeted
`EXPLAIN (ANALYZE, BUFFERS)` for material SQL only.

- [x] **Step 2: Verify PgBouncer and process topology**

Collect `SHOW CONFIG`, `SHOW POOLS`, `SHOW SERVERS`, source addresses, connection churn,
backend health, broker/worker readiness and relevant PID/bootstrap boundaries without
changing runtime state.

- [x] **Step 3: Run real E2E probes**

Exercise authenticated real endpoints for ZSXQ Insights overview, topics/local,
topics/local/tabs, sync/status, MP3 catalog/jobs/runtime status, notification state and
other top/low-frequency endpoints exposed by Task 1. Correlate each request id to SQL and
Session lifecycle logs. Do not perform external publishing or expensive synthetic work.

### Task 3: Diagnose and, only if justified, fix an application root cause with TDD

**Files:**
- Modify: only the exact production module proven responsible by Tasks 1–2.
- Test: the nearest existing `tests/unit/test_*.py` or `tests/frontend/*_contract.js`.

**Interfaces:**
- Consumes: a single root-cause hypothesis with reproducible evidence.
- Produces: one minimal, contract-protected fix or an evidence-backed no-code decision.

- [x] **Step 1: Write one failing behavior test（证据门禁判定不适用）**

Name the production break it catches; use real code and literal expectations. Run only
that test and confirm it fails for the identified missing behavior, not setup or syntax.

- [x] **Step 2: Implement the minimal root-cause fix（证据门禁判定不适用）**

Preserve fields and audit payloads; do not bundle unrelated refactors, timeouts, retries
or indexes.

- [x] **Step 3: Verify red/green and mutation protection（证据门禁判定不适用）**

Run the exact test to green, then the containing module suite. Confirm reverting the
behavior would make at least one test fail.

- [x] **Step 4: Decide runtime replacement safely**

If production code changed, run `venv/bin/python scripts/dev/check-restart-safety.py`.
Restart only when it returns safe; otherwise keep the code and mark runtime acceptance
blocked by the safety window.

**Decision:** Tasks 1–2 reproduced the failure with PostgreSQL executor times in the
sub-millisecond to tens-of-milliseconds range, no lock/pool wait, and 30% packet loss on
the live Tailscale path. No missing application behavior was identified, so manufacturing
a failing test or changing production code/config would have hidden an infrastructure
root cause. No runtime replacement or restart was required.

### Task 4: Verify, document and preserve recurrence memory

**Files:**
- Create: `docs/bugs/reviews/2026-08-31-db-performance-audit.md`
- Modify: relevant `docs/bugs/*.md`, `docs/bugs/README.md`, and
  `docs/bugs/recurrence-registry.json` only when evidence changes their state.
- Modify: `/Users/v9/.codex/automations/db/memory.md`

**Interfaces:**
- Consumes: all evidence and any fix from Tasks 1–3.
- Produces: report, closure table, verified regression state and next-run acceptance plan.

- [x] **Step 1: Run targeted and full verification**

```bash
PYTHONPATH=src/backend venv/bin/python -m unittest discover -s tests/unit -p 'test_*.py'
venv/bin/python -m compileall -q src/backend scripts
git diff --check
jq empty docs/bugs/recurrence-registry.json
venv/bin/python scripts/dev/review_bug_records.py --strict
```

Run any affected Node/JS contract and syntax checks in addition to the commands above.

- [x] **Step 2: Write the Chinese audit report**

Include the fixed-window comparison, ranked problems, SQL/request/task correlation,
three mandatory anomaly classes, table/index/EXPLAIN evidence, manual SQL/risk, uncaptured
boundaries, and the root-cause closure status table.

- [x] **Step 3: Update recurrence evidence and memory**

Set `reviewed_through` to 2026-08-31, update only materially changed cluster observations,
then prepend a concise run summary and current completion time to automation memory.

- [x] **Step 4: Re-read every completion claim**

Require fresh command output and runtime evidence for each claim. Any missing later window,
natural task, provider fault/recovery or mailbox receipt remains OPEN/OBSERVING.
