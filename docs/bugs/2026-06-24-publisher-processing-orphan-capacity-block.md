# 2026-06-24 Publisher processing 孤儿任务占满并发槽位

## 基本信息

- 状态：已修复
- 严重级别：高
- 影响模块：publisher / render worker / video editor recent tasks / celery watchdog
- 首次发现：视频编辑器 recent tasks 多个任务提示 `publisher 同类任务达到当前并发上限 4`
- 修复提交：待提交

## 现象

视频编辑器 recent tasks 中多个任务保持 `pending`，并显示红色错误：`系统保护：publisher 同类任务达到当前并发上限 4，稍后自动重试。`

现场排查时 PostgreSQL 压力正常，Celery `render` worker 的 active/reserved 均为 0，但数据库中仍有 4 个 `publisher / processing` 任务。这些 DB 状态已经脱离真实 worker 状态，却仍被 runtime guard 计入运行并发，导致后续正常任务全部被挡在容量保护外。

## 影响

- 正常 publisher 任务无法启动。
- 页面把“容量等待”渲染为红色失败，造成误判。
- watchdog 只修复 `pending` 且 Celery 消息消失的任务，无法处理 `processing` 孤儿任务，系统会持续自我阻塞。

## 根因

之前的自动修复只覆盖两类场景：

- 父编排已终止，但 publisher 子任务仍 active。
- 业务任务 `pending`，但 Celery 队列/worker 中已找不到对应消息。

遗漏了第三类场景：任务已经从 DB 状态推进到 `processing`，随后 worker 或外部进程异常退出、消息消失，最终 DB 没有被回写到终态。runtime guard 使用 DB 中的 `processing` 数量计算运行并发，因此这些孤儿任务会永久占用 publisher 槽位。

同时，runtime guard 把容量保护提示写入 `error_message`，前端 recent tasks 又把所有 `error_message` 都显示成红色“失败”，导致非失败状态被误报为失败。

## 解决思路

- 引入 publisher `processing` 孤儿任务对账：DB、Celery worker、Redis 队列三方不一致时进行收口。
- 合成/封面等可安全重跑阶段自动重投；上传阶段不自动重投，避免重复发布。
- 容量保护提示只写入 `result.runtime_guard`，不再污染 `error_message`。
- 前端按 `deferred_capacity/deferred_backlog/deferred_db_pressure` 渲染为“等待容量”，不再显示红色失败。

## 实际方案

- `services.publisher_queue_repair` 新增 `repair_stale_publisher_processing_tasks_once` 和 `maybe_repair_stale_publisher_processing_task`。
- `celery_monitor` watchdog 接入 processing 孤儿修复。
- `publisher/recover-pending` 接口同时触发 processing 孤儿修复。
- `runtime_guard._mark_publication_task_deferred` 不再写 `error_message`，保留 `result.runtime_guard`。
- `video-editor` recent tasks 隐藏 runtime guard 类 error message，并显示黄色等待容量提示。
- `tasks.publisher.run_publication_pipeline` 在启动时跳过 `completed/failed/canceled` 终态任务，避免旧 Celery 消息红elivery 后重复执行。
- `tasks.publisher.run_publication_pipeline` 校验 Celery message id 必须等于 DB 当前 `celery_task_id`；补投新消息后，旧 Redis unacked 消息即使红elivery 也会被跳过。

## 验证

- JS 语法检查：`node --check src/frontend/js/video-editor/main.js` 通过。
- 单元测试改用 `unittest` 执行；当前环境缺少 `pytest`。
- 覆盖用例：processing 消息消失重投、processing 不在 active 但队列可见时降回 pending、上传阶段孤儿任务不自动重投、deferred publisher 消息消失后重新入队、deferred publisher 消息卡在 unacked 但 worker 未执行时补投新 Celery id。
- 现场数据库连接压力正常，问题来自 publisher processing 孤儿任务占槽，而非数据库连接耗尽。

## 复发预防

- 任何队列类保护都必须区分“等待/保护/重试”和“失败/终态”。
- 运行并发统计不能只相信 DB active 状态，必须配合 Celery/Redis 对账修复孤儿状态。
- 可重试的耗时 worker 阶段要明确幂等边界；上传类阶段默认不自动重投。
- 同一 business task id 必须具备终态幂等保护，旧 Celery 消息不能在终态后再次执行。

## 后续事项

- 继续观察 publisher processing orphan repair 的自动修复日志。
- 如后续确实需要自动重投上传阶段，必须先引入发布幂等键和远端发布结果回查。
