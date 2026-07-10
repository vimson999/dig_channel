# 2026-07-09 Report Audio TTS 文案混入分段结构标签

## 基本信息

- 状态：已修复，已重启后端与相关 worker 生效
- 严重级别：高
- 影响模块：report_audio_workflow / report_script_material_workflow / DeepSeek normalizer / 详情页
- 首次发现：2026-07-09 用户试听 `b206bf1f-c88f-4100-bc09-339c36e3d8be` 音频时发现朗读了“第一部分：xxx”
- 修复提交：待提交

## 现象

研报转语音详情页对应任务已成功生成音频，但试听时能听到：

```text
第一部分：开场与变量切换（引入悬念与核心结论）
第二部分：产业背景与实地调研印证（铺垫与底层逻辑）
第三部分：产能版图与产品结构重构（深度数据支撑）
```

这些内容是写作结构提纲，不属于应该交给豆包朗读的最终文案。

## 影响

- 已生成音频会读出结构标签，成品不可直接使用。
- 详情页原本只在“结构化结果”底部展示长文案，排查时不够直观。
- 如果不在公共清洗链路处理，同类问题会影响研报转语音和基础文案物料两个入口。

## 根因

代码层根因：

1. DeepSeek normalizer 已要求 `final_script_for_tts` 只包含可朗读正文，但提示词没有明确禁止“第一部分/第二部分”这类栏目结构标签。
2. `clean_final_script_for_tts()` 只清理了 Gemini 页面残留、PDF 上传控件残留、编辑复核说明和尾部孤立序号，没有处理“第 N 部分：标题（说明）”。
3. 详情 API 没有单独暴露“音频文案”字段和质量告警，前端只能在结构化结果中顺手查看，容易误以为问题出在豆包识别阶段。

架构层根因：

- TTS 前的文本质量门禁还不完整。凡是会被音频朗读的字段，都必须按照“最终可直接朗读”而不是“结构完整”来验收。
- 详情页需要展示实际送给 TTS 的文本，而不是只展示模型结构化结果，排障时要能区分“源文本错”和“TTS 读错”。

## 解决思路

- 不把问题归因给豆包，先锁定实际送入 TTS 的 `final_script_for_tts`。
- 在公共服务 `services.report_audio_workflow` 中处理，而不是只在某一个页面或某一个 worker 里补丁。
- 后端 payload 单独暴露 `audio_script`，同时返回质量告警。
- 前端详情页新增独立“音频文案”面板，展示已用于豆包朗读的正文、字数、分段数、provider 和质量提示。

## 实际方案

- 在 `src/backend/services/report_audio_workflow.py` 中新增 `_strip_tts_structure_labels()`，移除：

```text
第 N 部分：xxx（xxx）
第 N 部分：xxx
```

- 在 `_tts_script_quality_issue()` 中把残留的分段结构标签判为不可朗读质量问题。
- 在 DeepSeek normalizer prompt 中明确禁止 `final_script_for_tts` 包含“第一部分、第二部分、开场与变量切换、产业背景”等结构标签。
- 在 `src/backend/api/report_audio_workflow.py` 的 `_job_payload()` 中新增 `audio_script`：

```json
{
  "text": "...",
  "chars": 1840,
  "plain_chars": 1840,
  "chunks": 1,
  "provider": "deepseek",
  "quality_issue": "包含分段结构标签"
}
```

- 在 `src/frontend/js/report-audio-workflow-detail.js` 中新增独立“音频文案”面板，支持复制文案和质量告警。

## 验证

静态与单测：

```text
PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_report_audio_workflow_service tests.unit.test_report_audio_workflow_api
Ran 20 tests in 0.558s OK

venv/bin/python -m py_compile src/backend/services/report_audio_workflow.py src/backend/api/report_audio_workflow.py
```

真实任务验证：

```text
job_id=b206bf1f-c88f-4100-bc09-339c36e3d8be
status=succeeded
audio_script_chars=1840
audio_script_provider=deepseek
audio_script_quality_issue=包含分段结构标签
```

运行态：

- Flask 后端已重启并监听 `http://localhost:5001`。
- `report_audio_workflow` worker 已重启并 ready。
- `report_script_material_workflow` worker 已重启并 ready。

## 复发预防

- 所有送 TTS 的文本必须经过 `clean_final_script_for_tts()` 和 `structured_result_quality_issue()`。
- 新增结构化器提示词时，必须明确区分“写作提纲/字段说明”和“可朗读正文”。
- 详情页必须展示实际送给 TTS 的文本，不能只展示笼统的结构化 JSON。
- 历史音频如果已经读入结构标签，不能通过前端显示清洗来假装修复；需要重跑结构化/朗读后才是干净成品。

## 后续事项

- 提供“从结构化阶段重跑并重新朗读”的操作入口，用于修复已经生成坏音频的历史任务。
- 将 `audio_script.quality_issue` 展示到列表页或任务卡片，提前暴露不可用音频。
