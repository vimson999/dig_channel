# 2026-07-07 平台上传误用 render 合成队列

## 基本信息

- 状态：已修复
- 严重级别：中高
- 影响模块：B站/YouTube 后投上传、publisher、Celery 队列、watchdog queue repair
- 首次发现：MP3 详情页 B站上传任务被长视频合成堵在 `render` 队列

## 现象

B站后投上传任务和视频合成任务共用 `render` 队列。由于 `render` worker 本地默认 `concurrency=1`，只要前面有长视频合成，B站上传即使只是网络 I/O 也必须等待合成任务结束。

追加发现：拆出 `platform_upload` 后，如果本地残留多个同名 `platform_upload@...` worker，Celery inspect 会出现 `DuplicateNodenameWarning`，详情页上传记录可能显示 `运行中`，但 Celery 监控卡片显示业务进行中为 0 或队列读取失败，造成两处状态不一致。

## 影响

- 用户手动触发的 B站上传无法及时开始。
- 上传任务被 render 队列中的长合成任务阻塞，误导用户以为上传卡死。
- watchdog / queue repair 也可能把上传型 publisher 子任务重新投回 `render`，导致问题复发。

## 根因

- `run_bilibili_upload_only` 和 `run_youtube_upload_only` 的 `apply_async` 写死 `queue="render"`。
- `celery_config.task_routes` 也把 upload-only 任务路由到 `render`。
- B站上传任务内部的 runtime guard 标记队列也是 `render`。
- publisher pending queue repair 只知道用 `run_publication_pipeline + render` 修复 publisher 子任务，没有识别“父完成后的独立上传任务”。

## 实际方案

- 新增 `platform_upload` Celery 队列。
- 新增 `scripts/dev/run-celery-platform-upload.sh` 和 `scripts/dev/stop-celery-platform-upload.sh`。
- B站/YouTube 后投上传统一投递到 `platform_upload`。
- `run_bilibili_upload_only` / `run_youtube_upload_only` 使用 `publisher_upload` admission feature 和 `platform_upload` queue。
- watchdog 服务表新增 `platform_upload`，本地 bootstrap 可自动拉起。
- publisher pending queue repair 识别 B站/YouTube upload-only 子任务，并补回 `platform_upload`，普通视频合成仍补回 `render`。
- Celery 监控业务任务分桶识别 upload-only publisher 子任务：上传任务归入 `platform_upload`，普通合成任务归入 `render`。
- publisher processing orphan repair 识别 `upload_bilibili` / `upload_youtube` 步骤；上传步骤不会自动重投以避免重复投稿，只会收口为失败并提示重新投递。
- 清理本地重复 `platform_upload@...` worker，只保留一个上传 worker，避免 Celery inspect 重复节点导致状态读数不可靠。
- 将现场被旧 render 队列收口失败的 B站上传任务恢复并迁移到 `platform_upload`。
- 修复 B站上传失败落库时再次读取过期 ORM 对象导致的 `PendingRollbackError`，确保 B站业务错误能正常显示为 `failed`。

## 验证

```bash
PYTHONPATH=src/backend venv/bin/python -m unittest tests.unit.test_publication_task_queue_repair -q
PYTHONPATH=src/backend venv/bin/python -m py_compile src/backend/celery_config.py src/backend/tasks/publisher.py src/backend/api/mp3_video_workflow.py src/backend/apis/v1/publisher.py src/backend/apis/v1/celery_monitor.py src/backend/services/publisher_queue_repair.py src/backend/services/runtime_guard.py
bash -n scripts/dev/run-celery-platform-upload.sh scripts/dev/stop-celery-platform-upload.sh scripts/dev/stop-all-background-jobs.sh
```

现场验证：

- `platform_upload@...` worker 在线。
- `platform_upload` 只有一个 worker PID；监控接口返回 `queue_error: null`、`business_active_count: 0`。
- B站上传任务 `21e2dd8d-1c5d-4007-b8e2-b7d243ecb1c3` 新 celery id 为 `66236be2-668e-420b-a5bf-5114a91235a1`。
- Celery active 显示该任务 routing key 为 `platform_upload`。
- `render` worker active 为空，没有执行上传任务。
- 该任务随后真实触发 B站接口错误 `21523：选择的充电档位状态不可用`，已修正为 `failed`，不再停留在脏 `processing` 状态。

## 复发预防

- 合成/渲染任务与平台上传任务必须使用不同队列。
- 新增 upload-only 任务时，必须同步检查：API 投递、Celery route、任务内部 guard、watchdog queue repair。
- 不允许用 `run_publication_pipeline` 修复 upload-only 子任务。
- 监控卡片与业务记录冲突时，要先查是否有重复 Celery hostname；同队列多 worker 必须使用唯一 hostname。
