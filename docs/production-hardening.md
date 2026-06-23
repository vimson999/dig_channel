# 生产级运行加固

这套系统不能靠“把 SQLAlchemy pool 调大”解决连接问题。生产策略是：应用少持有连接，PgBouncer 复用连接，任务入口按全局预算准入，数据库压力高时延迟而不是硬冲。

## 数据库连接

- 应用连接 `PgBouncer:6432`，PgBouncer 再连接 PostgreSQL。
- Web 进程保留小连接池：`WEB_DB_POOL_MODE=queue`，`WEB_DB_POOL_SIZE=4`。
- Celery worker、scheduler、watchdog 使用 `NullPool`：`WORKER_DB_POOL_MODE=null`，`SCHEDULER_DB_POOL_MODE=null`。
- 所有连接带 `application_name`，可在 `pg_stat_activity` 里定位来源。
- PostgreSQL 建议设置：

```sql
ALTER SYSTEM SET max_connections = '150';
ALTER SYSTEM SET idle_in_transaction_session_timeout = '60s';
ALTER SYSTEM SET statement_timeout = '120s';
ALTER SYSTEM SET lock_timeout = '10s';
SELECT pg_reload_conf();
```

4c16g 机器不建议把 `max_connections` 无脑调到几百上千；连接多会吃内存和上下文切换，PgBouncer 才是主要保护层。

## 任务准入

代码侧通过 `services.runtime_guard` 在提交和启动任务时做保护：

- DB 空闲连接低于 `TASK_ADMISSION_DB_MIN_FREE_CONNECTIONS` 时延迟任务。
- DB 使用率超过 `TASK_ADMISSION_DB_MAX_USED_RATIO` 时延迟任务。
- `idle in transaction` 超过 `TASK_ADMISSION_DB_MAX_IDLE_IN_TX` 时延迟任务。
- 同类任务超过 `TASK_ADMISSION_FEATURE_LIMITS` 时延迟任务。

延迟不是失败。任务会保持 `pending`，步骤会标为 `deferred_db_pressure` 或 `deferred_capacity`，并在 `result.runtime_guard` 写入原因。

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
