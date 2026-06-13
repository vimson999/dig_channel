# Content Pipeline Retention

同步、PDF 清洗、MP3 清洗都会产生大量本地文件。长期运行时，这些文件默认按“短保留、可预览、再删除”的方式处理。

## 默认策略

- 知识星球同步附件大文件：保留 2 天。
- content pipeline 清洗批次目录：保留 2 天。
- 手动发布临时整理目录：保留 2 天。
- pytest PDF 清洗中转批次：保留 2 天。
- 数据库记录、topic/raw JSON、manifest 等小元数据不作为主要清理目标。

运行中的 `pending` / `processing` 任务，以及运行中的同步 run 引用到的 workspace 会被保护，不会进入删除候选。

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
  "dry_run": true,
  "policy": {
    "sync_raw_days": 2,
    "content_pipeline_batch_days": 2,
    "manual_publish_days": 2,
    "pytest_batch_days": 2
  }
}
```

真正删除时传：

```json
{
  "dry_run": false
}
```

## 清理范围

- `runtime/content_pipeline/batches/*`
- `runtime/content_pipeline/manual_publish/*`
- pytest 项目的 `runtime/batches/*`
- `data/zsxq/*/*/files/*` 下的 PDF、MP3、M4A、WAV、Office、ZIP 等大附件

同步目录只删除大文件附件，保留数据库和 JSON 元数据，方便后续追溯。
