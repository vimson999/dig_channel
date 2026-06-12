# pytest PDF 清洗引擎接入说明

本文档用于把 `/Users/v9/Documents/3---work/06---dev---python/pytest` 里的 PDF 清洗服务，接入 `dig_ytb_channel` 的自动同步、清洗、自动发布链路。

## 当前结论

`pytest` 项目已经有可调用接口：

```text
POST http://127.0.0.1:5005/api/batch-clean
GET  http://127.0.0.1:5005/api/batch-clean/status/<job_id>
```

但它不是通用文件系统 API。`input_dir` 和 `output_dir` 虽然是参数，但后端 `_resolve_local_folder()` 要求目录必须位于 `pytest` 项目目录内。因此不能直接传 `/Users/v9/Downloads/...` 这类外部绝对路径。

第一阶段采用“当前项目搬运文件，pytest 只负责清洗”的方式：

```text
dig_ytb_channel content_pipeline Celery
  -> 复制 PDF 到 pytest/runtime/batches/{batch_id}/input
  -> POST 5005/api/batch-clean
  -> 轮询 5005/api/batch-clean/status/{job_id}
  -> 复制 output 结果回 dig_ytb_channel/runtime/content_pipeline/batches/{batch_id}/pdf_cleaned
  -> 更新 dig_ytb_channel 的 publication_tasks 状态
```

## 正式接入链路

系统自动同步已经接入 PDF 清洗队列：

```text
zsxq_sync Celery
  -> 同步知识星球内容并下载附件
  -> 本轮 run 成功完成
  -> 收集本轮 downloaded 的 PDF 附件
  -> 创建 content_pdf_clean PublicationTask
  -> 投递 tasks.content_pipeline.run_pdf_clean_task 到 content_pipeline 队列
  -> content_pipeline worker 负责搬运、调用 pytest、搬回清洗结果
```

边界：

- `zsxq_sync` 只负责同步和发起清洗任务，不直接搬运 PDF。
- `content_pipeline` 是唯一负责搬运文件和调用 `pytest` 的队列。
- 系统自动同步默认 `auto_pdf_clean=true`；手动 `/api/v1/zsxq/sync` 可显式传 `auto_pdf_clean=true` 启用。
- 清洗输入必须来自 sync run 结果里的 `downloaded_pdf_files`，它只记录本轮实际下载成功的 PDF；历史已下载、复用、跳过的附件不能再次进入清洗批次。
- 如果本轮没有下载成功的 PDF，会在 sync run 的 `result.content_pipeline.pdf_clean` 中记录 `reason=no_downloaded_pdf`，不会创建空清洗任务。
- 如果清洗任务入队失败，同步 run 仍保持同步结果，只在 `result.content_pipeline.pdf_clean` 里记录 `reason=submit_failed`。

## pytest 项目改造项

这些改造让 `pytest` 更适合作为稳定的清洗引擎。当前已完成最小必要项：`/api/health`、`batch_id` 记录、清洗线程整体异常时落 `FAILED` 状态。

### 1. 增加健康检查接口

已新增：

```text
GET /api/health
```

建议返回：

```json
{
  "success": true,
  "service": "pdf-cleaner",
  "cwd": "/Users/v9/Documents/3---work/06---dev---python/pytest",
  "batch_clean": true,
  "version": "2026-06-11"
}
```

作用：

- 当前系统启动清洗任务前确认 `5005` 是否可用。
- 判断 Flask 当前工作目录是否正确。
- 后续排查时能快速区分“服务没起”和“清洗失败”。

### 2. 接口接受可选 batch_id

`POST /api/batch-clean` 已支持可选参数：

```json
{
  "batch_id": "content_20260611_xxx",
  "input_dir": "runtime/batches/content_20260611_xxx/input",
  "output_dir": "runtime/batches/content_20260611_xxx/output",
  "threshold": 245,
  "prefix": "",
  "do_ocr": false,
  "overwrite": false
}
```

如果传了 `batch_id`，日志和 `AuditManager.create_batch_job()` 的 options 会记录它，方便和当前系统任务 ID 对齐。

### 3. 明确支持项目内 batch 目录

确认 `_resolve_local_folder()` 对以下路径可用：

```text
runtime/batches/{batch_id}/input
runtime/batches/{batch_id}/output
```

仍然保留“必须位于 pytest 项目目录内”的安全限制。第一阶段不建议直接开放任意绝对路径。

### 4. 状态接口保持结构稳定

当前系统依赖以下字段：

```json
{
  "success": true,
  "job": {
    "state": "PENDING|RUNNING|FINISHED|FAILED",
    "total": 10,
    "current": 3,
    "overall_percent": 32.5,
    "success": 2,
    "failed": 0,
    "skipped": 1,
    "message": "...",
    "files": [
      {
        "name": "原始相对路径.pdf",
        "status": "SUCCESS|FAILED|SKIPPED|ERROR",
        "message": "...",
        "output": "输出相对路径.pdf",
        "duration": 12.3
      }
    ]
  }
}
```

建议未来增加 `FAILED` 状态。如果清洗线程启动后整体异常，不要让任务永远停在 `RUNNING`。

### 5. 不要让清洗逻辑依赖固定目录

需要确认清洗流程只依赖传入的：

```text
input_dir
output_dir
```

不要隐式读取或写入：

```text
待清理/
清理完毕/
```

这两个目录可以继续给页面使用，但不应该成为 API 的隐藏依赖。

## 当前项目负责的事情

当前项目新增 `content_pipeline` 能力，职责如下：

- 创建 `PublicationTask`，记录清洗任务状态。
- 创建 batch 工作区。
- 复制原始 PDF 到 `pytest` 项目内部 batch input 目录。
- 调用 `5005` 清洗 API。
- 轮询清洗状态。
- 复制清洗结果回当前项目 runtime。
- 写入 manifest 和任务结果。

第一阶段不直接触发自动发布。清洗完成后，自动发布服务可以读取任务 `result.cleaned_files` 或当前项目输出目录继续发布。

## 环境变量

当前项目支持以下环境变量：

```text
PDF_CLEANER_BASE_URL=http://127.0.0.1:5005
PDF_CLEANER_PROJECT_DIR=/Users/v9/Documents/3---work/06---dev---python/pytest
PDF_CLEANER_BATCH_ROOT=runtime/batches
CONTENT_PIPELINE_OUTPUT_ROOT=runtime/content_pipeline/batches
PDF_CLEANER_POLL_INTERVAL=1.5
PDF_CLEANER_TIMEOUT_SECONDS=3600
```

如果不配置，当前项目会默认推断同级 `pytest` 目录。

## 第一阶段 API

当前项目提供：

```text
GET  /api/v1/content-pipeline/pdf-clean/health
POST /api/v1/content-pipeline/pdf-clean/submit
GET  /api/v1/content-pipeline/tasks
GET  /api/v1/content-pipeline/task/<task_id>
```

提交示例：

```json
{
  "title": "测试 PDF 清洗",
  "source_root": "/Users/v9/Downloads/room/pdf/01-待清洗",
  "files": [
    "/Users/v9/Downloads/room/pdf/01-待清洗/A.pdf",
    "/Users/v9/Downloads/room/pdf/01-待清洗/B.pdf"
  ],
  "cleaner": {
    "threshold": 245,
    "do_ocr": false,
    "overwrite": false,
    "prefix": ""
  }
}
```
