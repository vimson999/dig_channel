# 2026-06-23 研报音频编排 local_fallback 放行 Gemini 页面残留

## 现象

研报 PDF 音频编排任务进入豆包朗读阶段后，页面显示结构化结果来源为 `local_fallback`，但正文开头混入了 Gemini 页面和提示词残留，例如 `自定义 Gem`、`研报pdf解析视频文案说`、`【研报气质】`、`PDF + 1` 等内容。该文本被拆段送入豆包后，即使音频生成成功，也无法用于后续视频合成。

事故任务示例：`759738f9-e4e5-4627-9e62-473d86f5fc08`。

## 根因

1. Gemini 浏览器子任务抓取到的最终回复本身已经包含页面 UI / Gem 提示词残留。
2. DeepSeek 结构化器返回不可用结果后，服务层进入 `local_fallback`。
3. 旧版 `local_fallback` 只做了极弱的正文抽取和 `PDF + N` 清理，没有质量闸门。
4. 父编排恢复时会复用已落库的 `structured_result`，没有再次检查历史结构化结果是否含页面残留。

## 修复

1. 在 `services.report_audio_workflow` 增加 TTS 正文质量检查：
   - 检测 `自定义 Gem`、`研报pdf解析视频文案说`、Gem 提示词标签、Gemini 页脚等页面残留。
   - 检测 `PDF + N` 上传控件残留。
   - 清洗后仍有污染时抛出错误，禁止进入豆包朗读。
2. 在本地兜底抽取中先移除明确的 Gemini 页面残留，再验证正文质量。
3. 在 `tasks.report_audio_workflow._valid_structured_result` 中复查已落库结构化结果，避免恢复任务时复用历史脏结果。
4. 对事故任务执行止血：父任务标记为 `canceled`，避免继续合成不可用视频。

## 项目纪律

涉及外部网页自动化结果进入生产流水线时，必须遵守：

1. 外部模型/网页抓取文本进入下一阶段前，必须有质量闸门，不能只判断“非空”和“长度足够”。
2. fallback 只能用于格式兜底，不能绕过内容质量要求。
3. 父编排恢复历史结果时，也必须重新运行质量检查，不能默认信任旧结果。
4. 发现已生成坏中间产物时，先停止父任务，再修复代码，最后决定是否重跑。

## 验证

已补充单测：

1. `test_local_fallback_strips_gemini_capture_artifacts`
2. `test_structured_result_quality_detects_cached_capture_artifacts`
3. `test_normalizer_rejects_unclean_capture_artifacts`
4. `test_cached_structured_result_with_capture_artifacts_is_not_reused`
5. `test_clean_cached_structured_result_can_be_reused`
