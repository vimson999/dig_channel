# DB Runtime Snapshot Processing Actor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让每日只读运行态快照准确区分 Publisher pending repair、processing orphan repair 与上传失败的终态 actor，避免把当前 watchdog 和日志外旧运行体混为一类。

**Architecture:** 保持生产业务状态机不变，仅扩展诊断 SQL 投影与纯 Python 汇总。`processing_orphan_repair` 的原因和 actor 单独统计；actor 缺失只作为“不可归因终态”证据，不与“当前节点素材实际存在”的 foreign actor 证据混算。

**Tech Stack:** Python 3、SQLAlchemy/PostgreSQL JSONB、unittest。

**Spec:** 当前自动化提示“每日 DB 慢查询与索引巡检”的终态 writer 可归因与复发门禁要求。

## Global Constraints

- 不修改 Publisher 业务状态机、不执行 DDL、不回填历史数据。
- 保留现有 pending repair、upload failure 与 old-root 指标语义。
- 当前是用户明确指定的现有项目且工作树包含既有自动化改动；原地续跑，不提交、不清理、不覆盖无关变更。
- 新指标只证明诊断可归因性，不能把共享 DB/Redis/broker 的物理隔离标为完成。

## Review Focus

- `queue_repair` 与 `processing_orphan_repair` 是不同 JSON 子树，不能互相代替。
- actor 已存在的当前 watchdog 终态不得计入 actor-missing。
- actor 缺失只计不可归因，不因旧根路径自动伪装成精确主机归因。
- 现有 `foreign_actor_evidence` 口径保持只由可验证的本机素材/视频事实驱动。
- 空 actor、空 processing repair 与非 orphan 任务均应稳定处理。

---

### Task 1: Processing orphan actor diagnostic coverage

**Files:**
- Modify: `tests/unit/test_daily_db_runtime_snapshot.py`
- Modify: `scripts/diagnostics/daily_db_runtime_snapshot.py`

**Interfaces:**
- Consumes: 每个 Publisher 行的 `processing_repair_reason` 与 `processing_repair_actor`。
- Produces: `processing_orphan_closed`、`processing_orphan_actor_missing` 两个汇总指标，并在逐行快照中输出正确子树。

- [ ] **Step 1: Write the failing test**

新增 `test_publisher_summary_distinguishes_processing_orphan_actor_identity`：两个 `orphaned_processing` 行分别携当前 actor 与空 actor，断言 closed=2、actor_missing=1，并断言既有 `foreign_actor_evidence` 不被该口径改变。

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_daily_db_runtime_snapshot.DailyDbRuntimeSnapshotTest.test_publisher_summary_distinguishes_processing_orphan_actor_identity`

Expected: FAIL，缺少 `processing_orphan_closed` 或 `processing_orphan_actor_missing`。

- [ ] **Step 3: Implement minimal diagnostic projection and summary**

在 `summarize_publisher_rows(rows)` 统计 `processing_repair_reason` 非空的终态和 actor 缺失数；在只读 SQL 中从 `result -> 'processing_orphan_repair'` 投影 reason/actor。

- [ ] **Step 4: Run focused tests**

Run: `PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_daily_db_runtime_snapshot`

Expected: 所有该文件测试 PASS。

- [ ] **Step 5: Production read-only replay**

Run: `PYTHONPATH=src/backend venv/bin/python scripts/diagnostics/daily_db_runtime_snapshot.py --since '2026-10-25 06:00:00' --until '2026-10-26 06:00:00'`

Expected: 两条 processing orphan 均被统计，其中一条 actor 缺失；逐行证据区分当前 Mac mini actor 与不可归因旧 writer。
