# 研报基础文案物料自动生成

日期：2026-06-24

## 目标

当知识星球同步下载到 PDF，且该 PDF 下载次数达到配置阈值时，自动投递一个只生成“基础文案物料”的研报工作流任务。

基础文案物料定义为：

```text
PDF -> Gemini Gem 单篇解析 -> 结构化清洗 -> structured_result.final_script_for_tts
```

该任务不继续进入豆包朗读，也不生成音频或视频。

## 当前实现

- 配置键：`auto_script_material_dispatch`
- 默认阈值：`pdf_min_download_count = 40`
- Celery 任务类型：`report_script_material_workflow`
- Celery 队列：`report_script_material_workflow`
- 默认容量：运行上限 2，积压上限 50
- 任务模式：只生成基础文案物料，不进入豆包朗读、音频合并或视频合成
- 持久化位置：
  - `publication_tasks.result.structured_result.final_script_for_tts`
  - `publication_tasks.result.script_material`
  - `publication_tasks.result.output_path`

## 去重边界

- 基础物料任务会跳过同 PDF 已存在的运行中或已完成 `report_script_material_workflow`、`report_pdf_audio_workflow`、`report_audio_workflow`。
- 完整视频任务也会产生同样的基础文案物料，因此如果视频自动投递先创建了完整任务，基础物料投递会跳过。
- 只生成基础物料的任务不会阻止后续完整视频任务创建。
- `feature_backlog` / `feature_capacity` 会计入 `deferred`，表示延后排队，不计入失败。

## 后续复用

后续“研报转视频”和“今日研报 Top10”都应优先读取同 PDF 已完成任务中的 `structured_result.final_script_for_tts`，没有可用结果时再投递新的基础物料任务。Top10 历史复用范围已经包含 `report_script_material_workflow`。
