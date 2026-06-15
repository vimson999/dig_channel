# Content Pipeline Retention

同步、PDF 清洗、MP3 清洗都会产生大量本地文件。长期运行时，这些文件按“短保留、可预览、可定期清理”的方式处理。

## 默认策略

- 手动清理接口默认保留 3 天。
- 自动清理计划默认保留 3 天、每 24 小时执行一次、默认真实删除。
- 数据库记录、topic/raw JSON、manifest 等小元数据不作为主要清理目标。

`pending` / `processing` / `failed` 任务，以及运行中的同步 run 引用到的 workspace 会被保护，不会进入删除候选。也就是说，失败任务默认保留，方便排查或重试。

## 操作页面

前端入口：`content-retention-cleanup.html`，导航位置为“工具箱 / 历史文件清理”。

页面默认先执行预览，只展示候选数量、预计释放空间、分类统计和具体路径。真正删除需要点击“按当前预览执行清理”并二次确认。

同一页面提供“自动清理计划”：

- 保存当前保留策略。
- 设置执行间隔。
- 选择 dry-run 或真实清理。
- 启动、停止计划任务。
- 立即执行一次。
- 查看计划任务日志。

## API

预览候选文件，不删除：

```http
GET /api/v1/content-pipeline/retention/preview
```

执行清理，默认仍是 dry-run：

```http
POST /api/v1/content-pipeline/retention/cleanup
Content-Type: application/json

{
  "dry_run": false,
  "policy": {
    "sync_raw_days": 3,
    "content_pipeline_batch_days": 3,
    "manual_publish_days": 3,
    "pytest_batch_days": 3
  }
}
```

真正删除时传：

```json
{
  "dry_run": false
}
```

计划任务：

```http
GET /api/v1/content-pipeline/retention/scheduler/status
GET /api/v1/content-pipeline/retention/scheduler/config
POST /api/v1/content-pipeline/retention/scheduler/config
POST /api/v1/content-pipeline/retention/scheduler/start
POST /api/v1/content-pipeline/retention/scheduler/stop
POST /api/v1/content-pipeline/retention/scheduler/run-once
GET /api/v1/content-pipeline/retention/scheduler/logs
```

调度器日志：`logs/content_retention_cleanup_scheduler.log`。

## 清理范围

- `runtime/content_pipeline/batches/*`
- `runtime/content_pipeline/manual_publish/*`
- pytest 项目的 `runtime/batches/*`
- `data/zsxq/*/*/files/*` 下的 PDF、MP3、M4A、WAV、Office、ZIP 等大附件

同步目录只删除大文件附件，保留数据库和 JSON 元数据，方便后续追溯。
