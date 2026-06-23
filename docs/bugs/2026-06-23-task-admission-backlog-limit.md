# 任务准入保护把排队容量误收窄为运行并发

## 现象

`report_pdf_audio_workflow` 批量提交时，第三个同类任务显示：

```text
系统保护：report_pdf_audio_workflow 同类任务达到并发/积压上限 2，稍后自动重试。
```

部分任务最终被标记为 `failed`，但错误信息仍写着“稍后自动重试”。

## 原因

准入保护最初只有一套 `TASK_ADMISSION_FEATURE_LIMITS`。启动阶段用它限制运行并发是合理的，但提交阶段也用同一套阈值统计 `pending + processing`，导致“最多同时运行 2 个”被误用成“最多只能排队 2 个”。

此外，`guard_publication_task_start()` 调用 Celery `retry(max_retries=None)` 以为是无限重试；Celery 实际会回落到任务默认 `max_retries=3`，多次延迟后任务会进入失败态。

## 修复

- 新增 `TASK_ADMISSION_FEATURE_BACKLOG_LIMITS`，单独控制同类任务排队/运行总积压。
- `mode=submit` 使用积压预算，`mode=start` 使用运行预算。
- 准入保护重试使用 `TASK_ADMISSION_MAX_RETRIES`，默认 `100000`，避免被 Celery 默认 3 次截断。
- 延迟状态仍保持 `pending`，并区分：
  - `deferred_db_pressure`
  - `deferred_capacity`
  - `deferred_backlog`

## 纪律

运行并发和排队积压必须是两套预算。前者保护实际资源消耗，后者保护系统免受无限堆积；不能用同一个数字同时表达这两件事。
