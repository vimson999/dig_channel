# 2026-11-01 Daily DB Performance Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 2026-10-31 06:00 至 2026-11-01 06:00 的数据库、页面/API、后台任务与分布式运行体巡检，并只对证据充分的根因实施可回归修复。

**Architecture:** 使用既有日志分析器和只读数据库诊断建立与上一固定窗可比的全链路基线，再沿 request id、SQL origin、Session hold、PgBouncer/网络、executor、外部 I/O 与 task id 分层定位。只有可稳定重放、无需生产 DDL 的代码根因才进入 TDD 修复；否则保留 OPEN/OBSERVING 并形成明确复核条件。

**Tech Stack:** Python 3、Flask/SQLAlchemy、PostgreSQL/PgBouncer、Celery、Node test runner、unittest。

**Spec:** 当前自动化提示“每日 DB 慢查询与索引巡检”。

## Global Constraints

- 不得把一次扫描、短时下降、测试通过或当前快照当作 CLOSED。
- 不执行无人值守 DDL、回填、生产配置、凭据轮换、broker namespace 迁移或可能锁表的操作。
- 保留字段完整性、原始 JSON 审计数据和用户已有工作树改动。
- 主要问题按累计耗时、P95、最大耗时、失败率、受影响链路和复发次数综合排序。
- 常驻代码改变后先运行 `scripts/dev/check-restart-safety.py`；只有门禁放行才标准重启。
- 当前 checkout 承载真实日志与上一轮未提交修复，原地续跑，不创建 worktree、不提交或清理既有改动。

## Review Focus

- 日志覆盖或解析格式变化必须标为 coverage anomaly，不能把未捕获写成零事故。
- TTS/B站修复只有完整后窗证据才能从 OBSERVING 升级。
- 应用 SQL 慢而 executor 快时必须归因到 pool/driver/fetch/网络，不得盲目加索引。
- 页面/API 无慢 SQL 的长尾、后台长任务与数据库 Session hold 必须分开统计。
- old-root=0、任务最终成功或当前远端会话=0 都不能覆盖 actor、重试或物理隔离风险。

---

### Task 1: 固定窗口日志与复发基线

**Files:**
- Read: `logs/slow_query.log`、`logs/app.log`、`logs/backend.log`、`logs/scheduler.log`、`logs/celery/*.log`
- Create: `artifacts/db-performance/2026-11-01-log-analysis.txt`
- Create: `artifacts/db-performance/2026-11-01-prev-log-analysis.txt`

**Interfaces:**
- Consumes: 10-31 报告、memory、Bug/Session discipline、recurrence registry。
- Produces: SQL、HTTP、Celery、Session、PgBouncer 的可比统计与复发排序。

- [x] 用同一分析器生成本窗和上一窗输出并检查覆盖。
- [x] 提取主要 SQL 指纹、错误、三类长尾和复发键。
- [x] 明确新增、恶化、改善、反复与仍未闭环。

### Task 2: 只读数据库、索引与运行态取证

**Files:**
- Read: `scripts/diagnostics/daily_db_runtime_snapshot.py`
- Read: `scripts/diagnostics/db_bottleneck_report.py`
- Create: `artifacts/db-performance/2026-11-01-runtime-snapshot.txt`
- Create: `artifacts/db-performance/2026-11-01-db-bottleneck.txt`

**Interfaces:**
- Consumes: Task 1 的指纹、request/task id。
- Produces: durable actor/root、表/索引、锁、idle transaction、代表 EXPLAIN 与运行体证据。

- [x] 运行只读 durable 快照与数据库/PgBouncer 诊断。
- [x] 对真实热点做有界 `EXPLAIN (ANALYZE, BUFFERS)` 或复用同窗可信证据。
- [x] 分类 executor、pool/网络、Session、N+1、宽字段/分页、缓存/轮询与外部 I/O。

### Task 3: 根因修复与防复发合同

**Files:**
- Modify: 仅修改 Tasks 1-2 已证实且可安全落地的最小代码、测试、Bug 与 registry 文件。

**Interfaces:**
- Consumes: 可稳定重放、兼容业务且无需生产 DDL 的根因。
- Produces: RED→GREEN 修复，或无代码变更的证据裁决。

- [x] 若发现代码根因，先写最小失败测试并确认 RED。
- [x] 实施单一最小修复并确认定向 GREEN。
- [x] 更新 Bug、纪律与 recurrence registry 的防复发契约。

### Task 4: 完整验证与运行态验收

**Files:**
- Read: `scripts/dev/check-restart-safety.py`、`scripts/dev/restart-dev-stack.sh`

**Interfaces:**
- Consumes: Tasks 1-3 的证据和变更。
- Produces: 定向/完整测试、静态门禁、health/ready/E2E 与闭环状态。

- [x] 运行定向测试、完整 unit、compileall、Bug strict、registry JSON 与 `git diff --check`。
- [x] 仅在常驻代码改变且安全门放行时标准重启。
- [x] 验证 backend、workers、scheduler 与真实重点 API/自然任务。

### Task 5: 报告、闭环状态表与自动化记忆

**Files:**
- Create: `docs/bugs/reviews/2026-11-01-db-performance-audit.md`
- Modify: 对应 Bug、`docs/bugs/README.md`、`docs/bugs/recurrence-registry.json`
- Modify: `/Users/v9/.codex/automations/db/memory.md`

**Interfaces:**
- Consumes: Tasks 1-4 的全部证据。
- Produces: 中文报告、逐指纹闭环表和下一轮续跑基线。

- [x] 写入修复前后证据、人工 SQL/风险、未捕获边界与下一轮验收条件。
- [x] 逐项确认任何 CLOSED 都同时具备修复、测试、运行态与后续窗口证据。
- [x] 更新 memory，并做最终新鲜验证。
