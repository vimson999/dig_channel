# MP3 手动 DS 返回值不完整仍生成垃圾封面和视频

## 基本信息

- 状态：已修复
- 严重级别：P1
- 影响模块：mp3_video_workflow / 手动 DS 返回值 / IMA 全文 DS / 封面背景图 / publisher 子任务
- 首次发现：2026-07-07，详情页 `e0cfacbe-243d-4f10-93ac-7a9bc06e490b` 出现“未识别到时间戳”和“格式(版本A/B)”仍继续产图产视频
- 修复提交：待提交

## 现象

用户在 MP3 编排详情页看到封皮和背景图里出现“标题版本二（另一种视角）”“格式(版本A/B)”等模板占位内容，同时 DS 摘要区明确显示“未识别到时间戳”。系统仍把任务跑到 completed，并产生了两个视频子任务。

## 影响

- 缺少精华片段开始/结束时间戳时，视频截取范围不可控。
- 占位文案进入封面、背景图和视频标题，产物不可用。
- 父任务显示成功，容易误导后续下载、B站投递和复用缓存。

## 根因

手动 DS 返回值和 IMA 全文 DS 结果只做了宽松解析。解析失败时 `fallback_plan` 会用默认模板继续补齐字段，后续视觉生成和视频合成没有再次验证“第 4 条时间戳、第 6 条斜杠标题、第 7 条竖线标题”是否真实存在。复用已有 plan 时也缺少严格验收，导致坏 plan 可以绕过解析阶段。

## 解决思路

把 DS 返回值作为强契约输入：必须能识别第 1/2 条高密度标题、第 4 条开始/结束时间戳、第 6 条小君君横屏标题、第 7 条此刻此声竖屏标题。任何缺失或包含占位模板的结果都应在生成封面/背景图/视频前失败。

## 实际方案

- 新增 DS 返回值契约校验，手动 DS 和 IMA 全文 DS 在进入 plan 解析前必须通过。
- 新增严格 plan 校验，复用缓存 plan 也必须检查有效时间戳、6/7 标题和非占位封面文案。
- 占位词包括 `xxxx`、`当日日期`、`格式(版本A/B)`、`标题版本`、`未识别到时间戳`、`DS v4`、`IMA 转录`、`Gemini`、`Generated from transcript`、`推荐截取`、`最精华音频片段` 等。
- 已将错误任务 `e0cfacbe-243d-4f10-93ac-7a9bc06e490b` 标记为失败，移除父任务里的 `infographic`、`artifacts`、`keyframes`、`children`、`landscape_video`、`vertical_video` 引用，并将两个垃圾 publisher 子任务标记为 `invalidated_by_parent`。

## 验证

- `PYTHONPATH=src/backend venv/bin/python -m py_compile src/backend/services/mp3_video_workflow.py`
- `PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_mp3_video_workflow_service -q`
- 本地详情 payload 复核：坏任务状态为 failed，封面/背景图、关键帧、视频、children 均为空。

## 复发预防

- DS 文本解析失败时禁止 fallback 自动补模板继续生产。
- 任何“AI 返回值作为生产输入”的流程，在调用视觉/视频生成前必须做一次结构化验收。
- 复用缓存和历史 plan 不能跳过验收。

## 后续事项

- 如后续 DS 提示词格式调整，需要同步更新契约测试，不能只依赖人工看图发现问题。
