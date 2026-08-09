# 研报音频方案三在视觉资产未就绪时反复创建 Publisher 失败任务

## 基本信息

- 状态：前置门禁代码与合同测试已加载，待下一条方案三自动任务运行态验收
- 严重级别：P0
- 影响模块：研报音频编排 / 方案三竖屏 / Publisher / publication_tasks / render queue
- 首次发现：2026-08-06
- 修复提交：待提交
- 根因分类：父子编排前置条件 / 确定性失败重复建子任务 / 失败写放大
- 复发键：`ORCH-PARENT-CHILD-STATE`
- 是否复发：是
- 关联记录：`2026-07-29-report-audio-scheme3-preflight-blocked-landscape.md`、`2026-07-27-scheme3-redundant-intermediate-video.md`、`2026-07-18-publication-task-progress-json-write-amplification.md`
- 防线层级：shared_guardrail / contract_test / observability

## 现象

2026-08-05 06:02:19 至 2026-08-06 06:02:19，render 日志记录 162 个不同的
`run_publication_pipeline` Celery failure ID，错误均为“方案三需要已完成的正文分镜图片或兼容视觉视频”。
只读数据库对账确认 162 个 ID 全部一一对应 162 条失败 `publication_tasks`，不是日志重复。

## 影响

- 只有 5 个研报音频父任务，却创建了 162 个必然失败的 Publisher 子任务。
- 每个子任务在发现方案三素材缺失前仍执行多轮任务读取、进度/result 更新和封面生成，放大数据库写入、
  render 队列占用和连接等待。
- 失败任务持续替换父任务保存的 child 引用，使恢复和用户界面状态更难判断。

## 根因

旧运行路径在创建 Publisher 子任务时只携带 `scheme3` 配置，没有解析同一 PDF 的火柴人视觉工作流、
正文分镜时间线或兼容视频路径。Publisher 进入 `v_synthesis` 后才发现两者皆空并失败。

研报音频父任务恢复时把 failed child 当作可重新创建条件，每次续跑都会创建新的 Publisher；视觉资产
仍未就绪时，确定性前置失败被错误实现为“创建后失败”，形成失败风暴。

## 解决思路

1. 在创建 Publisher 之前解析同一 PDF 的火柴人视觉工作流。
2. 视觉工作流存在但素材未就绪时，用 `ChildTaskStillRunning` defer 父任务，不创建子任务。
3. 素材就绪后把不可变 scene timeline 或兼容 video path 显式传入 Publisher。
4. 只有真正可执行时才写入子任务和投递 render 队列。

## 实际方案

- `_resolve_scheme3_visual_inputs` 解析同 PDF、`stick_figure` 工作流的 scene timeline、video path、status 和 step。
- `_create_publisher_video_task` 对任何包含 `scheme3` 的组合先执行前置门禁；无工作流直接给出明确错误，
  工作流仍处理中则抛 `ChildTaskStillRunning(label=scheme3_visual)`。
- 就绪后把 `visual_scene_timeline` / `visual_video_path` 和 `visual_workflow_id` 写入 Publisher 参数，
  主方案和第二竖屏方案均覆盖。
- 父任务现有 defer 机制负责延迟续跑，未创建 Publisher 时不会产生失败状态写和 render 消费。

## 验证

- 162 个 Celery failure ID 与数据库 162 条失败任务一一对账，集中在 5 个研报音频父任务。
- 三个历史失败样本均为 `scheme3`，scene count=0、无 video path、无 visual workflow id，符合根因。
- `test_publisher_video_task_includes_scheme3_scene_timeline` 证明就绪时间线传入渲染器。
- `test_publisher_video_task_defers_scheme3_until_visual_assets_are_ready` 证明未就绪时 defer 且不创建任务。
- 研报音频任务模块 54 tests 通过；完整 unit 1,028 tests 已通过。
- 新 worker 自 2026-08-06 08:47 重启后尚未出现该错误指纹；因为没有新的对应方案三任务，当前只能证明
  “未复发”，不能替代下一条真实方案三任务的正向验收。

## 复发预防

- 确定性前置条件必须在创建子任务前检查；等待态用 defer，不得用 failed 子任务表达。
- 任何父任务在短窗口为同一 role/source 创建多个 failed child 都应进入 failure-storm 告警。
- 方案三合同同时覆盖主竖屏、第二竖屏、时间线和兼容视频两种输入。
- 每日巡检同时按 Celery task ID、PublicationTask ID 和 source_task_id 去重/聚合失败，不能只看日志行数。

## 后续事项

- 下一条自动方案三任务确认视觉未就绪阶段 Publisher 创建数为 0，就绪后只创建 1 个并完成。
- 下一完整窗口要求该错误从 162 降至 0；若仍出现，按新 worker PID 和 source task 继续追未复用门禁的入口。
