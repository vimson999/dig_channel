# DB 根因闭环实施计划

> 已于 2026-08-29 获用户批准；允许按安全门修改数据库并重启项目。

## 1. Notification outbox 红灯测试

文件：

- `tests/unit/test_workflow_notification_outbox.py`
- `tests/unit/test_workflow_notification_retry_policy.py`
- `tests/unit/test_workflow_notification_service.py`

先增加失败测试：稳定 dedupe key、重复 enqueue 只建一条 delivery、DB 提交后才 broker、broker 失败仍 pending、并发租约唯一、20 event 故障只连接一次、恢复后全部 sent、临时与永久 SMTP 错误分类、非协议错误禁止 fallback。

运行：

```bash
PYTHONPATH=src/backend venv/bin/python -m unittest \
  tests.unit.test_workflow_notification_outbox \
  tests.unit.test_workflow_notification_retry_policy \
  tests.unit.test_workflow_notification_service -v
```

## 2. Additive model 与 migration

文件：

- 新增 `src/backend/models/workflow_notification.py`
- 修改 `src/backend/models/__init__.py`
- 新增 `src/backend/migrations/versions/20260829_workflow_notification_outbox.py`
- 修改 `scripts/dev/check-runtime-schema.py`

实现两张表、唯一约束、due/lease 索引与 additive migration。down revision 指向 `20260826_content_pipeline_canonical_cleaned_assets`。runtime schema 先允许兼容部署，再在代码切换前要求新表完整。

## 3. Outbox service 与 drain task

文件：

- 新增 `src/backend/services/workflow_notification_outbox.py`
- 修改 `src/backend/services/workflow_notification_service.py`
- 修改 `src/backend/tasks/workflow_notify.py`
- 修改 `src/backend/celery_config.py`
- 必要时修改记录 delivery id 的 MP3、report-audio、ZSXQ 调用方。

顺序：短事务 upsert → release → broker；短事务 lease → release → SMTP → 新短事务 CAS。加入 provider breaker、half-open probe、恢复 reconciler、稳定事件 header 和结构化日志。删除 per-event Celery retry 风暴路径，保留兼容旧 raw payload task 的有界迁移入口。

## 4. 应用 notification migration

先运行：

```bash
venv/bin/python scripts/dev/check-restart-safety.py
```

然后以专用默认 dry-run/显式 apply 工具应用 additive migration，记录数据库、角色、锁、表/索引不存在到存在、约束、空表行数与回滚 SQL。任何门禁失败立即停止，不重启。

## 5. PgBouncer TDD 与 A/B

文件：

- `scripts/dev/setup-local-pgbouncer.py`
- `tests/unit/test_local_pgbouncer_config.py`
- 新增 `scripts/diagnostics/benchmark_pgbouncer_lifetime.py`
- `scripts/diagnostics/analyze_db_performance_logs.py`
- `tests/unit/test_db_performance_log_analyzer.py`

先测试候选参数、配置输出、失败分类与建连 churn 聚合。再执行 900/1800 秒只读 A/B，保存请求成功率、P50/P95/max、连接创建/失败、pool/server 快照。仅将证据最好的候选写入运行配置。

## 6. 索引维护

### Bilibili

重新 dry-run `scripts/maintenance/apply_bilibili_competitor_bvid_index_cleanup.py`，分别验收
`video-bvid` 与同源复核新增的 `competitor-uid`；安全门通过后才显式 `--apply`，保存每个目标的
JSON pre/post/lookup/rollback 证据，并用 fresh-install 与 runtime schema 契约防止独立索引重建。

### Comment hash

先重写 `scripts/maintenance/apply_comment_hash_index_cleanup.py` 并增加 `tests/unit/test_comment_hash_index_cleanup.py`：默认 dry-run、显式 apply、精确 allowlist、owner/validity/usage/definition/lock/pre-post/rollback 门禁。高使用 `group_tab_type` 永久排除。`group_type` 只有在 EXPLAIN 证据成立时单独 apply，否则保留并记录原因。

同时修复 `check-runtime-schema.py` 的 Alembic ancestry 判断和对应测试。

## 7. 完整验证与重启

依次运行定向测试、全量 unit、`compileall -q src/backend`、相关 Node 契约、`git diff --check`、registry JSON 与 Bug strict。

重启前再次运行安全门；只有 0 个不可中断任务才执行 `scripts/dev/restart-dev-stack.sh`。重启后检查 backend health、Redis/broker、17 个服务、workflow_notify worker、队列和 activity。

## 8. 运行态与复发窗口

- 受控 SMTP 断连/恢复验收 durable pending、全局熔断和最终补发。
- 真实 MP3 中文 IME 记录 composing/in-flight/request id。
- 受控 ZSXQ publish DB 瞬断验证 Session 清理，不擅自发布真实外部内容。
- 运行日志重新量化页面/API/worker、SQL、Session、PgBouncer 与 notification delivery。
- 更新 Bug、recurrence registry、每日巡检报告和 automation memory。

没有完整后窗时标记 `RUNTIME_REPLACED/OBSERVING`，不得写成已解决。
