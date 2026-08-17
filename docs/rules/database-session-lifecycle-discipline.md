# 数据库 Session 生命周期纪律

本文档约定项目内所有 Web API、Celery 任务、scheduler、watchdog、CLI 脚本和诊断脚本的数据库连接使用规则。目标是把“数据库又被打挂”的问题从临时补丁变成硬约束。

## 核心判断

- `idle` 连接可以存在，但必须由连接池、PgBouncer 和超时参数限制数量与寿命。
- `idle in transaction` 连接不应长期存在。后台任务里出现超过 60 秒的 `idle in transaction`，按事故处理。
- 连接池不是并发控制器。业务并发要靠队列、准入、限流、状态机和重试控制。
- PgBouncer 负责削峰，不负责修正应用层未关闭事务。
- `commit()` 通常会归还当前连接，但默认会过期 ORM 属性。提交后再读取 ORM 对象，可能立即重新借出连接；因此长耗时边界必须使用纯 DTO/字典快照，不能继续传 ORM 对象。
- “及时释放连接”指及时归还当前逻辑租约并结束事务，不是每条 SQL 都创建新的物理 PostgreSQL 连接。SQLAlchemy/PgBouncer 可以复用物理连接，业务代码只负责缩短 checkout 到 checkin 的持有窗口。

## 标准三阶段

所有网络、浏览器、AI、音视频、文件下载和 Celery broker 操作都拆成三个阶段：

1. 短事务读取必要列，校验状态，生成纯标量/字典/DTO 快照，并提交状态。
2. 结束事务并释放 session，只携带 ID 和快照执行外部耗时操作。
3. 外部操作完成后按 ID 重新加载，在新短事务中写结果；失败也用新短事务记录。

数据库事务只负责数据库原子性。不能为了追求“数据库记录和 Celery 消息同时成功”而把事务跨到 Redis；需要通过预分配 task ID、幂等任务、入队失败状态和 watchdog 补偿实现最终一致性。

能在数据库外完成的 JSON 解析、哈希、文件检查、提示词构造、音视频处理和远程参数准备，应在申请数据库连接前完成。需要依赖数据库结果的计算，先用短事务读取最小投影并生成 DTO，再释放连接后计算；最后只用一个短写事务落库。

## 必须遵守

- Web 请求依赖 Flask teardown 清理 session，但长轮询、流式响应和后台线程仍要显式清理。
- Celery task、scheduler、watchdog、CLI、一次性诊断脚本必须在每个工作单元的 `finally` 中执行 `rollback/remove`。
- 父编排等待子任务、浏览器上传、外部平台返回、音视频合成、文件下载等长等待前，必须释放 DB session。
- Celery `apply_async`、Redis、HTTP、Playwright、LLM、TTS 和对象存储调用前，必须确认 `session.new/dirty/deleted` 为空，并结束只读事务。
- `sleep`、指数退避、人工节流和等待子任务同样属于外部等待，进入前必须结束事务。
- 任务创建必须遵循“落库并提交 -> 释放连接 -> broker 投递 -> 短事务回写 celery_task_id”。broker 失败时把已落库任务标为 `failed/enqueue_failed`，由状态机和修复器收口。
- 释放 session 后，不能继续读旧 ORM 对象；跨等待只保存 ID，后续按 ID 重新加载。
- 查询监控页面、任务快照、自动收口等看似“只读”的代码，也必须释放 session；只读查询同样会开启事务。
- 新增 worker/scheduler/watchdog 时，必须使用角色化连接池配置，并接入数据库压力准入。
- 数据库异常进入恢复分支后，任何查询、更新或提交之前必须先 `rollback/remove`；禁止在可能失效的 session 上二次查询，也禁止用 `finally: commit()` 收口这类分支。
- 可重试数据库异常在 service 层不得提前写业务终态。由任务层先写 `retrying` 再安排 retry，只有重试耗尽才允许写 `failed`。

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
- 禁止 `flush -> apply_async/HTTP/浏览器 -> commit`；`flush` 后事务仍未提交，会在整个外部等待期间占用连接和锁。
- 禁止在 `commit()` 后把过期 ORM 对象传入外部调用；必须在提交前生成快照，再释放 session。
- 禁止把 `QueuePool timeout`、`server closed the connection unexpectedly` 当普通业务错误吞掉。
- 禁止只靠调大 PostgreSQL `max_connections` 掩盖应用泄漏。
- 禁止新功能重新实现已成熟链路的等待、重试、session 释放和状态收口。

## 标准配置

- Web：小 QueuePool，默认 `WEB_DB_POOL_SIZE=4`，`WEB_DB_MAX_OVERFLOW=0`。
- Worker / scheduler / watchdog：默认 `NullPool`，任务用完即还。
- PgBouncer：transaction pool，限制服务端连接，允许应用端短连接进入排队。本地远端
  PostgreSQL 链路保留 `min_pool_size=6` 与 `server_idle_timeout=600`，避免页面并发
  退化为冷连接扇出；跨 Tailscale 的后端 socket 默认 `server_lifetime=300`，并由
  生成脚本和测试共同锁定。热连接数量与 socket 寿命是两项独立防线，禁止再用
  6 小时长寿命掩盖网络漂移后的陈旧连接。
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
SELECT
    pid,
    application_name,
    state,
    now() - xact_start AS xact_age,
    now() - query_start AS query_age,
    now() - state_change AS state_age,
    wait_event_type,
    wait_event,
    query
FROM pg_stat_activity
WHERE state = 'idle in transaction'
ORDER BY xact_start NULLS LAST;
```

`xact_age` 只表示事务从何时开始，不能单独证明会话已经空闲了多久；判断跨外部等待必须同时看 `state_age`、`query_age`、Pool checkout/checkin 日志和调用链。

验收标准：

- `idle in transaction` 为 0，或仅有正在结束的短事务且小于 60 秒。
- worker/scheduler/watchdog 重启后一轮内不留下长事务。
- 业务列表 API、Celery 看板、自动收口逻辑不会因为只读查询留下事务。
- `db_connection_lifecycle` 不应出现超过默认 5 秒的连接持有；确有长 SQL 时必须同时提供 executor 证据，不能把跨外部等待当成长 SQL。
- 应用侧 SQL 超过 500ms、但相同 SQL 的 PostgreSQL executor 小于 10ms 时，不得新增
  索引；必须检查 PgBouncer `cl_waiting/sv_login/sv_idle`、连接事件和远端网络。
- 全量重启门禁必须覆盖 `publication_tasks` 以及所有独立业务表；当前至少包括近期有
  心跳的 `zsxq_sync_runs.running/retrying`。

## 新功能检查清单

新增或改造功能前必须回答：

- 是否有长等待、外部请求、文件处理、浏览器自动化或子任务轮询？
- 等待前是否释放 session？
- 是否在外部调用前检查了未提交的 `new/dirty/deleted`，并由测试覆盖？
- 等待后是否按 ID 重新加载，而不是读旧 ORM？
- 失败、重试、取消、超时路径是否都会 `rollback/remove`？
- 故障注入是否覆盖“service 抛出 -> retrying 落库 -> Celery retry -> 下一次 delivery 不被终态去重”完整链路？
- 页面轮询接口是否避免返回大 JSON，并在查询后释放 session？
- 是否有测试或现场 SQL 能证明没有 `idle in transaction`？

## 关联文档

- [工作流运行经验与事故处理纪律](../WORKFLOW_RUNTIME_OPERATIONS_PLAYBOOK.md)
- [成功经验复用纪律](reuse-proven-patterns-discipline.md)
- [Bug 修复记录纪律](bugfix-record-discipline.md)
- [Celery Watchdog 留下 idle in transaction 连接](../bugs/2026-06-24-watchdog-idle-in-transaction.md)
