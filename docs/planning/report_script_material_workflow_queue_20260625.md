# 研报基础文案物料独立队列

日期：2026-06-25

## 背景

单篇研报经 Gemini Gem 解析后形成的 `structured_result.final_script_for_tts` 是可复用基础物料：

- 完整“研报转语音/视频”需要它。
- “今日研报 Top10”需要十篇单篇文案作为总编辑输入。
- 星球同步达到下载阈值后，可以提前生成该物料，减少后续等待。

原先用 `report_pdf_audio_workflow + script_material_only` 复用完整编排队列，会和豆包朗读、视频合成任务共用容量规则。新方案将基础文案物料拆成独立 feature 和独立队列。

## 队列定义

- feature：`report_script_material_workflow`
- queue：`report_script_material_workflow`
- API：`/api/report-script-material-workflows`
- 测试页面：`src/frontend/report-script-material-workflow-test.html`
- worker 启动脚本：`scripts/dev/run-celery-report-script-material-workflow.sh`
- worker 停止脚本：`scripts/dev/stop-celery-report-script-material-workflow.sh`
- 默认运行上限：2
- 默认积压上限：50
- 输出：`publication_tasks.result.structured_result.final_script_for_tts`

该队列只执行：

```text
PDF -> Gemini 单篇 Gem -> normalize_for_tts -> 基础文案物料落库
```

它不进入豆包朗读、不合并音频、不生成视频。

## 复用策略

创建新基础物料前，会先查找同 PDF attachment 已完成的结果：

- `report_script_material_workflow`
- `report_pdf_audio_workflow`
- `report_audio_workflow`

匹配键：

- `PublicationTask.source_task_id == attachment_id`
- `PublicationTask.report_audio_pdf_attachment_id == attachment_id`

只有找不到可用 `structured_result.final_script_for_tts` 时，才新建 `report_pdf_gemini` 子任务。

## 自动投递

知识星球同步配置 `auto_script_material_dispatch` 开启后：

- PDF 下载次数达到 `pdf_min_download_count`，默认 40。
- 本地 PDF 文件存在且已下载完成。
- 未发现同 PDF 已有物料或完整研报任务。
- 投递到 `report_script_material_workflow`。

运行保护返回 `feature_backlog` 或 `feature_capacity` 时，summary 中记为 `deferred`，表示延后排队，不再混入失败统计。

## 手工测试与监控

- 打开 `http://localhost:8888/report-script-material-workflow-test.html`。
- 页面可选择一批已同步 PDF，并批量投递到 `report_script_material_workflow`。
- 页面顶部读取 `report_script_material_workflow` 的 Celery Monitor 状态，可直接跳转 `celery-monitor.html`。
- Celery Monitor 的工作流分组已包含 `report_script_material_workflow`，可启动、停止、重启、查看日志和积压。

## Top10 接入

`report_top10_workflow` 的单篇文案复用范围已包含 `report_script_material_workflow`。因此 Top10 的优先路径是：

```text
单篇缓存 -> report_script_material_workflow / report_pdf_audio_workflow 历史结果 -> 单篇 Gemini 新解析
```

这能避免同一个 PDF 在物料预生成、研报转视频、Top10 编排之间重复跑 Gemini。

## 后续优化

- 为新物料任务补独立详情/API 页面，避免借用研报转语音页面查看。
- 将 Gemini 子任务 retry/helper 抽成共享模块，减少 `report_audio_workflow`、`report_script_material_workflow`、`report_top10_workflow` 策略漂移。
- 多 Chrome profile 稳定后，再评估 `report_pdf_gemini` worker 并发是否从 1 提升。
