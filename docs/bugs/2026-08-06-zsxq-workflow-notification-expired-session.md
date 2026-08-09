# 2026-08-06 ZSXQ 工作流通知在连续事件间刷新过期 ORM

## 基本信息

- 状态：代码、测试与新 worker 真实通知链路均已验证，继续观察
- 严重级别：P0
- 影响模块：ZSXQ workflow finalizer / workflow notification / zsxq_workflow_runs / PostgreSQL Session
- 首次发现：2026-08-06 11:52:58 运行态慢查询复盘
- 修复提交：当前工作树（未提交）
- 根因分类：DB-SESSION-LIFECYCLE / expired ORM full-row refresh
- 复发键：`DB-SESSION-LIFECYCLE:workflow-notification-commit-expiry`
- 是否复发：第 2 次（与历史 `2026-07-16-zsxq-expired-orm-full-row-refresh.md` 同属过期 ORM 生命周期簇）
- 关联记录：`2026-08-06-zsxq-page-update-roundtrip-amplification.md`、`2026-08-06-zsxq-attachment-state-roundtrip-amplification.md`
- 防线层级：local_patch / shared_guardrail / contract_test / observability

## 现象

11:52:58 的工作流收尾在 `_queue_notification` 内出现一次按主键读取
`zsxq_workflow_runs` 全列的 33,513.64ms 慢查询；同一 checkout 持有 33,626.75ms，
其中 SQL 33,559.39ms、非 SQL 67.36ms。随后 `_advance_workflow_loaded` 又出现一次
1,023.76ms 的工作流点读。该窗口没有 SQL 错误、statement/lock/connection/query timeout
或 idle-in-transaction 事件。

## 影响

通知排队本身只需要冻结的 workflow payload 和一个状态标记，却被远端连接等待放大成
约 33.5 秒的全行回读。它拖慢了 ZSXQ 工作流终态收口，并把通知慢度误看成缺少主键索引；
该查询条件已经是主键，现有证据不支持 CREATE INDEX。

## 根因

`_queue_notification` 第一次 `commit()` 使用默认 `expire_on_commit=True`。当一个收尾路径
连续发送 `sync_failed`、`workflow_failed` 等事件时，第一次通知提交会使 workflow ORM
对象过期；第二次通知先读取 `stage_results`/`to_dict()`，触发整行刷新，再等待远端
PostgreSQL/PgBouncer。原实现在通知排队后的第二次提交还直接写 ORM parent，扩大了跨提交
生命周期的复发面。

## 解决思路

通知是外部 broker I/O，不能把过期 ORM parent 带过提交边界。先在事务边界内冻结 payload
和通知状态，短暂关闭该局部 Session 的 commit expiration；通知入队后只按 workflow 主键
用 Core UPDATE 写回 `stage_results`，并同步 identity-map 的已提交快照，不重新加载整行、
不删除原始 JSON。

## 实际方案

- `_queue_notification` 保存并恢复 `session.expire_on_commit`，连续事件之间保持已加载投影。
- 通知标记改为 `update(ZsxqWorkflowRun).where(id=...).values(stage_results=...)`，并使用
  `synchronize_session=False`；随后以 `set_committed_value` 更新内存投影，避免重复 ORM UPDATE。
- 仍保留提交前 payload 快照、broker 入队后的短事务回写、失败 rollback 和原有幂等判断。
- 未执行 DDL、ALTER、数据回填或破坏性清理；`stage_results` 与原始 JSON 字段完整保留。

## 验证

- 新增回归测试：连续调用 `sync_failed → workflow_failed` 时，捕获 SQL 不出现第二次
  `zsxq_workflow_runs` 全行 SELECT，并验证两个通知标记均持久化。
- 定向 workflow/session/投影测试 86 项通过；此前全量 unit 1,033 项通过。完整测试、
  compileall、diff check、Bug strict 和运行态重载均已在本修复后完成。
- 真实运行态修复前的 33.5 秒样本已保留为对照。12:13:44–12:13:49 的新 worker finalizer
  `b438ed6e-8588-43e1-ab6d-614c254f657e`
  已完成，任务耗时 5.023s；`zsxq_workflow_runs` 读取为 519.14ms，后续窄 UPDATE 为
  35.8/95.69/108.71/225.53ms，没有再出现 33.5 秒全行 refresh 或对应长持有。
- 新 worker 重载后 12:32–12:33 的通知任务（`workflow_started`、`sync_failed`、`sync_completed`）
  均成功；`sync_failed` 8.620s 是通知外部发送长尾，日志没有匹配的 33 秒级 workflow 全行 SQL。
- 最终补丁重载后的 12:46:17–12:46:57 窗口，workflow 任务耗时 3.5196s，3 个通知耗时
  1.3854/1.4278/1.2705s 且全部成功；没有 `zsxq_workflow_runs` 全行 33 秒刷新、connection hold、
  SQL 错误或 timeout。通知外部 I/O 仍是独立的毫秒到秒级长尾，不再与 ORM 过期回读混淆。

## 复发预防

- 任何跨 commit 的 workflow/task/asset 通知路径必须先冻结 DTO，再用无过期短事务和窄 Core
  回写；提交后禁止访问 ORM parent 的延迟属性。
- 回归契约覆盖连续终态事件、broker 入队失败、通知去重以及全列 SELECT 预算。
- DB 分析器继续按 request/Celery task、origin、SQL/non-SQL 持有和 PgBouncer 等待关联；
  主键点查仍按累计耗时/P95/最大值排名，但不凭应用耗时盲加索引。

## 后续事项

- 运行态验收已按门禁完成；后续只需在每日窗口持续观察连续终态通知，不再为该根因增加索引。
