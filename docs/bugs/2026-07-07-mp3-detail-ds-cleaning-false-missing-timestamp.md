# MP3 详情页 DS 清洗误报未识别时间戳

## 基本信息

- 状态：已修复
- 严重级别：P1
- 影响模块：mp3-video-workflow-detail / mp3_video_workflow API / DS 返回值展示
- 首次发现：2026-07-07，详情页 `faf51caf-88f4-4470-9a52-b3bea89ed541` 显示“Gemini 上传 MP3”和“未识别到时间戳”
- 修复提交：待提交

## 现象

任务实际使用的是手动 DS 返回值，后端规划里已经识别出 `00:02:36.520 - 00:36:41.040`，但详情页 DS 清洗区仍显示“未识别到时间戳”。同时摘要行把 `ds_response` 错误展示成“Gemini 上传 MP3”。第 2 条标题也被清洗成引号里的短语“宁德时代时刻”，丢失了完整标题。

## 影响

- 用户误以为任务仍走 Gemini 上传和错误流程。
- 正确的 DS 时间戳被展示成缺失，影响对流程状态的判断。
- DS 标题清洗结果不可信，容易误导后续 B站标题、封面文案和人工验收。

## 根因

生产规划层和详情展示层各自维护了一套 DS 解析逻辑。规划层支持 `[00:02:36.520]` 这种括号时间戳，API 展示层 `_extract_clip` 不支持。API/前端标题清洗还会优先抽取任意引号文本，导致完整标题中的“宁德时代时刻”被误当作标题 2。前端摘要来源标签只区分 `ima_transcript`，其他都落到 Gemini 文案。

## 解决思路

详情展示层不能比生产规划层更窄。展示层解析至少要覆盖生产层已支持的 DS 返回格式，并且来源标签必须按真实 `input_source` 展示。

## 实际方案

- API `_extract_clip` 支持 `开始时间戳： [00:02:36.520]`、`结束时间戳： [00:36:41.040]`。
- API/前端标题清洗先按行移除“标题版本一/二”等 heading，再取正文标题；只有整段都是引号包裹时才抽取引号内容。
- 详情页新增 `sourceLabel()`，将 `ds_response` 显示为“手动 DS 返回值”。
- bump 详情页 JS 版本号，避免浏览器缓存旧逻辑。

## 验证

- `node --check src/frontend/js/mp3-video-workflow-detail.js`
- `PYTHONPATH=src/backend venv/bin/python -m py_compile src/backend/api/mp3_video_workflow.py`
- `PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_mp3_video_workflow_service -q`
- 本机接口复核 `faf51caf-88f4-4470-9a52-b3bea89ed541`：`input_source=ds_response`，`title_2` 为完整标题，`clip=02:36 - 36:41`。

## 复发预防

- DS 返回格式新增或调整时，必须同时覆盖生产解析和详情展示解析。
- 禁止展示层用“任意引号文本优先”的标题清洗策略。
- 详情页来源标签必须枚举所有生产入口，不能用二分支兜底到 Gemini。

## 后续事项

- 后续可把 API/前端展示清洗进一步收敛到后端统一结构化 payload，减少重复解析。
