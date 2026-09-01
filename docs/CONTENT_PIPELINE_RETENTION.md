# Content Pipeline Retention

同步、PDF 清洗、MP3 清洗都会产生大量本地文件。长期运行时，这些文件按“短保留、可预览、可定期清理”的方式处理。

## 默认策略

- 手动清理接口默认保留 3 天。
- 自动清理计划默认保留 3 天、每 24 小时执行一次、默认真实删除。
- 数据库记录、topic/raw JSON、manifest 等小元数据不作为主要清理目标。
- `runtime/content_pipeline/batches` 按批次内大文件自身 mtime 判断是否过期；即使父目录最近被碰过，过期 PDF/MP3/WAV/Office/ZIP 等大文件也会进入候选。
- `runtime/content_pipeline/web_uploads/<submission-id>` 保存网页投递的原件；仍被未收尾任务引用的目录会受保护，默认在 3 天后按整个提交目录清理。
- `data/processed` 只扫描生成的 `.mp4`；按视频文件自身 mtime 判断是否过期，默认保留 3 天。JSON、WAV/MP3、字幕、ASR/TTS 缓存和其他非 MP4 文件不在这一类清理范围内。
- `pending` / `processing` / `failed` 任务引用的文件默认只保护 3 天；超过 `unfinished_reference_protect_days` 后，视为历史残留，按文件或目录自身保留期进入清理候选。

也就是说，刚失败或刚卡住的任务会短期保留，方便排查或重试；长期未收尾的历史残留不会永久占用磁盘。

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
- 每次执行会发送开始、完成、异常邮件；邮件发送失败只写日志，不阻断清理。

调度器按单实例运行：服务层有启动锁，调度器进程自身也有进程锁。重复启动时，第二个进程会记录 warning 后退出，避免重复清理、重复发邮件或用后续空跑覆盖真实清理结果。

判断“今天是否清理”时，以调度器日志里的 `[RETENTION_CLEANUP] trigger_done` 为准；状态里的 `last_result` 只代表最后一次执行结果。

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
    "pytest_batch_days": 3,
    "processed_video_days": 3,
    "include_processed_videos": true,
    "unfinished_reference_protect_days": 3
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

邮件通知默认开启，可用环境变量关闭：

```bash
CONTENT_RETENTION_EMAIL_NOTIFY=0
```

## 清理范围

- `runtime/content_pipeline/batches/*` 内的 PDF、MP3、M4A、WAV、Office、ZIP 等大文件
- `runtime/content_pipeline/web_uploads/*` 下过期的网页投递原件目录
- `runtime/content_pipeline/manual_publish/*`
- pytest 项目的 `runtime/batches/*`
- `data/zsxq/*/*/files/*` 下的 PDF、MP3、M4A、WAV、Office、ZIP 等大附件
- `data/processed/**/*.mp4` 下超过保留期的生成视频

同步目录和 content pipeline 批次默认只删除大文件附件，`data/processed` 只删除 MP4；数据库、JSON、manifest、音频和缓存等元数据或中间产物保留，方便后续追溯。

生成 MP4 的候选会校验 `PublicationTask` 的视频路径投影，以及仍在保护窗口内任务的结果路径；只要任务仍处于 `pending`、`processing` 或近期 `failed` 且引用该文件，就不会进入候选。

## 未收尾任务保护窗口

清理服务会读取 `PublicationTask` 和运行中的 `ZsxqSyncRun` 引用路径。默认规则：

- 任务最近 3 天仍处于 `pending`、`processing`、`failed`：保护其引用文件。
- 超过 3 天仍未收尾：不再因为该任务状态阻止清理。
- 可以通过 `unfinished_reference_protect_days` 调整窗口。

这个规则用于处理“任务状态漏收口导致文件永远不清”的情况。
