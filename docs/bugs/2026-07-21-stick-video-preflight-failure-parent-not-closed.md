# 火柴人视频预检失败未关闭父任务

## 基本信息

- 状态：已修复、历史任务已收口并部署验收
- 严重级别：高
- 影响模块：火柴人视频 / render worker / 父子任务状态机
- 首次发现：2026-07-21 真实视频合成任务
- 修复提交：待提交
- 根因分类：早退失败路径缺少父任务传播参数
- 复发键：`ORCH-PARENT-CHILD-STATE`
- 是否复发：是；父子任务终态不一致簇的新入口
- 关联记录：`2026-06-26-report-audio-publisher-child-completed-parent-waiting.md`、`2026-07-07-mp3-video-publisher-file-done-parent-waiting.md`
- 防线层级：shared_guardrail / contract_test / runtime_acceptance / observability

## 现象与影响

- 子任务 `8350b635-9a7f-4aa8-910e-22b7d03ddb76` 因“第 1 段图片尚未完成”在 2.04 秒内失败。
- 父任务 `48c6592c-082a-438c-be4c-fe0c81342284` 仍停留在 `processing/video_queued/90`，继续等待已经失败的子任务。
- 重启安全门会把这个伪运行任务视为活动任务，页面也无法给出真实终态。

## 根因

视频 worker 的 3 个预检早退分支调用 `_mark_failed` 时没有传入已经解析出的 `workflow_id`。子任务本身被正确标为失败，但共用收口函数无法定位父任务，因此父状态传播被跳过。

## 实际方案

- 3 个预检失败分支统一传入 `workflow_id=workflow_id`。
- 父任务失败收口同时写入 `completed_at`，保证终态字段完整。
- 使用正式 `_mark_failed` 路径原子关闭现场父子任务，不直接修改结果 JSON。

## 验证

- 回归测试覆盖“预检失败必须把 workflow ID 传给收口函数”。
- 现场父子任务均已变为明确失败终态，重启安全门再次检查为 0 个活动任务。
- 完整回归通过，修复已由新 render worker 加载。

## 复发预防

1. 所有子任务失败入口必须携带父任务标识，早退分支与异常分支使用同一收口合同。
2. 父任务进入 failed/completed/canceled 时必须同步写 `completed_at`。
3. 父子状态契约测试必须覆盖主流程之前的输入与资产预检失败。
