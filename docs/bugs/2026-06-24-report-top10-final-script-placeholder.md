# 2026-06-24 Top10 总编辑返回占位符导致最终口播稿不完整

## 基本信息

- 状态：已修复，待重启 `report_top10_workflow` worker 后对新任务生效；已对当前任务结果做数据修复
- 严重级别：高
- 影响模块：`report_top10_workflow`、Top10 结果详情页
- 首次发现：Top10 父任务成功后，结果文件中 `video_segments` 有 34 段，但 `final_script_for_tts` 只有 52 字
- 修复提交：待提交

## 现象

任务状态显示成功，Gemini 总编辑子任务也成功返回 JSON，但结果里的最终口播稿为：

```text
ASSEMBLE_BY_BACKEND_USING_final_script_assembly_plan
```

页面“最终口播稿”区域因此只显示一段占位符，而不是完整栏目稿。

## 影响

- 编排状态是成功，但业务结果不可直接用于后续 TTS/视频合成。
- 用户容易误以为 Top10 已完整产出，实际只得到结构化装配计划。
- `video_segments`、`opening`、`items`、`closing` 已有完整物料，但没有被后端汇编。

## 根因

总编辑 Gem 的策略是“复用单篇深度稿，后端按 `video_segments` 串联装配”，因此它把 `final_script_for_tts` 写成后端装配占位符。

原后端实现只做了严格 JSON 提取和字段保存，没有识别该占位符，也没有按 `voice_text_ref` / `voice_text_source` 汇编最终口播稿。

## 解决思路

把最终口播稿生成从“完全依赖 Gem 返回单字段”升级为：

1. 如果 Gem 返回了足够长的 `final_script_for_tts`，直接使用。
2. 如果返回占位符，则按 `video_segments` 顺序汇编：
   - `voice_text_ref` 从总编辑 JSON 中取导语、转场、收尾等文本。
   - `voice_text_source` 从 `source_reports` 中取对应 `Rxx.single_report_script`。
3. 汇编后的文本写回 `editor_result.final_script_for_tts` 和父任务 `result.final_script_for_tts`。
4. 原始占位符保留到 `final_script_for_tts_raw`，便于追溯。

## 实际方案

新增 `services.report_top10_workflow.assemble_final_script_for_tts()`：

- 识别 `ASSEMBLE_BY_BACKEND_USING_final_script_assembly_plan` 占位符。
- 支持解析 `opening.voice_text`、`items[0].editorial_intro` 等引用。
- 支持从 `source_reports` 按 `R01/R02/...` 映射单篇正文。
- 用空行连接各段，生成完整口播稿。

在 `tasks.report_top10_workflow` 中：

- `extract_editor_json()` 后立即调用汇编器。
- 写结果文件前把汇编后的 `final_script_for_tts` 回填到 `editor_result`。
- 父任务最终结果也保存汇编后的口播稿。

## 验证

静态验证：

```bash
venv/bin/python -m py_compile src/backend/services/report_top10_workflow.py src/backend/tasks/report_top10_workflow.py
venv/bin/python -m unittest tests.unit.test_report_top10_workflow_service tests.unit.test_report_top10_workflow_task
```

当前任务数据修复验证：

- `source_reports = 10`
- `video_segments = 34`
- 汇编后 `final_script_for_tts` 应显著大于占位符长度，并可在详情页展示/复制。

## 复发预防

- 结构化 Gem 返回占位符或装配计划时，后端必须承担最终物料汇编责任。
- 成功态校验不能只看任务完成；还要校验关键业务产物长度和可用性。
- Top10 下一阶段接入 TTS/视频前，必须以汇编后的 `final_script_for_tts` 为唯一输入。
