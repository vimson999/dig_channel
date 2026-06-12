# 2026-05-30 工作归档

## 知识星球同步改造为独立 Celery 队列

- 背景：此前 `zsxq-lab.html` 点击“同步当前 Tab / 同步全部 Tab”会在 Flask HTTP 请求里同步执行拉取、写库、附件下载和 9-12 秒随机等待，容易因后端重启或浏览器连接断开出现 `Failed to fetch`。
- 架构调整：
  - 新增独立 Celery 队列 `zsxq_sync`，不与 `render`、`cloud_tts` 共用队列。
  - 新增 worker 启动脚本 `scripts/dev/run-celery-zsxq.sh`。
  - 新增任务模块 `src/backend/tasks/zsxq_sync.py`，任务名 `tasks.zsxq_sync.run_zsxq_sync`。
- 后端 API：
  - `/api/v1/zsxq/sync` 不再执行长同步。
  - API 只创建 `zsxq_sync_runs` 记录，状态为 `queued`，然后投递 Celery 任务并立即返回 `run_id` 和 `celery_task_id`。
  - Celery worker 在后台执行同步、写库、附件下载和节流。
- 同步服务：
  - `ZsxqSyncService` 新增 `create_sync_run()`。
  - `sync_tabs()` 支持接收已有 `run_id`，由 Celery 将状态从 `queued` 更新到 `running/completed/failed`。
  - 同步日志查询会把长时间无进展的 `queued/running` 记录标记为失败。
- 前端：
  - 点击同步后显示“已加入同步队列”。
  - 页面每 5 秒轮询同步日志，直到任务不再处于 `queued/running`。
  - 同步日志展示运行中的 partial 进度。

## 启动方式

```bash
./scripts/dev/run-celery-zsxq.sh
```

该 worker 固定 `concurrency=1`，用于降低知识星球接口和附件下载的并发风险。

## 验证

- `python3 -m py_compile src/backend/celery_config.py src/backend/tasks/zsxq_sync.py src/backend/apis/v1/zsxq.py src/backend/services/zsxq_sync_service.py`
- `node --check src/frontend/js/zsxq-lab.js`
- `git diff --check`

## Video Editor 同时生成播客视觉化视频

- 需求：在 `video-editor.html` 只设置一次音频和标题，可选地同时提交 `podcast-visualizer.html` 对应的视频任务。
- 前端：
  - 在提交按钮上方新增 `同时生成播客视觉化视频` checkbox。
  - 勾选后展开两个输入项：`Podcast Subtitle` 和 `Time Label`。
  - `Time Label` 默认使用北京时间当天日期，格式为 `YYYY-MM-DD / 精选片段`。
  - 播客视觉化固定生成横屏视频，不跟随 video-editor 的横竖屏设置。
  - 第一版使用第一个音频片段，复用该片段的起止时间、倍速和当前 ASR 模型。
- 后端：
  - 新增 `/api/v1/documents/podcast-visualizer/submit-from-path`。
  - 该接口接受已上传音频路径，不重复上传文件。
  - 创建独立 `podcast_visualizer` PublicationTask，并继续复用现有 `run_podcast_visualizer_task` 渲染流程。
- 容错：
  - 主视频任务和播客视觉化任务独立提交。
  - 如果主任务提交成功但播客任务提交失败，前端会明确提示“主视频任务已提交，但播客视觉化任务提交失败”。

## 验证补充

- `python3 -m py_compile src/backend/apis/v1/documents.py`
- `node --check src/frontend/js/video-editor/main.js`

## Video Editor 同时生成播客任务格式修复

- 现象：`test both` 测试中主视频任务已提交成功，但播客视觉化任务提交失败，提示 `音频格式仅支持 mp3、m4a、wav、aac、flac、ogg`。
- 原因：`video-editor.html` 的资源上传会保存为 `data/uploads/MP3_时间戳` 这类无扩展名文件；主视频合成可正常识别，但 `submit-from-path` 只按路径后缀校验音频格式，导致误判。
- 修复：`submit-from-path` 对无后缀音频改为使用 `ffprobe` 探测媒体时长，能识别为音频就允许提交。
- 验证：`data/uploads/MP3_1780214747538` 虽无 `.mp3` 后缀，但 `ffprobe` 校验通过。

## Video Editor 播客任务音频结束时间修复

- 现象：使用标题 `内资闭门会 | 机器人订单重启...` 测试时，主视频任务提交成功，播客视觉化任务返回 400。
- 原因：`video-editor` 的 `end_offset` 语义是“从音频尾部裁掉多少秒”，不是“绝对结束时间”。前端此前把 `end_offset` 直接传给 podcast 的 `audio_end`，导致出现 `audio_start=80`、`audio_end=38.39` 这种结束时间早于开始时间的参数。
- 修复：提交播客视觉化任务时，`audio_end` 改为 `segment.duration - segment.end_offset`。
