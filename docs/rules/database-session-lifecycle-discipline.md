# 数据库 Session 生命周期纪律

本文档约定项目内所有 Web API、Celery 任务、scheduler、watchdog、CLI 脚本和诊断脚本的数据库连接使用规则。目标是把“数据库又被打挂”的问题从临时补丁变成硬约束。

## 核心判断

- `idle` 连接可以存在，但必须由连接池、PgBouncer 和超时参数限制数量与寿命。
- `idle in transaction` 连接不应长期存在。后台任务里出现超过 60 秒的 `idle in transaction`，按事故处理。
- 连接池不是并发控制器。业务并发要靠队列、准入、限流、状态机和重试控制。
- PgBouncer 负责削峰，不负责修正应用层未关闭事务。

## 必须遵守

- Web 请求依赖 Flask teardown 清理 session，但长轮询、流式响应和后台线程仍要显式清理。
- Celery task、scheduler、watchdog、CLI、一次性诊断脚本必须在每个工作单元的 `finally` 中执行 `rollback/remove`。
- 父编排等待子任务、浏览器上传、外部平台返回、音视频合成、文件下载等长等待前，必须释放 DB session。
- 释放 session 后，不能继续读旧 ORM 对象；跨等待只保存 ID，后续按 ID 重新加载。
- 查询监控页面、任务快照、自动收口等看似“只读”的代码，也必须释放 session；只读查询同样会开启事务。
- 新增 worker/scheduler/watchdog 时，必须使用角色化连接池配置，并接入数据库压力准入。

推荐释放函数：

```python
def release_db_session_quietly():
    try:
        db.session.rollback()
    except Exception:
        pass
    try:
        db.session.remove()
    except Exception:
        pass
```

## 禁止事项

- 禁止在循环外创建 session，然后跨多轮循环复用。
- 禁止在等待 Celery 子任务时持有 ORM 对象集合。
- 禁止把 `QueuePool timeout`、`server closed the connection unexpectedly` 当普通业务错误吞掉。
- 禁止只靠调大 PostgreSQL `max_connections` 掩盖应用泄漏。
- 禁止新功能重新实现已成熟链路的等待、重试、session 释放和状态收口。

## 标准配置

- Web：小 QueuePool，默认 `WEB_DB_POOL_SIZE=4`，`WEB_DB_MAX_OVERFLOW=0`。
- Worker / scheduler / watchdog：默认 `NullPool`，任务用完即还。
- PgBouncer：transaction pool，限制服务端连接，允许应用端短连接进入排队。
- PostgreSQL 服务端建议设置硬保险：
  - `idle_in_transaction_session_timeout`
  - `statement_timeout`
  - `lock_timeout`

注意：如果 PgBouncer 配置了 `ignore_startup_parameters = options`，应用通过连接串 `options=-c statement_timeout=...` 设置的超时不会生效。此时必须用数据库角色或数据库级别的 `ALTER ROLE` / `ALTER DATABASE` 设置硬保险。

## 健康验收

任一重启或修复后，至少检查：

```sql
SELECT state, count(*)
FROM pg_stat_activity
WHERE datname = current_database()
GROUP BY state
ORDER BY count(*) DESC;
```

```sql
SELECT pid, application_name, state, now() - xact_start AS xact_age, query
FROM pg_stat_activity
WHERE state = 'idle in transaction'
ORDER BY xact_start NULLS LAST;
```

验收标准：

- `idle in transaction` 为 0，或仅有正在结束的短事务且小于 60 秒。
- worker/scheduler/watchdog 重启后一轮内不留下长事务。
- 业务列表 API、Celery 看板、自动收口逻辑不会因为只读查询留下事务。

## 新功能检查清单

新增或改造功能前必须回答：

- 是否有长等待、外部请求、文件处理、浏览器自动化或子任务轮询？
- 等待前是否释放 session？
- 等待后是否按 ID 重新加载，而不是读旧 ORM？
- 失败、重试、取消、超时路径是否都会 `rollback/remove`？
- 页面轮询接口是否避免返回大 JSON，并在查询后释放 session？
- 是否有测试或现场 SQL 能证明没有 `idle in transaction`？

## 关联文档

- [工作流运行经验与事故处理纪律](../WORKFLOW_RUNTIME_OPERATIONS_PLAYBOOK.md)
- [成功经验复用纪律](reuse-proven-patterns-discipline.md)
- [Bug 修复记录纪律](bugfix-record-discipline.md)
- [Celery Watchdog 留下 idle in transaction 连接](../bugs/2026-06-24-watchdog-idle-in-transaction.md)
