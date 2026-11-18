# 2026-10-30 Daily DB Performance Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 2026-10-29 06:00 至 2026-10-30 06:00 的数据库、页面/API、后台任务与分布式运行体巡检，并仅对证据充分的根因实施可回归修复。

**Architecture:** 先用现有日志分析器和 durable 只读快照建立与上一固定窗可比的量化基线，再按 request id、SQL origin、Session hold、PgBouncer、executor、外部 I/O 和 task id 分层定位根因。生产数据库只执行只读统计与有界 `EXPLAIN (ANALYZE, BUFFERS)`；DDL、生产配置、凭据或跨主机隔离动作只形成带验证/回滚的人工步骤。确认可在本项目修复的代码根因后严格 RED→GREEN，最后完成全量验证、运行态验收、Bug/registry/报告和 automation memory。

**Tech Stack:** Python 3、Flask/SQLAlchemy、PostgreSQL/PgBouncer、Celery、unittest、项目诊断脚本。

**Spec:** 当前自动化提示“每日 DB 慢查询与索引巡检”。

## Global Constraints

- 不得把一次扫描、短时下降、测试通过或当前快照当作 CLOSED。
- 不执行无人值守 DDL、回填、生产配置、凭据轮换、broker namespace 迁移或可能锁表的操作。
- 保留字段完整性、原始 JSON 审计数据和现有用户工作区改动。
- 主要问题按累计耗时、P95、最大耗时、失败率、受影响链路和复发次数综合排序。
- 常驻代码变更后，必须先通过 `scripts/dev/check-restart-safety.py` 才能标准重启；被门禁阻断则保持待验收。
- 用户明确指定当前项目；工作树已有前序未提交改动，本轮原地续跑，不创建 worktree、不提交、不清理无关文件。

## Review Focus

- 日志覆盖骤降或格式漂移时，必须标记 coverage anomaly，不能把零计数写成零事故。
- `old-root=0` 不能覆盖 actor 缺失、远端会话或共享 broker/凭据风险。
- 应用 SQL 慢而 executor 快时必须归因到 pool/driver/fetch/网络，禁止无证据加索引。
- 页面/API 无慢 SQL 的长尾与后台长任务不得冒充数据库 executor 或 Session hold。
- 完整测试、运行态证据和后续窗口证据必须分层陈述，未捕获保持 OPEN/OBSERVING。

---

### Task 1: 固定窗口日志与复发基线

**Files:**
- Read: `logs/slow_query.log`、`logs/app.log`、`logs/backend.log`、`logs/scheduler.log`、`logs/celery/*.log`
- Create: `artifacts/db-performance/2026-10-30-log-analysis.txt`
- Create: `artifacts/db-performance/2026-10-30-prev-log-analysis.txt`

**Interfaces:**
- Consumes: 10-29 报告、memory、recurrence registry。
- Produces: 本窗与上一窗 SQL、HTTP、Celery、Session、PgBouncer 可比统计和问题排序。

- [x] **Step 1:** 用同一分析器生成两窗完整输出并确认覆盖。
- [x] **Step 2:** 提取主要指纹、错误、低频高延迟、无慢 SQL 长尾和后台长持有。
- [x] **Step 3:** 标记新增、恶化、改善、反复和仍未闭环。

### Task 2: 只读数据库、索引与运行态取证

**Files:**
- Read: `scripts/diagnostics/daily_db_runtime_snapshot.py`
- Read: `scripts/diagnostics/db_bottleneck_report.py`
- Create: `artifacts/db-performance/2026-10-30-runtime-snapshot.txt`
- Create: `artifacts/db-performance/2026-10-30-db-bottleneck.txt`

**Interfaces:**
- Consumes: Task 1 的指纹、request/task id。
- Produces: durable actor/root、pg_stat、表/索引、锁/idle transaction、PgBouncer 与代表 EXPLAIN 证据。

- [x] **Step 1:** 运行只读 durable 快照并复核自然任务。
- [x] **Step 2:** 运行只读 DB/PgBouncer 诊断与有界代表 EXPLAIN。
- [x] **Step 3:** 分类 executor、pool/网络、Session、N+1/大字段/分页/缓存/轮询/外部 I/O。

### Task 3: 根因修复与防复发合同

**Files:**
- Modify: 仅由 Tasks 1-2 证实根因后的最小代码、测试、Bug 与 registry 文件。

**Interfaces:**
- Consumes: 可稳定重放且无需生产 DDL 的代码根因。
- Produces: RED→GREEN 回归与最小修复，或不改代码的证据裁决。

- [x] **Step 1:** 新代码根因先写最小失败测试并确认 RED（本轮裁决无可安全落地的新代码根因，N/A）。
- [x] **Step 2:** 仅实施使该测试通过的修复（本轮未改生产代码，N/A）。
- [x] **Step 3:** 定向 GREEN，并更新 Bug、纪律和 recurrence registry。

### Task 4: 完整验证与运行态验收

**Files:**
- Read: `scripts/dev/check-restart-safety.py`、`scripts/dev/restart-dev-stack.sh`

**Interfaces:**
- Consumes: Tasks 1-3 的证据和变更。
- Produces: 完整测试、静态门禁、health/ready/E2E 和闭环状态。

- [x] **Step 1:** 运行定向、完整 unit、compileall、Bug strict、registry JSON 与 `git diff --check`。
- [x] **Step 2:** 只有常驻代码改变且安全门放行时标准重启（本轮无常驻代码变更，无需重启）。
- [x] **Step 3:** 验证 backend/worker/scheduler 与真实重点 API/自然任务。

### Task 5: 报告、闭环状态表与自动化记忆

**Files:**
- Create: `docs/bugs/reviews/2026-10-30-db-performance-audit.md`
- Modify: 对应 Bug、`docs/bugs/README.md`、`docs/bugs/recurrence-registry.json`
- Modify: `/Users/v9/.codex/automations/db/memory.md`

**Interfaces:**
- Consumes: Tasks 1-4 的全部证据。
- Produces: 中文报告、闭环状态表和下一轮续跑基线。

- [x] **Step 1:** 生成逐指纹闭环状态表和索引/人工动作裁决。
- [x] **Step 2:** 复核所有 CLOSED 均有修复、测试、运行态和后续窗口证据。
- [x] **Step 3:** 用当前运行时间前置更新 automation memory，并完成最终新鲜验证。
