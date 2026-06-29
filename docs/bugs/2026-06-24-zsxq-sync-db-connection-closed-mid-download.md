# 2026-06-24 知识星球同步下载后数据库服务端断开导致整轮失败

## 基本信息

- 状态：已修复
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

## 验证

- 新增单元测试确认 `server closed the connection unexpectedly` 会被识别为可重试数据库异常。
- 新增单元测试确认 `ZSXQ_SYNC_DB_RETRIES` 和 `ZSXQ_SYNC_DB_RETRY_DELAY_SECONDS` 能控制重试次数与退避时间。

## 复发预防

- 新增同步任务瞬断处理运行纪律到 `docs/WORKFLOW_RUNTIME_OPERATIONS_PLAYBOOK.md`。
- 生产加固文档记录同步任务 DB retry 默认值和语义。
- 后续遇到数据库类失败时，必须先区分：
  - 连接池借不到连接：准入/连接预算问题。
  - 服务端断开当前连接：瞬断/重试/事务幂等问题。

## 后续事项

- 继续观察 `zsxq_sync` 是否还有非 retryable 的数据库异常。
- 如果同类断开仍高频发生，需要进一步排查 PostgreSQL 日志、PgBouncer 日志、网络层和数据库重启/OOM 记录。
