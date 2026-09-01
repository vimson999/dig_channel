# 知识星球发布状态同步超时污染 Session 并丢失完成通知

## 基本信息

- 状态：RUNTIME_REPLACED / AWAITING_REAL_TASK_ACCEPTANCE
- 严重级别：高
- 影响模块：ZSXQ publish / attachment publish status / content pipeline notification / SQLAlchemy Session
- 首次发现：2026-08-29 每日数据库性能巡检
- 修复提交：当前工作区
- 根因分类：数据库瞬断后的 Session 生命周期 / best-effort 异常吞噬 / 通知级联失败
- 复发键：`DB-SESSION-LIFECYCLE`
- 是否复发：是；同类“数据库异常后未 rollback 即继续外部 I/O”再次出现
- 关联记录：`2026-07-22-zsxq-publish-attachment-status-n-plus-one.md`、`2026-08-08-postgres-tunneled-route-query-timeout.md`
- 防线层级：local_guard / contract_test / runtime_acceptance

## 现象与影响

Celery delivery `4f2878f1-2a42-4b78-b98b-7b3dbcb0cc37` 处理发布任务 `739f6bbf-18e4-44d8-abd1-f8ad496736e1`。18:43:49，附件状态同步为读取来源任务 `456ab87b-89c8-4028-a167-133f9e5dafa5` 执行一次窄点查，应用等待 22.247 秒后遇到 PgBouncer query timeout。

发布业务已经完成，但旧 `_update_attachment_publish_status` 捕获并吞掉异常，没有 rollback/remove。随后 `publish_completed` 通知在同一失败 Session 上调用外部 Celery publish，`apply_async_after_db_release` 触发 `PendingRollbackError`，日志明确记录 `queued=False reason=queue_failed`。结果是发布任务返回成功，但附件状态可能未同步、完成通知确定未入队。

## 根因

第一层根因仍是 PgBouncer/网络导致的 query timeout，而不是 PostgreSQL executor 或索引；第二层可修复根因是 best-effort wrapper 将数据库异常降级成日志后继续执行，却没有恢复 Session 不变量。异常吞噬把一个局部状态同步失败扩散成后续通知失败。

## 实际方案

- 附件发布状态同步对可重试数据库错误最多执行两次；重试前显式 rollback，并在每次尝试结束后 remove Session。
- 非重试错误同样 rollback/remove 并返回失败；不会把失败 Session 带到通知或工作流入队边界。
- 成功路径也 remove Session，确保后续 broker I/O 不持有空闲事务或连接。
- 保留原始任务 result、附件字段和审计数据；没有把状态伪造成成功，也没有自动执行历史数据回填。

## 验证与后续

- 两个单测先在旧代码明确失败：瞬断时没有重试/清理，非重试错误也没有 rollback/remove。
- 修复后 `test_zsxq_publish_state_updates` 7 项通过；瞬断路径断言调用两次、rollback 一次、remove 两次，非重试路径断言清理一次并返回失败。
- 当前代码需要新的 zsxq_publish worker 加载并由真实发布任务验证。历史任务的附件状态和漏失通知未做无人值守回填；需通过现有 backfill/重发流程先只读对账，再由人工确认数据动作。
- 下一轮要求真实 transient 注入或自然瞬断下出现一次有界 retry、无后续 `PendingRollbackError`、附件状态最终匹配、`publish_completed queued=True`，并在后续完整窗口无复发后才可转 CLOSED/RUNTIME_ACCEPTED。

## 2026-08-29 运行替换

重启安全门读取 47 条 publication task 和 0 条活动 ZSXQ sync run，确认没有不可中断任务；标准整栈重启后 backend PID 14264 healthy，broker OK，17 个服务全部 running、无 paused/active/reserved/scheduled。新 zsxq_publish worker PID 14749 于 06:22:20 ready，已加载当前代码。

只读对账确认受影响历史任务仍是 `completed/done`，source task id 正确；没有自动重放通知或回填附件状态。新 worker 尚未收到新的真实发布任务，也没有可安全制造的生产 query timeout，因此只有运行替换证据，没有行为验收，状态保持 `RUNTIME_REPLACED / AWAITING_REAL_TASK_ACCEPTANCE`。

## 2026-08-31 后窗复核

固定窗口和巡检时只读对账均没有新的 `zsxq_publish` 任务，故不能制造发布、回填附件或伪造通知来完成
验收。虽然本轮真实 `topics/local` 已捕获一次 PgBouncer query timeout，但它不经过发布状态同步路径，
不能替代该 Bug 的真实任务证据。状态继续 `RUNTIME_REPLACED / AWAITING_REAL_TASK_ACCEPTANCE`；
下一次自然发布必须核对有界重试、Session 清理、附件最终状态与 durable `publish_completed` delivery。
