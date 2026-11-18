# 2026-10-21 Daily DB Performance Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 2026-10-20 06:00 至 2026-10-21 06:00 的数据库、页面/API、后台任务与分布式运行体巡检，并仅对证据充分的根因实施可回归修复。

**Architecture:** 先复用现有日志分析器和 durable 只读快照恢复固定窗量化基线，再按 request id、SQL origin、Session hold、PgBouncer 事件和 task id 分层定位 executor、网络、连接持有、外部 I/O 与旧 actor。生产数据库只执行只读统计与有界 `EXPLAIN (ANALYZE, BUFFERS)`；任何 DDL、生产配置或物理隔离动作仅记录人工步骤。确认可在本项目修复的根因后严格 RED→GREEN，最后完成全量验证、运行态验收、Bug/registry/报告和自动化 memory。

**Tech Stack:** Python 3、Flask/SQLAlchemy、PostgreSQL/PgBouncer、Celery、unittest、项目诊断脚本。

**Spec:** 当前自动化提示“每日 DB 慢查询与索引巡检”。

## Global Constraints

- 不得把一次扫描、短时下降、测试通过或当前快照当作 CLOSED。
- 不执行无人值守 DDL、回填、生产配置、凭据轮换、broker namespace 迁移或可能锁表的操作。
- 保留字段完整性、原始 JSON 审计数据和现有用户工作区改动。
- 主要问题按累计耗时、P95、最大耗时、失败率、用户影响和复发次数综合排序。
- 常驻代码变更后，必须先通过 `scripts/dev/check-restart-safety.py` 才能标准重启；被门禁阻断则保持待验收。
- 当前是用户明确指定的现有项目且工作树已有未提交改动；本轮原地续跑，不创建 worktree、不提交、不清理无关文件。

## Review Focus

- 日志覆盖骤降或格式漂移时，必须标记 coverage anomaly，不能把零计数写成零事故。
- Publisher 的 `old-root=0` 不能覆盖 actor 缺失且当前节点素材存在的 `foreign_actor_evidence`。
- 应用 SQL 慢而 executor 快时必须归因到 pool/driver/fetch/网络，禁止无证据加索引。
- 页面/API 无慢 SQL 的长尾与后台长任务不得冒充数据库 executor 或 Session hold。
- 完整测试、运行态证据和后续窗口证据必须分层陈述，未捕获保持 OPEN/OBSERVING。

---

### Task 1: 固定窗口日志与复发基线

**Files:**
- Read: `logs/slow_query.log`
- Read: `logs/app.log`
- Read: `logs/backend.log`
- Read: `logs/scheduler.log`
- Read: `logs/celery/*.log`
- Read: `scripts/diagnostics/analyze_db_performance_logs.py`
- Create: `artifacts/db-performance/2026-10-21-log-analysis.txt`
- Create: `artifacts/db-performance/2026-10-21-prev-log-analysis.txt`

**Interfaces:**
- Consumes: 10-20 报告基线、日志 timestamp/request id/task id/SQL origin/lifecycle/PgBouncer 事件。
- Produces: 本窗与上一窗可比的 SQL、HTTP、Celery、Session、PgBouncer 统计和原始证据指纹。

- [ ] **Step 1:** 用同一分析器生成本窗与上一窗完整输出，确认时间边界和输入日志覆盖。
- [ ] **Step 2:** 提取主要 SQL、错误/timeout、低频高延迟、无匹配慢 SQL、后台长持有和复发键原始证据。
- [ ] **Step 3:** 按累计耗时、P95、最大耗时、失败率、受影响链路和复发次数排序并标记新增/恶化/改善/反复/未闭环。

### Task 2: 只读数据库、索引与运行态取证

**Files:**
- Read: `scripts/diagnostics/daily_db_runtime_snapshot.py`
- Read: `scripts/diagnostics/db_bottleneck_report.py`
- Create: `artifacts/db-performance/2026-10-21-runtime-snapshot.txt`
- Create: `artifacts/db-performance/2026-10-21-db-bottleneck.txt`

**Interfaces:**
- Consumes: Task 1 的主要指纹、request/task id 和异常时间点。
- Produces: durable actor/root、pg_stat、表/索引规模、锁/idle transaction、PgBouncer 与代表 EXPLAIN 证据。

- [ ] **Step 1:** 运行生产只读 durable 快照，复核所有 feature root、Publisher actor、失败率和自然任务。
- [ ] **Step 2:** 运行只读 DB/PgBouncer 诊断与有界代表 EXPLAIN，核对六个重点表和重复/低价值索引。
- [ ] **Step 3:** 逐条分类 executor 慢、pool/网络、Session 持有、N+1/大字段/分页/聚合/缓存/轮询/外部 I/O；三证不足时明确不建议 DDL。

### Task 3: 根因修复与防复发合同

**Files:**
- Modify: 仅由 Task 2 确认根因后决定的最小代码文件。
- Test: 与该根因一一对应的 `tests/unit/test_*.py` 或前端合同。
- Modify: 对应 `docs/bugs/*.md` 与 `docs/bugs/recurrence-registry.json`。

**Interfaces:**
- Consumes: 可稳定重放、调用链闭合且无需生产 DDL 的代码根因。
- Produces: 先失败后通过的最小回归、单一根因修复、复发键和运行验收条件；若无可修代码根因，则产出不修改代码的证据裁决。

- [ ] **Step 1:** 对新根因先写最小失败测试并确认因目标缺陷 RED；无新根因则记录不修改代码的证据。
- [ ] **Step 2:** 仅实施通过该测试所需的最小修复，保留完整字段与审计数据。
- [ ] **Step 3:** 定向 GREEN，并更新 Bug、Session 纪律和 recurrence registry 的复发防线。

### Task 4: 完整验证与运行态验收

**Files:**
- Read: `scripts/dev/check-restart-safety.py`
- Read: `scripts/dev/restart-dev-stack.sh`

**Interfaces:**
- Consumes: Tasks 1-3 的证据和可能代码变更。
- Produces: 定向/完整测试、静态门禁、health/ready/E2E 与明确的 OPEN/OBSERVING/RUNTIME_REPLACED/CLOSED 状态。

- [ ] **Step 1:** 运行定向测试、完整 unit、compileall、worker/revoke 边界、Bug strict、registry JSON 和 `git diff --check`。
- [ ] **Step 2:** 只有常驻代码改变且安全门放行时执行标准重启；否则不重启并写明阻塞。
- [ ] **Step 3:** 验证 backend health、worker/scheduler ready、idle-in-transaction 和真实 ZSXQ/重点 API 或自然任务；缺自然样本保持 OBSERVING。

### Task 5: 报告、闭环状态表与自动化记忆

**Files:**
- Create: `docs/bugs/reviews/2026-10-21-db-performance-audit.md`
- Modify: `docs/bugs/README.md`
- Modify: `docs/bugs/recurrence-registry.json`
- Modify: `/Users/v9/.codex/automations/db/memory.md`

**Interfaces:**
- Consumes: Tasks 1-4 的全部证据。
- Produces: 中文结论、前后量化、调用链、索引裁决、人工动作、风险、未捕获边界和下一轮验收计划。

- [ ] **Step 1:** 生成根因闭环状态表，逐项列出指纹、根因、修复、运行态证据、复发、风险、责任动作和下一轮条件。
- [ ] **Step 2:** 复核所有“已解决/CLOSED”均具备修复、测试、真实运行态和后续窗口证据；否则降为 OPEN/OBSERVING。
- [ ] **Step 3:** 用当前运行时间把本轮摘要前置写入 automation memory，并完成最终新鲜验证。
