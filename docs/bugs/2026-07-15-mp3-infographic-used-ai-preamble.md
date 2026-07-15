# MP3 竖屏信息墙误用 AI 寒暄与任务说明

## 基本信息

- 状态：已修复
- 严重级别：P1
- 影响模块：mp3_video_workflow / DS 规划 / 竖屏背景信息图
- 首次发现：2026-07-15，任务 `4bd74594-fd58-4bef-b96f-25664de7294d`
- 修复提交：待提交
- 根因分类：非结构化 AI 输出被机械截行
- 复发键：AI-OUTPUT-STRUCTURED-PROJECTION
- 是否复发：首次
- 关联记录：`2026-07-07-mp3-invalid-ds-response-generated-garbage-assets.md`
- 防线层级：shared_guardrail / contract_test

## 现象

竖屏背景的信息墙本应展示金句、核心观点和关键判断，却出现“作为您的投研助手”“根据您提供的音频文档内容”“以下是针对 7 个问题的具体回答”等 AI 寒暄和任务说明。

## 影响

- 信息墙失去投资信息密度，成片观感像未清洗的 AI 对话截图。
- 同一问题会影响手动 DS 返回值和 IMA 转录后调用 DS 两条入口。
- 仅增加单个关键词过滤无法覆盖 AI 话术变体，容易反复出现。

## 根因

DS 文本信息图在结构化规划之前生成。`_infographic_bullets` 从 DS 全文自上而下机械选取符合长度的行，导致位于正文前面的 AI 寒暄优先占据标题、摘要和前三条观点。后续虽已调用 DS 规划标题、封面和时间戳，但规划结果没有定义信息墙字段，信息图也没有在规划完成后重绘。

## 解决思路

把信息墙视为 DS 规划的正式结构化产物，而不是从完整回复中顺手截取。AI 负责判断哪些内容适合展示，程序只负责字段验收、兜底和渲染。

## 实际方案

- DS 规划契约新增 `infographic_title`、`infographic_subtitle`、`infographic_points`，明确三条观点优先来自第 5 项金句，其次来自第 4 项核心逻辑。
- 信息图改为在 DS 规划完成并通过严格校验后生成。
- 旧格式兜底解析优先读取编号第 5 节，并过滤寒暄、角色介绍、任务说明、栏目标题、时间戳和流程词。
- 增加包含关系去重，避免同一句短金句和长版本同时占位。
- 升级规划和视觉资产版本，历史任务续跑时不会继续复用错误信息图。

## 验证

- `PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_mp3_video_workflow_service.Mp3VideoWorkflowServiceTest.test_transcript_infographic_filters_process_markers tests.unit.test_mp3_video_workflow_service.Mp3VideoWorkflowServiceTest.test_transcript_infographic_prefers_ds_gold_quotes_over_ai_preamble tests.unit.test_mp3_video_workflow_service.Mp3VideoWorkflowServiceTest.test_draw_transcript_infographic_creates_image`
- 使用任务 `4bd74594-fd58-4bef-b96f-25664de7294d` 的真实 DS 返回验证，信息墙候选来自“连接器格局巨变”“调整与基本面背离”等金句和观点，不再包含 AI 寒暄。

## 复发预防

- 所有进入视觉资产的 AI 文案必须先形成有名称、有类型约束的结构化字段。
- AI 负责语义选择，程序负责确定性验收；不得用“全文前 N 行”代替内容策划。
- 手动输入、自动 DS 和历史缓存必须走同一结构化投影与过滤函数。

## 后续事项

- 观察不同 DS 表述下是否能稳定得到三条互不重复的观点；若 DS 未返回足够内容，应明确失败或展示不足，不再用流程说明补位。
