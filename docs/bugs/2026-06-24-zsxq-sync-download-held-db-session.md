# 2026-06-24 知识星球同步下载期间持有 DB session

## 基本信息

- 状态：已修复，已重启 `zsxq_sync` worker 生效
- 严重级别：高
- 影响模块：知识星球同步、附件下载、数据库连接生命周期
- 首次发现：修复 watchdog `idle in transaction` 后复查 `pg_stat_activity`，发现 `zsxq_sync` 同步阶段会出现短事务串
- 修复提交：待提交

## 现象

重启后复查数据库连接，旧 watchdog 长事务已消失，但同步 worker 在下载阶段仍会反复出现：

```text
application_name = dig_ytb_worker_celery_41246
state = idle in transaction
query = SELECT zsxq_attachments...
```

本次现场观察中事务年龄没有持续超过 60 秒，但它暴露出同类风险：如果某个附件下载很慢，DB 事务可能陪着网络下载一起等待。

## 影响

- 慢下载或大文件下载时可能形成长 `idle in transaction`。
- 长事务会占用连接、保留 MVCC 快照，并放大连接池压力。
- 一旦 PostgreSQL 或 PgBouncer 在下载后切断连接，文件可能已落盘但 DB 状态没有提交，造成后续对账复杂。

## 根因

`ZsxqSyncService._download_topic_attachments()` 与 `_download_manual_attachment_record()` 使用了同一种危险结构：

1. 查询 `ZsxqAttachment` ORM 对象。
2. 基于 ORM 对象判断是否下载。
3. 直接调用 `_download_attachment_file()` 做网络下载。
4. 下载完成后继续在同一个 ORM session 上写 `local_path/download_status`。

也就是说，网络下载动作被夹在数据库 session 生命周期中间。小文件时只是短事务串；大文件、慢网络或平台响应慢时就可能变成长事务。

## 解决思路

下载动作必须和数据库事务分离：

1. 下载前先提交当前记录判断和状态变更，结束 DB 事务。
2. 下载时只拿纯标量字段：`record_id`、`record_name`、`payload`。
3. 下载完成后按 `record_id` 重新加载 `ZsxqAttachment`。
4. 再写入 `local_path/download_status/error_message` 并提交。

这和父编排等待子任务后的原则一致：跨等待不持有 ORM 对象，只保存 ID。

## 实际方案

修改 `src/backend/services/zsxq_sync_service.py`：

- `_download_manual_attachment_record()`：
  - 非目标、复用、已有下载、达到上限等分支改为 `commit` 收口。
  - 下载前 `commit`，结束事务。
  - 下载后 `db.session.get(ZsxqAttachment, record_id)` 重新加载并写回结果。
- `_download_topic_attachments()`：
  - 系统同步附件下载采用同样策略。
  - 下载失败时也重新加载记录后写入 `failed/error_message`。

## 验证

静态验证：

```bash
PYTHONPATH=src/backend venv/bin/python -m py_compile src/backend/services/zsxq_sync_service.py src/backend/tasks/zsxq_sync.py
```

单测验证：

```bash
PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_zsxq_sync_task_retry tests.unit.test_celery_monitor_watchdog
```

运行态验证：

- 当前同步 run `b06bc377-0845-4a8f-b1b9-edc9c0877927` 已完成，下载 21，失败 3。
- 已单独重启 `zsxq_sync` worker，新的下载事务释放策略已加载。

## 复发预防

- 任何下载、上传、浏览器自动化、外部 API 长等待前都必须结束 DB 事务。
- 跨网络等待只允许保存 ID 和纯标量，不允许持有 ORM 对象。
- 新增同步或下载功能必须检查 [数据库 Session 生命周期纪律](../rules/database-session-lifecycle-discipline.md)。
- 现场复查不只看连接数量，还要看 `idle in transaction` 的 `xact_age`。

## 后续事项

- 数据库角色级 `idle_in_transaction_session_timeout` 当前仍为 `0`，需要按生产加固 SQL 开启硬保险。
