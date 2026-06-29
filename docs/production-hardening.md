# 生产级运行加固

这套系统不能靠“把 SQLAlchemy pool 调大”解决连接问题。生产策略是：应用少持有连接，PgBouncer 复用连接，任务入口按全局预算准入，数据库压力高时延迟而不是硬冲。

事故排查、重启、暂停、封皮标题、发布成功判定等运行纪律见 [工作流运行经验与事故处理纪律](WORKFLOW_RUNTIME_OPERATIONS_PLAYBOOK.md)。
数据库 session 生命周期专项纪律见 [数据库 Session 生命周期纪律](rules/database-session-lifecycle-discipline.md)。

## 数据库连接

- 应用连接 `PgBouncer:6432`，PgBouncer 再连接 PostgreSQL。
- Web 进程保留小连接池：`WEB_DB_POOL_MODE=queue`，`WEB_DB_POOL_SIZE=4`。
- Celery worker、scheduler、watchdog 使用 `NullPool`：`WORKER_DB_POOL_MODE=null`，`SCHEDULER_DB_POOL_MODE=null`。
- 所有连接带 `application_name`，可在 `pg_stat_activity` 里定位来源。
- 应用层必须保证任务、scheduler、watchdog、CLI 每个工作单元后释放 SQLAlchemy session；PgBouncer 不能替应用关闭未结束事务。
- PostgreSQL 建议设置：

```sql
ALTER SYSTEM SET max_connections = '150';
ALTER SYSTEM SET idle_in_transaction_session_timeout = '60s';
ALTER SYSTEM SET statement_timeout = '120s';
ALTER SYSTEM SET lock_timeout = '10s';
SELECT pg_reload_conf();
```

如果无权 `ALTER SYSTEM`，优先按业务账号设置角色级硬保险：

```sql
ALTER ROLE cappa_rw SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE cappa_rw SET statement_timeout = '120s';
ALTER ROLE cappa_rw SET lock_timeout = '10s';
```

如果 PgBouncer 配置了 `ignore_startup_parameters = options`，连接串里的 `options=-c statement_timeout=...` 不会生效，必须使用上面的角色级或数据库级配置。

4c16g 机器不建议把 `max_connections` 无脑调到几百上千；连接多会吃内存和上下文切换，PgBouncer 才是主要保护层。

## 任务准入

代码侧通过 `services.runtime_guard` 在提交和启动任务时做保护：

- DB 空闲连接低于 `TASK_ADMISSION_DB_MIN_FREE_CONNECTIONS` 时延迟任务。
- DB 使用率超过 `TASK_ADMISSION_DB_MAX_USED_RATIO` 时延迟任务。
- `idle in transaction` 超过 `TASK_ADMISSION_DB_MAX_IDLE_IN_TX` 时延迟任务。
- 同类运行任务超过 `TASK_ADMISSION_FEATURE_LIMITS` 时延迟启动。
- 同类排队/运行任务超过 `TASK_ADMISSION_FEATURE_BACKLOG_LIMITS` 时延迟提交。

延迟不是失败。任务会保持 `pending`，步骤会标为 `deferred_db_pressure`、`deferred_capacity` 或 `deferred_backlog`，并在 `result.runtime_guard` 写入原因。

## 队列自恢复

容量保护只负责“该不该延迟”，不能负责“任务是否还活着”。watchdog 负责第二层恢复：

- `CELERY_AUTO_HEAL_ENABLED=true`：允许自动重启消费卡住的 worker。
- `CELERY_AUTO_HEAL_REQUEUE_STALE_TASKS=1`：允许自动重投业务表 pending 但 Celery 消息不可见的孤儿任务。
- repair 只重投同一个 `PublicationTask.id`，不新建业务任务，避免重复发布或重复清洗。
- repair 尊重 `CELERY_PAUSED_SERVICES` 和页面 pause 状态。

## 同步任务瞬断重试

知识星球同步会同时做远端拉取、附件下载和数据库落库。附件已经落盘但更新 `zsxq_attachments` 时如果 PostgreSQL 主动断开连接，当前事务会回滚；这类失败不能当成功，也不能立即把整轮 workflow 判死。

- `ZSXQ_SYNC_DB_RETRIES=5`：`zsxq_sync` 遇到可重试数据库异常时最多重试 5 次。
- `ZSXQ_SYNC_DB_RETRY_DELAY_SECONDS=60`：退避基数 60 秒，按 60/120/180... 递增，最长 600 秒。
- 重试前必须 `rollback` 并释放 SQLAlchemy session，避免带着坏连接继续执行。
- 只有重试耗尽后才写失败和发送失败通知。

## 看门狗暂停语义

人为暂停 worker 后，看门狗和后端自举不会自动拉起。可用：

```bash
curl -X POST http://localhost:5001/api/v1/celery/pause/report_audio_workflow
curl -X POST http://localhost:5001/api/v1/celery/resume/report_audio_workflow
```

也可以用环境变量固定暂停：

```bash
CELERY_PAUSED_SERVICES=report_audio_workflow,mp3_video_workflow
```

## 观测项

必须持续看这些指标：

- PostgreSQL: `total connections`, `idle in transaction`, `waiting clients`, `long transactions`
- PgBouncer: `SHOW POOLS;`, `SHOW CLIENTS;`, `SHOW SERVERS;`
- Celery: queue length, active/reserved/scheduled, retry count, deferred task count
- 业务: workflow age, pending/processing task age, publish verified count

## 验证 SQL

```sql
SELECT application_name, state, count(*)
FROM pg_stat_activity
WHERE usename = current_user
GROUP BY application_name, state
ORDER BY count(*) DESC;
```

```sql
SELECT state, count(*)
FROM pg_stat_activity
GROUP BY state
ORDER BY count(*) DESC;
```
