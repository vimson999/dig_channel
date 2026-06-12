# 2026-06-01 工作归档

## 知识星球同步 502 排查

- 现象：`POST /api/v1/zsxq/sync` 请求耗时约 76 秒后返回 502，前端同步日志显示 `知识星球请求失败: 'NoneType' object has no attribute 'client'`。
- 排查纪律：先查看项目日志与同步运行记录，没有全局乱搜日志。
- 结论：这次不是知识星球上游接口失败。后端已经成功创建 `zsxq_sync_runs` 记录，失败发生在把任务投递到独立 Celery 队列 `zsxq_sync` 的 broker 发布阶段，任务没有进入队列。
- 现场处理：将最新异常 run `bd3a6357-161d-46e3-bd76-de406b0b342f` 标记为 `failed`，并写入 `queue_publish_failed=true`、`queued=false`、`running=false`。

## 修复

- `/api/v1/zsxq/sync` 入队失败时：
  - 立即捕获 Celery publish 异常。
  - 将对应同步 run 标记为失败。
  - 返回明确的 `知识星球同步任务入队失败`，不再伪装成知识星球接口错误。
- Celery 配置：
  - `zsxq_sync` 队列和任务路由已注册。
  - broker 连接和 socket 超时降为 5 秒。
  - 关闭任务发布重试，避免 HTTP 请求卡几十秒。
- 同步服务：
  - 新增 `mark_run_failed()`，用于入队失败时回写 run 状态。
  - 自动清理 stale run 时同时处理 `queued` 和 `running`。
  - JSON result 字段使用复制后的 dict 重新赋值，避免 SQLAlchemy 漏掉 JSON 字段变更。

## 验证

- `venv/bin/python -m py_compile src/backend/celery_config.py src/backend/apis/v1/zsxq.py src/backend/services/zsxq_sync_service.py src/backend/tasks/zsxq_sync.py`
- `git diff --check`
- 云端 PostgreSQL 查询确认最新异常 run 已更新为 `failed`，且 `result.queue_publish_failed=true`。

## 重启要求

需要重启后端和知识星球专用 worker，让新的 API 兜底逻辑与 Celery broker 超时配置生效：

```bash
./scripts/dev/run-backend.sh
./scripts/dev/run-celery-zsxq.sh
```

## 豆包 TTS 假排队任务排查

- 现象：任务 `高盛华虹1` 在前端长期显示“排队中”。
- 结论：该任务不是正在 Celery 队列里等待，而是数据库记录创建成功后，Celery 入队失败，导致 `publication_tasks.celery_task_id` 为空，状态停留在 `pending`。
- 现场处理：
  - 确认 `cloud_tts` worker 在线并监听 `cloud_tts` 队列。
  - 将任务 `3975eb3b-d14a-447f-91e3-5ab46ebbfe39` 重新投递到 `cloud_tts` 队列。
  - 新 Celery ID：`2d90086f-ccbf-41ea-ad70-3eeb48e891d8`。
  - 复查确认任务已完成，音频生成到 `data/processed/doubao_tts/3975eb3b-d14a-447f-91e3-5ab46ebbfe39/01_高盛华虹1.mp3`。
- 修复：
  - `POST /api/v2/tts/submit` 在 Celery 入队失败时会把任务标记为 `failed`，返回明确的 `TTS任务入队失败`，不再留下假排队任务。
  - TTS worker 完成或失败时写入 `completed_at`，便于后续任务历史和状态判断。
- 验证：
  - `venv/bin/python -m py_compile src/backend/api/tts_v2.py src/backend/tasks/tts.py`
  - `git diff --check`

## 知识星球 Worker 停止脚本

- 新增 `scripts/dev/stop-celery-zsxq.sh`。
- 用途：只停止 `zsxq_sync` 专用 Celery worker，不影响 `render` 和 `cloud_tts` 队列。
- 行为：先按进程命令中的 `celery` + `zsxq_sync` 精确筛选并温和 `kill`，若仍有残留再 `kill -9`。
- 验证：`bash -n scripts/dev/stop-celery-zsxq.sh`。

## Video Editor 任务列表过滤

- 现象：`video-editor.html` 右侧 Recent Tasks 会展示豆包 TTS、播客视觉化等非视频合成任务。
- 原因：页面调用 `/api/v1/publisher/tasks`，该接口此前直接返回 `publication_tasks` 最近 20 条；TTS 和 podcast visualizer 也复用这张表。
- 修复：
  - `/api/v1/publisher/tasks` 默认只返回 publisher/video 类任务，排除 `params.feature` 为专用功能的任务；保留 `include_all=1` 作为调试开关。
  - `video-editor` 前端增加防御过滤，只展示包含 `video/audio_segments/bilibili/thumbnail` 的视频发布任务。
- 验证：
  - `venv/bin/python -m py_compile src/backend/apis/v1/publisher.py`
  - `node --check src/frontend/js/video-editor/main.js`
  - `git diff --check`

## Video Editor 假死任务自助重新入队

- 现象：视频任务卡片显示 `pending | 0%`，但实际没有进入或没有继续被 Celery 消费，需要手工排查。
- 当前处理：
  - 任务 `d3847c81-e750-45c7-b93f-f2a54a4672c5` 已重新入队并完成。
  - 任务 `79644b59-725a-4347-9a59-af37ec0b5c55` 已重新入队并完成。
- 后端修复：
  - 新增 `/api/v1/publisher/requeue/<task_id>`，可对同一个视频任务原地重新投递到 `render` 队列。
  - `/api/v1/publisher/submit` 与 `/api/v1/publisher/retry` 入队失败时会把任务标记为 `failed`，不再留下 `pending + celery_task_id=null` 的假排队任务。
- 前端修复：
  - `video-editor.html` 右侧未完成任务卡新增 `重新入队` 按钮。
  - 点击后调用 requeue API，成功后刷新任务列表。
- 验证：
  - `venv/bin/python -m py_compile src/backend/apis/v1/publisher.py`
  - `node --check src/frontend/js/video-editor/main.js`
  - `node --check src/frontend/js/publisher/services.js`
  - `git diff --check`

## Celery Redis Result Backend 超时处理

- 现象：worker 日志频繁出现 `TimeoutError('Timeout reading from socket')`，栈在 `task.backend.mark_as_done -> RedisBackend.store_result`。
- 结论：任务主体已经执行到结束阶段，异常发生在 Celery 试图把任务完成元数据写回远程 Redis result backend 时；业务状态、进度和产物路径已经由项目写入 PostgreSQL，Redis result backend 对当前业务不是关键路径。
- 风险：Redis result backend 抖动会让已完成任务在 Celery 日志里显示为异常，制造“假失败/假死”的判断噪音。
- 修复：
  - `task_ignore_result=True`，不再向 Redis 写 Celery task result。
  - `task_store_errors_even_if_ignored=False`，失败元数据也不依赖 Redis result backend。
  - `result_backend_transport_options` 设置 5 秒 socket/connect timeout，避免少量显式 result backend 调用长时间卡住。
- 验证：
  - `venv/bin/python -m py_compile src/backend/celery_config.py`
  - 配置检查确认 `task_ignore_result=True`。
  - `git diff --check`
- 生效要求：需要重启所有 Celery worker。

## 同时生成主视频与播客视觉化稳定性修复

- 现象：`video-editor.html` 勾选“同时生成播客视觉化视频”后，主视频任务与播客视觉化任务经常出现 `pending 0%` 或卡在 `encode`。
- 原因：
  - 主视频任务和播客视觉化任务此前都投递到同一个 `render` 队列。
  - 播客视觉化 encode 属于长任务，容易占住或拖死 render worker，导致主视频也跟着卡。
  - Celery 默认任务被 worker 取走后立即 ack；worker 后续崩溃时，Redis 队列里已无消息，DB 仍停在 pending，形成假死。
- 修复：
  - 新增独立队列 `podcast_visualizer`。
  - `tasks.podcast_visualizer.run_podcast_visualizer_task` 路由到 `podcast_visualizer`，不再占用 `render` 队列。
  - 新增 `scripts/dev/run-celery-podcast.sh` 和 `scripts/dev/stop-celery-podcast.sh`。
  - Celery 长任务改为 `task_acks_late=True`、`task_reject_on_worker_lost=True`、`worker_prefetch_multiplier=1`，降低任务被 worker 取走后丢失的概率。
- 当前处理：
  - 主视频任务 `96cf3c68-d953-473d-842e-f6f16208c0b0` 已重新投递到 `render`。
  - 播客视觉化任务 `a36c7261-0200-4807-b3ee-b60dd2333015` 已重新投递到 `podcast_visualizer`。
- 验证：
  - `venv/bin/python -m py_compile src/backend/celery_config.py src/backend/apis/v1/documents.py`
  - `bash -n scripts/dev/run-celery-podcast.sh`
  - `bash -n scripts/dev/stop-celery-podcast.sh`
  - 配置检查确认 `podcast_visualizer` 队列和路由已注册。
  - `git diff --check`
- 生效要求：需要重启后端和所有 Celery worker，并额外启动 podcast worker。

## Podcast Visualizer 队列排查与自助恢复

- 现象：`podcast-visualizer.html` 任务列表中两个播客视觉化任务停在 `pending 0%`。
- 排查结果：
  - `a36c7261-0200-4807-b3ee-b60dd2333015` 已有 Celery ID，消息在 `podcast_visualizer` 队列里等待消费。
  - `ac0d6712-6ef0-415e-a383-f04f2ca5e927` 原本没有 Celery ID，属于接口入队失败后遗留的假 pending。
  - `celery inspect active_queues` 返回 `No nodes replied`，说明当前没有可响应的 Celery worker，至少 `podcast_visualizer` worker 没有在消费。
  - Redis 队列确认：`podcast_visualizer=2`，其他 `render/cloud_tts/zsxq_sync=0`。
- 修复：
  - `POST /api/v1/documents/podcast-visualizer/submit` 和 `submit-from-path` 入队失败时会把任务标记为 `failed`，返回明确错误，不再留下假 pending。
  - 新增 `POST /api/v1/documents/podcast-visualizer/requeue/<task_id>`，可对同一个播客视觉化任务原地重新入队。
  - `podcast-visualizer.html` 任务列表中未完成任务新增 `重新入队` 按钮；没有 Celery ID 的任务会显示 `未入队` 提示。
  - 已将旧任务 `ac0d6712-6ef0-415e-a383-f04f2ca5e927` 手动补投递到 `podcast_visualizer` 队列。
- 验证：
  - `venv/bin/python -m py_compile src/backend/apis/v1/documents.py`
  - `node --check src/frontend/js/podcast-visualizer.js`
- 生效要求：重启后端，然后启动 `./scripts/dev/run-celery-podcast.sh` 消费 `podcast_visualizer` 队列。
