# 自动图片重放把已完成视频父任务降回处理中

## 基本信息

- 状态：代码、回归与现场任务收口已完成，待新 worker 验收
- 严重级别：P0
- 影响模块：火柴人视觉视频 / 自动图片初始化 / 重启安全门
- 首次发现：2026-07-24
- 修复提交：当前工作区
- 根因分类：终态非单调、异步任务重放缺少幂等保护
- 复发键：`ORCH-PARENT-CHILD-STATE` / `RUNTIME-TOPOLOGY-RELOAD`
- 是否复发：第 N 次；再次出现晚到任务覆盖较新终态
- 关联记录：`2026-07-10-report-audio-completed-parent-redelivered.md`、`2026-07-22-terminal-polling-image-repair-payload-recurrence.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

- 视频渲染子任务已于 12:21 完成，父任务已拥有同一视频路径且文件可下载。
- 12:22 自动图片初始化重放，发现 44 张图片全部已完成，却无条件把父任务写成 `processing/images_done/80%`。
- 重启安全门因此持续把已完成业务误判为不可中断任务。

## 影响

- 页面和监控显示任务长期处理中，用户无法确认真实终态。
- 安全重启被错误阻断，诱发人工绕过门禁或强杀 worker 的风险。

## 根因

- 自动图片初始化的父任务更新没有检查已有可用视频，也没有遵守终态单调性。
- `queued=0/skipped=全部/errors=0` 本应是幂等重放，却仍覆盖父任务状态。

## 解决思路

- 已有视频文件、所有图片完成、没有新任务入队且没有错误时，自动图片任务只返回统计，不再写父任务。
- 历史误回退任务必须以已完成视频子任务和相同输出路径为双重证据收口。

## 实际方案

- 新增 `_preserve_completed_video_state`，将终态保护放在父任务 UPDATE 之前。
- 现场任务 `fde84efa-3885-4290-9f85-601e4a347b98` 通过 CTE 校验已完成子任务和相同视频路径后，原子恢复为 `completed/video_done/100%`。

## 验证

- 单元测试覆盖“已有视频 + 全部图片完成 + 零入队 + 零错误”必须保留终态，以及出现新入队时不得误保留。
- 现场收口后重启安全门返回 `OK: no non-interruptible processing tasks found`。
- 全量单测通过。

## 复发预防

- 异步任务重放不得把 completed/canceled 等终态写回 processing/pending。
- 所有父状态回写必须声明是否允许降级；默认禁止终态降级。
- 重启门禁发现长时间无心跳任务时，必须核对结构化子任务和产物，不得只看父状态。

## 后续事项

- 新 worker 启动后重放一次全完成图片初始化，确认父任务仍保持 completed。
