# 2026-09-03 Database Performance Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 2026-09-02 06:00 至 2026-09-03 06:00 的数据库、页面/API 与后台任务性能巡检，并仅对证据充分的根因实施可回归、可运行验收的修复。

**Architecture:** 先用现有日志分析器建立固定窗口量化基线，再按 request id、SQL origin、Session hold 与 Celery task id 追踪最高累计耗时、P95、最大值和失败风暴。数据库物理层只执行只读统计与 EXPLAIN；任何 DDL 仅生成带超时、验证和回滚的人工 SQL。确认应用根因后按 TDD 实施单一修复，最后更新 Bug、recurrence registry、巡检报告和自动化记忆。

**Tech Stack:** Python 3、Flask/SQLAlchemy、PostgreSQL/PgBouncer、Celery、unittest、项目诊断脚本。

**Spec:** 自动化“每日 DB 慢查询与索引巡检”当前运行提示。

## Global Constraints

- 不得把扫描完成、一次止血或短窗下降写成 CLOSED。
- 不执行无人值守 DDL、回填、生产配置或可能锁表的操作。
- 保留原始 JSON 审计数据和现有字段完整性。
- 重启前必须通过 `scripts/dev/check-restart-safety.py`，运行替换后必须验证 backend、worker、health 与真实链路。
- 当前工作树已有用户改动；本轮不得覆盖或回滚无关文件。

---

### Task 1: 固定窗口日志基线

**Files:**
- Read: `logs/slow_query.log`
- Read: `logs/app.log`
- Read: `logs/backend.log`
- Read: `logs/scheduler.log`
- Read: `logs/celery/*.log`
- Read: `scripts/diagnostics/analyze_db_performance_logs.py`

**Interfaces:**
- Consumes: 日志中的 timestamp、request id、SQL origin、Session lifecycle、Celery outcome。
- Produces: SQL、端点、任务、错误、连接和 PgBouncer 的窗口统计与主要问题排序。

- [ ] **Step 1:** 运行固定窗口分析器，并保存完整输出到 `artifacts/db-performance/2026-09-03-log-analysis.txt`。
- [ ] **Step 2:** 运行上一窗口同口径分析并逐项比较新增、恶化、改善、反复。
- [ ] **Step 3:** 对累计耗时、P95、最大耗时、失败率和复发次数最高的记录提取原始日志证据。

### Task 2: 数据库物理层与调用链取证

**Files:**
- Read: `scripts/diagnostics/db_bottleneck_report.py`
- Read: `docs/rules/database-session-lifecycle-discipline.md`
- Read: `docs/bugs/recurrence-registry.json`

**Interfaces:**
- Consumes: Task 1 的指纹、request id、origin 和重点表。
- Produces: pg_stat、表/索引规模、EXPLAIN 与应用等待/数据库 executor 的根因分类。

- [ ] **Step 1:** 运行只读数据库瓶颈报告，禁止任何写事务或 DDL。
- [ ] **Step 2:** 对主要 SQL 指纹关联真实 SQL、调用来源、连接持有与外部 I/O。
- [ ] **Step 3:** 只有在真实 workload、pg_stat 与 EXPLAIN 同时支持时才生成 DBeaver 人工 SQL；否则明确记录“不建议 DDL”。

### Task 3: 根因修复与防复发门禁

**Files:**
- Modify: 由 Task 2 根因定位决定的最小应用文件。
- Test: 与根因对应的 `tests/unit/test_*.py`。
- Modify: `docs/bugs/recurrence-registry.json`
- Create: `docs/bugs/2026-09-03-<root-cause>.md`（仅在确认新根因时）。

**Interfaces:**
- Consumes: 可稳定复现且调用链闭合的根因。
- Produces: 先红后绿的回归、单一最小修复、复发键和下一窗验收条件。

- [ ] **Step 1:** 编写最小失败回归并运行，确认它因目标根因失败。
- [ ] **Step 2:** 实施只修根因的最小代码变更，不夹带无关重构。
- [ ] **Step 3:** 运行定向测试并更新 Bug/recurrence 防复发合同。

### Task 4: 完整验证与运行态验收

**Files:**
- Read: `scripts/dev/check-restart-safety.py`
- Read: `scripts/dev/restart-dev-stack.sh`

**Interfaces:**
- Consumes: Task 3 的代码与测试变更。
- Produces: 定向测试、完整 unit、compileall、Bug strict、diff check 与可归因的运行态证据。

- [ ] **Step 1:** 运行定向测试、完整 unit、compileall、`git diff --check`、Bug 严格检查和现有数据库生命周期门禁。
- [ ] **Step 2:** 若运行时代码改变，先运行重启安全门；仅在无不可中断任务时使用标准重启脚本。
- [ ] **Step 3:** 检查 backend/worker ready、health、idle-in-transaction，并用自然或安全的真实 API/任务验收；未捕获目标动作时保持 OBSERVING。

### Task 5: 报告与记忆

**Files:**
- Create: `docs/bugs/reviews/2026-09-03-db-performance-audit.md`
- Modify: `/Users/v9/.codex/automations/db/memory.md`

**Interfaces:**
- Consumes: Tasks 1-4 的全部证据。
- Produces: 中文根因闭环状态表、人工动作、风险、未捕获边界与下一轮验收计划。

- [ ] **Step 1:** 写巡检报告，逐项标记 OPEN、OBSERVING、RUNTIME_REPLACED 或 CLOSED，并说明依据。
- [ ] **Step 2:** 用当前运行时间把本轮摘要前置写入自动化 memory。
- [ ] **Step 3:** 复核报告中不存在把“未捕获”误写成“未发生”或把测试通过替代运行态证据的表述。
