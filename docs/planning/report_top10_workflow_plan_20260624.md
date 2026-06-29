# 今日研报 Top10 编排 MVP 设计

## 背景

现有单篇研报链路已经能把 PDF 交给单篇 Gemini Gem，并产出质量较高的 `structured_result.final_script_for_tts`。Top10 编排不是重新解析 PDF，而是在 10 篇单篇文案基础上做栏目化重排、主线提炼和转场串联。

## 第一阶段目标

1. 测试页支持选择 10 份已同步 PDF。
2. 对每份 PDF 优先复用已持久化的单篇文案。
3. 缓存未命中时，才创建单篇 Gemini 子任务生成文案。
4. 10 篇单篇文案齐备后，按 `【输入素材】` 格式组装输入。
5. 将输入投递到总编辑 Gem：`https://gemini.google.com/gem/3d1eca63c142`。
6. 持久化总编辑 JSON、`final_script_for_tts` 和 `video_segments`。

## 稳定性纪律

Top10 总编辑阶段不允许把 10 篇单篇全文无限制塞给浏览器版 Gemini。当前策略：

- 总编辑输入使用结构化素材块。
- 每篇保留标题、封面标题、副标题、摘要、风险提示。
- 单篇正文默认只给 800 字精华摘录，完整结果仍保存在父任务 `source_reports`。
- 可通过 `REPORT_TOP10_EDITOR_SCRIPT_CHAR_LIMIT` 调整上限。
- 父任务等待子任务时只轮询轻量状态字段，禁止每秒读取大 `params/result` JSON。
- 如果总编辑 Gem 返回 `ASSEMBLE_BY_BACKEND_USING_final_script_assembly_plan` 这类装配占位符，后端必须按 `video_segments` 的 `voice_text_ref` / `voice_text_source` 汇编真正的 `final_script_for_tts`，不能把占位符当成功产物。

原因：浏览器自动化链路对 3 万字级 prompt 不稳定，容易出现点击发送后 prompt 仍留在输入框、Gemini 未进入生成的状态，同时会放大数据库和页面轮询压力。

## 缓存原则

单篇 PDF 文案是跨工作流复用资产，不属于某一个 Top10 任务私有。

缓存命中优先级：

1. `report_single_script_cache`：专门的轻量单篇文案缓存任务。
2. 已完成的 `report_pdf_audio_workflow`：复用历史单篇研报转语音编排结果。
3. 当前 Top10 父任务内已完成的单篇 Gemini 子任务。

缓存身份优先使用：

1. 知识星球附件 `attachment_id`。
2. 附件 `file_id`。
3. 后续扩展本地上传 PDF 时使用文件 hash。

## 编排结构

- 父任务 feature：`report_top10_workflow`
- 单篇文案缓存 feature：`report_single_script_cache`
- 父任务队列：`report_top10_workflow`
- 单篇/总编辑 Gemini 子任务：复用 `report_pdf_gemini` 队列
- 测试页面：`src/frontend/report-top10-workflow-test.html`
- API：`/api/report-top10-workflows`

## 非目标

第一阶段不生成豆包音频、不生成视频画面、不自动发布。视频画面应在总编辑 JSON 稳定后，再基于 `video_segments` 做第二阶段设计。

## 后续方向

1. 让单篇研报转语音/视频编排在启动时也先查询 `report_single_script_cache`。
2. 为本地上传 PDF 增加 sha256 身份，支持跨来源复用。
3. 根据总编辑输出的 `video_segments` 设计 Top10 视频画面模板。
4. 增加缓存失效策略，例如 Gem 提示词版本变化后可强制重跑。
5. 抽出共享的 Gemini 子任务重试、等待、恢复和输入预算工具，避免新编排再次漏掉成熟策略。
