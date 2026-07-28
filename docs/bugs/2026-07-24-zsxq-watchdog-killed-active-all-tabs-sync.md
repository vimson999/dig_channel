# ZSXQ watchdog 误杀活动中的全部 Tab 同步

## 基本信息

- 状态：已修复并完成真实任务重投与运行时门禁验收
- 严重级别：P0
- 影响模块：Celery watchdog / ZSXQ sync / Celery Monitor / 全部 Tab 定时同步
- 首次发现：2026-07-24
- 修复提交：未提交
- 根因分类：watchdog 重启入口缺少 ZSXQ 业务心跳门禁、Redis unacked 与真实执行态混淆
- 复发键：`RUNTIME-RESTART-ACTIVE-TASK-GUARD`
- 是否复发：同类问题复发
- 关联记录：`2026-07-18-full-restart-interrupted-active-render.md`、`2026-07-03-zsxq-workflow-queue-failure-and-sync-orphan.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

生财有术全部 Tab 自动同步 `5f908f6f-7a64-41b3-80a1-f9c537b9c0f6` 于
10:13 开始，数据库心跳持续更新并运行到第 13/17 个 Tab。watchdog 在 10:23 起看到
`zsxq_sync` 队列仍有 1 条等待消息，同时 Celery inspect 未报告当前活动任务，于连续三次
确认后在 10:26:36 自动重启 worker，并强杀 PID `73750/73745`。

同步消息留在 Redis `unacked`，数据库行停在 `running`，最近心跳为 10:26。Celery Monitor
因此在半小时后仍显示“进行中”，但实际 `active/reserved/scheduled/queue` 均为 0。

## 影响

- 已完成 13 个 Tab 的远端请求和评论入库后，中断剩余同步。
- 页面把失去执行进程的业务行持续展示为“进行中”。
- 活动行阻止调度器补投同一星球，直到 120 分钟通用超时。
- Redis visibility timeout 到期后旧消息仍可能再次投递。

## 根因

watchdog 为降低数据库轮询，运行循环没有加载 ZSXQ 业务任务快照；`_stop_service` 的
统一重启门禁只覆盖 `publication_tasks` feature，而 `zsxq_sync_runs` 不在该模型内。
因此 watchdog 即使面对近期持续写心跳的 ZSXQ 任务，也会直接停止 worker。

另一方面，Monitor 的残留收口最初把 Redis `unacked` 视为“仍在执行”。被强杀 worker
遗留的 unacked 在 visibility timeout 前不会消失，但它只能证明消息未确认，不能证明
任何 worker 正在执行。

## 实际方案

- `zsxq_sync` 停止/重启门禁直接查询 `zsxq_sync_runs` 的轻量结构化列。
- `running/retrying` 且五分钟内有数据库心跳的同步一律视为不可中断；查询失败时 fail-closed。
- watchdog、监控页单服务重启和其他 `_stop_service` 调用统一复用该门禁。
- Celery Monitor 对全部 Tab 自动同步执行十分钟孤儿判定。
- 孤儿收口只以 Celery `active/reserved/scheduled` 为真实执行依据；Redis unacked 不再替代执行态。
- 运行态检查失败时不收口，防止误杀；收口后的延迟重投由任务终态幂等检查安全跳过。
- 现场旧记录已自动收口为 failed，新任务
  `d8e24081-90d3-472a-a731-1af393c39778` 已按数据库配置重新投递。

## 验证

- Celery Monitor、全部 Tab 服务和调度器定向回归：`43 tests passed`。
- 新增测试覆盖活动任务保护、孤儿任务收口、inspect 失败不收口、ZSXQ worker 运行态引用。
- 后端与 watchdog 分别重载，新 watchdog PID `86463`。
- 真实数据库调用 `_service_stop_guard("zsxq_sync")` 返回
  `active_non_interruptible_tasks`，准确识别新生财任务并拒绝重启。
- 新任务已开始处理“精华”Tab，抓取 39 条，心跳持续更新且无错误。

## 复发预防

- 任何 worker 重启入口都必须使用对应业务表的结构化心跳做 fail-closed 门禁。
- Celery inspect 没看到任务不等于 worker 空闲；近期业务心跳优先阻止破坏性操作。
- Redis unacked 只证明未确认消息存在，不能单独证明任务仍在执行。
- 新增非 `publication_tasks` 业务队列时，必须同时登记停止门禁、孤儿收口和延迟重投幂等测试。

## 2026-07-24 14:50 再次复发与补救

监控页和 watchdog 的单服务入口已经接入 ZSXQ 心跳门禁，但
`scripts/dev/check-restart-safety.py` 的全量 shell 重启入口仍只查询
`publication_tasks`。现场门禁错误返回“无不可中断任务”，完整重启中止了正在运行的
ZSXQ worker，未确认消息随后重新投递。

本次补救不再复制“只保护当前入口”的做法：

- 全量重启门禁同时查询 `publication_tasks.processing` 和
  `zsxq_sync_runs.running/retrying`。
- ZSXQ 只读取 ID、星球、mode、status 和 `updated_at`，五分钟内有心跳即拒绝重启。
- 任一数据源查询失败仍 fail-closed。
- 现场门禁准确识别两个活动同步并返回退出码 `2`；定向门禁与 watchdog 回归
  `81 passed`。
