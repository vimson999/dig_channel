# 2026-06-24 知识星球同步下载后数据库服务端断开导致整轮失败

## 基本信息

- 状态：2026-07-19 复发后已补齐修复，待新 worker 运行验收
- 严重级别：高
- 影响模块：知识星球同步、系统同步 workflow、数据库连接稳定性
- 首次发现：2026-06-23 23:23 邮件告警
- 修复提交：待提交

## 现象

系统同步 workflow `3920838e-4042-4b2c-a120-715840834c48`，批次 `20260623-231541`，触发方式 `scheduler`，第 7 轮失败。

错误发生在附件下载后的数据库更新阶段：

```text
(psycopg2.OperationalError) server closed the connection unexpectedly
SQL: UPDATE zsxq_attachments SET local_path=..., download_status='downloaded' ...
```

现场排查结果：

- `ZsxqSyncRun` 为失败，`attachments_downloaded=21`，`attachments_skipped=132`。
- 报错里的本地文件 `data/zsxq/28888112822211/调研纪要-hashtag_88844545452542/files/战略性看多券商板块260623.mp3` 已经存在。
- 报错里的附件记录没有稳定落库，说明事务在连接断开后回滚。

## 影响

- 已经下载到本地的附件无法写入 `download_status=downloaded`。
- 整轮系统同步 workflow 被判失败。
- 如果不重试，后续清洗、发布旁路不会按本轮结果继续推进。

## 根因

这次不是 `QueuePool limit ... timed out`，而是 PostgreSQL 或中间连接层主动关闭了正在使用的连接。

`zsxq_sync` 任务此前只放过 Celery 自身的 retry 异常，没有像发布、编排任务一样识别 `server closed the connection unexpectedly` 这类可重试数据库异常。因此一次瞬断直接进入失败分支，导致整轮同步失败。

### 2026-07-19 复发复盘

同步 run `899dc4a5-0a3c-4a12-aac4-01c9f9703ce8` 在附件已下载、准备重新加载附件记录时再次遇到 `server closed the connection unexpectedly`。6 月的修复只覆盖了 Celery task 外层重试，没有覆盖以下两层状态机契约：

1. `_download_topic_attachments()` 的异常分支没有先 `rollback`，却再次查询附件并在 `finally` 中提交，连续触发 `PendingRollbackError`。
2. `sync_tabs()` 在把异常交给 Celery 前先将 run 写为 `failed`；60 秒后重试消息到达时，入口终态去重把它当成重复消息直接跳过。

因此日志看起来“已经安排重试”，业务上却没有真正重试。原修复只测了异常识别，没有覆盖 service 状态落库与下一次 delivery 的完整链路。

## 解决思路

- 数据库瞬断不能被当成普通业务失败。
- 任务必须先 `rollback` 当前 session，避免坏连接继续污染后续 SQL。
- 对可重试数据库异常使用 Celery retry 退避重试。
- 重试期间不发送最终失败通知；只有重试耗尽后才记录为失败。
- 本地和生产配置必须显式暴露重试次数和退避时间，避免靠临时环境变量。

## 实际方案

- `tasks.zsxq_sync.run_zsxq_sync` 引入 `is_retryable_db_error`。
- 遇到可重试数据库异常时：
  - `db.session.rollback()`
  - 按 `ZSXQ_SYNC_DB_RETRIES` 判断是否还能重试
  - 按 `ZSXQ_SYNC_DB_RETRY_DELAY_SECONDS` 计算退避时间
  - `raise self.retry(...)`
- 默认配置：
  - `ZSXQ_SYNC_DB_RETRIES=5`
  - `ZSXQ_SYNC_DB_RETRY_DELAY_SECONDS=60`
- 默认值同步写入：
  - `src/backend/app.py`
  - `scripts/dev/run-backend.sh`
  - `scripts/dev/restart-dev-stack.sh`
  - `scripts/deployment/production.env`

2026-07-19 补充：

- 附件下载成功后的 DB 查询或提交发生 SQLAlchemy 异常时，立即 `rollback` 并原样抛出；禁止在坏 session 上二次查询，也取消 `finally` 无条件提交。
- 普通下载异常仍可在新事务中落 `download_status=failed`，数据库异常不得伪装成附件业务失败。
- `sync_tabs()` 对可重试 DB 异常只回滚并抛出，不再提前写终态。
- Celery 准备重试时将 run 写为 `retrying`；只有重试额度耗尽才写 `failed`。
- 数据库错误诊断读取失效连接的 `connection.info` 时降级为空计时栈，诊断逻辑不得制造第二个异常。

## 验证

- 新增单元测试确认 `server closed the connection unexpectedly` 会被识别为可重试数据库异常。
- 新增单元测试确认 `ZSXQ_SYNC_DB_RETRIES` 和 `ZSXQ_SYNC_DB_RETRY_DELAY_SECONDS` 能控制重试次数与退避时间。
- 2026-07-19 新增故障注入，覆盖“文件已下载、回写前连接断开”：断言只加载一次附件、session 已回滚、附件不被误标失败、异常继续交给 Celery。
- 新增状态机回归，断言 service 层瞬断后 run 保持非终态，重试调度写 `retrying`，仅重试耗尽写 `failed`。
- 新增失效连接日志回归，断言 SQL 诊断中间件不会遮蔽原始数据库异常。
- 知识星球同步、编排、查询纪律和运行时初始化相关测试共 `129` 个通过。

## 复发预防

- 新增同步任务瞬断处理运行纪律到 `docs/WORKFLOW_RUNTIME_OPERATIONS_PLAYBOOK.md`。
- 生产加固文档记录同步任务 DB retry 默认值和语义。
- 后续遇到数据库类失败时，必须先区分：
  - 连接池借不到连接：准入/连接预算问题。
  - 服务端断开当前连接：瞬断/重试/事务幂等问题。
- 重试验收必须覆盖完整链路：service 抛出、run 状态、Celery retry、下一次 delivery 入口；仅测试 `is_retryable_db_error()` 不得判定完成。
- 异常处理器执行任何 SQL 前必须先恢复事务；`finally: commit()` 禁止用于可能经历数据库异常的分支。

## 后续事项

- 继续观察 `zsxq_sync` 是否还有非 retryable 的数据库异常。
- 如果同类断开仍高频发生，需要进一步排查 PostgreSQL 日志、PgBouncer 日志、网络层和数据库重启/OOM 记录。
