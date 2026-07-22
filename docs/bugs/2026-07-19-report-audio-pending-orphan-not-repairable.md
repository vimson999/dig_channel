# Report audio pending 父任务不在孤儿修复范围

## 基本信息

- 状态：已修复并完成历史任务收口与新进程验收
- 严重级别：P1
- 影响模块：report_audio_workflow / Celery watchdog / publication_tasks
- 首次发现：2026-07-19 重启后监控验收
- 修复提交：未提交
- 根因分类：孤儿修复 feature 矩阵缺项
- 复发键：`ORCH-PARENT-CHILD-STATE`
- 是否复发：同类 pending 消息丢失问题复发
- 关联记录：`2026-06-23-zsxq-workflow-stale-celery-message.md`、`2026-07-02-report-script-material-orphan-pending-after-repair-limit.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

父任务 `0b927507-301c-4581-baa0-b1a8487c7755` 自 2026-07-12 起停留在
`pending / queued / 71%`，Celery 队列和 worker 均无对应消息。其等待子任务投影仍显示
processing，但真实子任务已在 2026-07-11 失败并完成收口。

## 影响

- 任务在监控页长期显示活跃，但永远不会继续。
- 已成功的分段音频和恢复检查点无法被编排复用。
- pending 状态不阻断重启安全门，因此仅靠重启前检查无法发现这类业务遗留。

## 根因

通用 pending 孤儿修复已经覆盖 MP3、文案、Top10、内容清洗和知识星球发布，
但遗漏 `report_audio_workflow/report_pdf_audio_workflow`。report audio 自身已经具备
按恢复检查点复用成功子任务、重试失败分段和有限次数收口的能力，只是旧消息消失后
没有入口再次调用它。

## 解决思路

- 将两个 report audio feature 别名登记到同一个 `report_audio_workflow` 队列。
- 复用正式 `process_report_audio_workflow_job`，不手改结果 JSON。
- 继续要求旧 Celery ID 同时不在 worker 和 Redis 队列中，且任务超过宽限期。
- 保留父任务 ID、恢复检查点和子任务历史，只替换 Celery ID。

## 实际方案

- 扩展 `REPAIRABLE_FEATURES` 与 `_repair_target_for_feature`。
- 新增测试，验证恢复检查点不丢失、队列正确、进度重置为重新排队状态。
- 现场任务重排后复用了已成功分段并创建新的失败分段重试任务。
- 最终父任务在 94% 明确失败：原始 PDF 已按保留策略删除，无法生成首屏截图；
  该结果是可解释终态，不再占用队列或伪装成运行中。
- 再次安全重启，使新映射由正式 watchdog 进程加载。

## 验证

- pending report audio 孤儿单元测试通过。
- 旧消息确认不可见，新消息 `495390fc-0bcb-4a8f-9a2d-75ad710ce4e5` 被新 worker 接管。
- 新豆包子任务 `68125216-c639-449a-9b32-5eb2190070f1` 完成。
- 父任务最终为 `failed / failed / 94%`，错误明确指向已不存在的源 PDF。
- 第二次重启结构门禁和任务安全门均通过，bootstrap 全部服务 `ok=true`、`errors=[]`。

## 复发预防

1. 新增业务 feature 时必须登记队列、pending 修复、processing 修复和阶段边界四项矩阵。
2. 监控验收不能只看 worker running，还必须检查“业务 active 但队列/worker ownership 为零”的历史任务。
3. 父任务等待子任务的结构化投影必须由续跑路径刷新，真实子任务终态优先于旧投影。
4. 历史源文件已过保留期时允许明确失败，但不得继续 pending 或 processing。

## 后续事项

- 评估是否在保留清理前主动关闭已经超过可恢复窗口的历史任务。
