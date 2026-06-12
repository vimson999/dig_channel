# Hermes API

本文件只整理 Hermes 需要调用的稳定接口，范围对应三个页面：

- `http://localhost:8888/video-editor.html`
- `http://localhost:8888/podcast-visualizer.html`
- `http://localhost:8888/tts.html`
- `http://localhost:8888/pdf-scroll-video.html`
- `http://localhost:8888/zsxq-auto-publish.html`

默认后端地址：

```text
http://127.0.0.1:5001
```

当前开发环境中，部分接口带 `require_login`，但 DEBUG 模式会 fallback 到本地 admin/test 用户。Hermes 在本机使用时通常可以直接调用；如果后续给非本机系统使用，应再补统一鉴权。

## 通用约定

异步任务通常遵循：

```text
submit -> task_id -> poll task -> completed/failed -> download/view result
```

常见任务状态：

```text
pending / processing / completed / failed / partial_failed
```

常见字段：

- `task_id`: 任务 ID。
- `celery_id`: Celery 后台任务 ID。
- `status`: 任务状态。
- `current_step`: 当前步骤。
- `progress`: 进度百分比。
- `params`: 提交时的参数。
- `result`: 生成结果，例如 `video_path`、音频段落路径等。
- `error_message`: 失败原因。

## 1. TTS：豆包语音 WebSocket V3

页面：`tts.html`

队列：`cloud_tts`

### 1.1 检查 TTS 配置

```http
GET /api/v2/tts/status
```

返回示例：

```json
{
  "success": true,
  "data": {
    "providers": [
      {
        "id": "doubao_ws_v3",
        "name": "豆包语音 WebSocket V3",
        "configured": true,
        "resource_id": "seed-tts-2.0",
        "formats": ["mp3", "ogg_opus", "pcm"]
      }
    ]
  }
}
```

### 1.2 提交 TTS 合成任务

```http
POST /api/v2/tts/submit
Content-Type: application/json
```

请求体：

```json
{
  "title": "测试-1",
  "segments": [
    "第一段需要合成的文字。",
    "第二段需要合成的文字。"
  ],
  "config": {
    "resource_id": "seed-tts-2.0",
    "model": "seed-tts-2.0-standard",
    "voice_type": "zh_female_mizai_uranus_bigtts",
    "format": "mp3",
    "sample_rate": 24000,
    "bit_rate": 128000,
    "speed": 1.0,
    "loudness": 1.0,
    "emotion": "",
    "emotion_scale": 4,
    "enable_subtitle": true
  }
}
```

常用音色：

```text
黑猫 zh_female_mizai_uranus_bigtts
清澈 zh_female_qingchezizi_uranus_bigtts
刘飞 zh_male_liufei_uranus_bigtts
大台 zh_male_dayi_uranus_bigtts
```

返回示例：

```json
{
  "success": true,
  "message": "任务已提交，正在后台排队处理",
  "data": {
    "article_id": "task-id",
    "task_id": "task-id",
    "celery_id": "celery-id"
  }
}
```

### 1.3 查询 TTS 任务列表

```http
GET /api/v2/tts/history?page=1&per_page=20
```

返回中的 `first_audio_segment.audio_url` 可用于试听/下载第一段成功音频。

### 1.4 查询 TTS 任务详情

```http
GET /api/v2/tts/article/{task_id}
```

返回示例节选：

```json
{
  "success": true,
  "data": {
    "id": "task-id",
    "title": "测试-1",
    "status": "completed",
    "segments": [
      {
        "sequence": 1,
        "text": "第一段需要合成的文字。",
        "status": "success",
        "audio_url": "/api/v2/tts/task/task-id/segment/1/audio",
        "subtitle_url": "/api/v2/tts/task/task-id/segment/1/subtitle"
      }
    ]
  }
}
```

### 1.5 下载 TTS 音频

```http
GET /api/v2/tts/task/{task_id}/segment/{sequence}/audio
```

返回文件下载。

### 1.6 下载 TTS SRT

```http
GET /api/v2/tts/task/{task_id}/segment/{sequence}/subtitle
```

仅当豆包返回字幕时间戳并生成了 SRT 时可用。

## 2. 播客视觉化视频

页面：`podcast-visualizer.html`

队列：`render`

### 2.1 生成预览帧

```http
POST /api/v1/documents/podcast-visualizer/preview-frame
Content-Type: application/json
```

请求体：

```json
{
  "title": "盘点鲍威尔八年功与过，他给美联储留下了什么？",
  "headline_subtitle": "机构资金主导超额收益",
  "subtitle": "2026-05-19 / 精选片段",
  "brand_text": "胶囊播客",
  "caption_text": "美债收益率持续高企，意味着市场正在重新定价长期资金成本。",
  "orientation": "landscape",
  "srt_selected": false
}
```

返回：

```text
image/png
```

### 2.2 提交播客视频任务

```http
POST /api/v1/documents/podcast-visualizer/submit
Content-Type: multipart/form-data
```

表单字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `audio` | file | 是 | 音频，支持 mp3/m4a/wav/aac/flac/ogg |
| `srt` | file | 否 | 字幕文件，只支持 `.srt` |
| `title` | text | 否 | 视频标题 |
| `headline_subtitle` | text | 否 | 标题下方副标题 |
| `subtitle` | text | 否 | 时间/栏目 label |
| `brand_text` | text | 否 | 唱片中心品牌文字 |
| `caption_text` | text | 否 | 手动字幕兜底 |
| `audio_start` | text | 否 | 开始时间，支持秒或 `mm:ss.s` |
| `audio_end` | text | 否 | 截止时间，支持秒或 `mm:ss.s` |
| `audio_speed` | number | 否 | 音频倍速 |
| `auto_asr` | boolean text | 否 | `true/false`，是否本地 ASR |
| `asr_model` | text | 否 | 默认 `medium` |
| `orientation` | text | 否 | `landscape` 或 `portrait` |
| `fps` | number | 否 | 默认 30 |
| `crf` | number | 否 | 默认 20 |

注意：如果没有上传 SRT，也没有开启 `auto_asr`，并且没有 `caption_text`，页面会拒绝提交。后端任务也要求最终必须有字幕，否则失败，不生成无字幕视频。

返回示例：

```json
{
  "success": true,
  "task_id": "task-id",
  "celery_id": "celery-id",
  "message": "播客视频任务已提交"
}
```

### 2.3 查询播客任务列表

```http
GET /api/v1/documents/podcast-visualizer/tasks?page=1&per_page=20
```

### 2.4 查询播客任务详情

```http
GET /api/v1/documents/podcast-visualizer/task/{task_id}
```

### 2.5 下载播客视频

```http
GET /api/v1/documents/podcast-visualizer/task/{task_id}/download
```

返回 `video/mp4` 文件下载。

## 3. 视频编辑 / 多音频合成视频

页面：`video-editor.html`

队列：`render`

这个页面目前主要复用 `publisher` 和 `resources` API。Hermes 推荐使用这套异步接口，而不是旧的 `/api/v1/video-editor/synthesize` 同步接口。

### 3.1 上传素材

```http
POST /api/v1/resources/upload
Content-Type: multipart/form-data
```

表单字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `file` | file | 是 | 音频、图片或视频 |

支持扩展名：

```text
png, jpg, jpeg, gif, mp4, mov, avi, mp3, wav, m4a, webp
```

返回示例：

```json
{
  "success": true,
  "message": "File uploaded successfully",
  "path": "data/uploads/audio_1770000000000.mp3",
  "filename": "audio_1770000000000.mp3"
}
```

### 3.2 查看或下载资源

```http
GET /api/v1/resources/view?path={relative_path}
```

`relative_path` 使用上传接口返回的 `path`，例如：

```text
data/uploads/audio_1770000000000.mp3
```

### 3.3 本地 ASR 提取音频文案

```http
GET /api/v1/resources/transcribe?path={relative_path}
```

也支持：

```http
POST /api/v1/resources/transcribe
Content-Type: application/json
```

请求体：

```json
{
  "path": "data/uploads/audio_1770000000000.mp3"
}
```

返回示例：

```json
{
  "success": true,
  "text": "识别出的音频文案"
}
```

### 3.4 提交视频合成任务

```http
POST /api/v1/publisher/submit
Content-Type: application/json
```

请求体示例：

```json
{
  "audio_segments": [
    {
      "path": "data/uploads/audio_1.mp3",
      "speed": 1.0,
      "start": 0,
      "end_offset": 0,
      "subtitle": ""
    },
    {
      "path": "data/uploads/audio_2.mp3",
      "speed": 1.1,
      "start": 12.5,
      "end_offset": 3.0,
      "subtitle": ""
    }
  ],
  "aspect_ratio": "16:9",
  "asr_model": "medium",
  "export_params": {
    "resolution": "1080p",
    "bitrate": "8M",
    "fps": 30
  },
  "bilibili": {
    "title": "视频标题",
    "desc": "高级剪辑版生成"
  },
  "thumbnail": {
    "main_title": "视频标题"
  },
  "video": {
    "enabled": true,
    "image_path": "data/uploads/background.mp4",
    "media_type": "video",
    "sub_burn_enabled": true,
    "cta": null,
    "subtitle_position": {
      "y_pct": 0.9
    },
    "subtitle_size": "large"
  }
}
```

说明：

- `audio_segments[].path` 来自 `/api/v1/resources/upload`。
- `audio_segments[].start` 是该音频内部开始时间，单位秒。
- `audio_segments[].end_offset` 是从该音频尾部裁掉的秒数。
- `audio_segments[].speed` 是音频倍速。
- `video.image_path` 可以是图片或视频背景路径。
- `video.media_type` 为 `image` 或 `video`。
- 横屏字幕默认位置建议 `0.9`，竖屏默认位置建议 `0.72`。

返回示例：

```json
{
  "success": true,
  "message": "Task submitted",
  "task_id": "task-id",
  "celery_id": "celery-id"
}
```

### 3.5 查询视频合成任务

```http
GET /api/v1/publisher/task/{task_id}
```

成功后，`data.result.video_path` 是生成视频路径。

### 3.6 查询最近视频合成任务

```http
GET /api/v1/publisher/tasks
```

返回最近 20 条 `PublicationTask`。

### 3.7 下载生成视频

视频任务完成后：

```http
GET /api/v1/resources/view?path={data.result.video_path}
```

## 4. 旧同步视频合成接口

页面当前主要不用它，但后端仍保留：

```http
POST /api/v1/video-editor/synthesize
Content-Type: multipart/form-data
```

这个接口是同步合成，适合小任务，不建议 Hermes 第一阶段使用。推荐使用第 3 节的异步 `publisher` 流程。

下载旧接口结果：

```http
GET /api/v1/video-editor/download/{filename}
```

## 5. PDF 滚动视频

页面：`pdf-scroll-video.html`

这个能力目前是同步接口：请求提交后，后端直接返回 MP4 文件，不创建任务，也不进入 Celery 队列。适合 Hermes 对单个 PDF 做即时转换；如果 PDF 很大、时长很长，调用方需要设置足够的 HTTP 超时时间。

### 5.1 PDF 转匀速滚动 MP4

```http
POST /api/v1/documents/pdf-scroll-video
Content-Type: multipart/form-data
```

表单字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `file` | file | 二选一 | 本地 PDF 文件，只支持 `.pdf` |
| `pdf_url` | text | 二选一 | PDF 网络链接 |
| `width` | number | 否 | 视频宽度，默认 1080，范围 360-3840 |
| `height` | number | 否 | 视频高度，默认 1920，范围 360-3840 |
| `duration` | number | 否 | 视频总时长秒数，默认 30，范围 3-600 |
| `start_hold` | number | 否 | 开头停留秒数，默认 1，范围 0-30 |
| `end_hold` | number | 否 | 结尾停留秒数，默认 1，范围 0-30 |
| `fps` | number | 否 | 帧率，默认 30，范围 12-60 |
| `crf` | number | 否 | H.264 压缩质量，默认 20，范围 16-32，越小质量越高 |

常用画布：

```text
竖屏 1080x1920
横屏 1920x1080
高清竖屏 1440x1920
方形 1080x1080
```

返回：

```text
video/mp4
```

响应头：

| Header | 说明 |
| --- | --- |
| `X-Page-Count` | PDF 页数 |
| `X-Long-Image-Width` | 长图宽度 |
| `X-Long-Image-Height` | 长图高度 |
| `X-Video-Width` | 输出视频宽度 |
| `X-Video-Height` | 输出视频高度 |
| `X-Video-Duration` | 输出视频时长 |
| `X-File-Size` | 输出 MP4 字节数 |

文件上传示例：

```bash
curl -X POST http://127.0.0.1:5001/api/v1/documents/pdf-scroll-video \
  -F "file=@/path/to/report.pdf" \
  -F "width=1080" \
  -F "height=1920" \
  -F "duration=30" \
  -F "start_hold=1" \
  -F "end_hold=1" \
  -F "fps=30" \
  -F "crf=20" \
  --output report.scroll.mp4
```

PDF 链接示例：

```bash
curl -X POST http://127.0.0.1:5001/api/v1/documents/pdf-scroll-video \
  -F "pdf_url=https://example.com/report.pdf" \
  -F "width=1920" \
  -F "height=1080" \
  -F "duration=45" \
  --output report.scroll.mp4
```

错误返回示例：

```json
{
  "success": false,
  "message": "请上传 PDF 文件或填写 PDF 链接"
}
```

注意：

- 该接口依赖 `FFmpeg` 和 PDF 渲染能力。
- 如果 `start_hold + end_hold >= duration`，后端会自动把开头/结尾停留都重置为 0。
- 上传文件大小受后端 `PDF_VIDEO_MAX_FILE_SIZE` 限制。
- 返回文件来自临时目录，响应关闭后临时目录会清理；Hermes 应立即保存响应体。

## 5. 知识星球自动发帖

页面：`zsxq-auto-publish.html`

用途：配置 PDF / 音频附件自动发帖策略，支持预览下一批、手动试跑、启动独立调度器、查看运行记录与日志。

### 5.1 读取当前策略

```http
GET /api/v1/zsxq/auto-publish/config
```

返回示例：

```json
{
  "success": true,
  "data": {
    "config": {
      "exists": true,
      "name": "知识星球自动发帖策略",
      "group_id": "123456789",
      "group_name": "突突突快跑",
      "enabled": true,
      "interval_minutes": 30,
      "max_rounds": 0,
      "continuous": true,
      "cookie_env_name": "ZSXQ_AUTO_PUBLISH_COOKIE",
      "cookie_env_configured": false,
      "rules": [
        {
          "id": "pdf",
          "file_type": "pdf",
          "source_dir": "/Users/you/Downloads/伯恩斯坦",
          "batch_size": 4
        }
      ]
    }
  }
}
```

### 5.2 保存自动发帖策略

```http
POST /api/v1/zsxq/auto-publish/config
Content-Type: application/json
```

请求体示例：

```json
{
  "cookie": "zsxq_access_token=...",
  "name": "外资 PDF + 调研音频",
  "group_id": "123456789",
  "group_name": "突突突快跑",
  "enabled": true,
  "interval_minutes": 30,
  "max_rounds": 0,
  "rules": [
    {
      "id": "pdf",
      "label": "PDF 批次",
      "enabled": true,
      "file_type": "pdf",
      "source_dir": "/Users/you/Downloads/伯恩斯坦",
      "recursive": true,
      "extensions": ".pdf",
      "batch_size": 4,
      "upload_retry_count": 2,
      "max_run_attempts": 3,
      "max_file_size_mb": 100,
      "body_prefix": "本批 PDF 如下：",
      "include_filenames": true,
      "hashtag_id": "123",
      "hashtag_title": "测试标签"
    },
    {
      "id": "audio",
      "label": "音频批次",
      "enabled": true,
      "file_type": "audio",
      "source_dir": "/Users/you/Downloads/MP3",
      "recursive": true,
      "extensions": ".mp3,.m4a,.wav",
      "batch_size": 4,
      "upload_retry_count": 2,
      "max_run_attempts": 3,
      "max_file_size_mb": 50,
      "body_prefix": "本批音频如下：",
      "include_filenames": true,
      "hashtag_id": "456",
      "hashtag_title": "调研"
    }
  ]
}
```

说明：

- `cookie` 只用于保存时校验当前 `group_id` 是否真的属于该账号可访问星球。
- 持续调度真正执行时默认读取后端环境变量 `ZSXQ_AUTO_PUBLISH_COOKIE`。
- `batch_size` 当前限制为 1 到 8，方便匹配知识星球附件发帖节奏。

### 5.3 预览下一批候选文件

```http
POST /api/v1/zsxq/auto-publish/preview
Content-Type: application/json
```

请求体与保存策略基本一致，返回会告诉 Hermes 当前规则下：

- `eligible_count`: 当前可发布文件数量
- `skipped_count`: 被跳过数量
- `skipped_reasons`: 跳过原因统计
- `next_batch`: 下一批即将发出的文件

### 5.4 手动触发一次后台试跑

```http
POST /api/v1/zsxq/auto-publish/scheduler/run-once
Content-Type: application/json
```

请求体：

```json
{
  "cookie": "zsxq_access_token=..."
}
```

说明：

- 这里的 `cookie` 是可选的。
- 页面试跑优先使用当前传入的 Cookie，不依赖环境变量。
- 接口返回的是“试跑子进程已排队”，不是同步执行结果；真正结果要再查运行历史。

### 5.5 查询最近运行历史

```http
GET /api/v1/zsxq/auto-publish/runs?limit=20
```

返回中每条 run 会带：

- `status`: `running / success / partial / failed / skipped`
- `message`: 本轮摘要
- `rules`: 每条规则本轮结果
- `topic_id`: 如果发布成功，会记录知识星球话题 ID

### 5.6 查询调度器状态

```http
GET /api/v1/zsxq/auto-publish/scheduler/status
```

用于确认：

- 独立调度器是否运行中
- 当前 PID
- 日志路径
- 当前策略快照
- Cookie 环境变量是否已配置

### 5.7 启动 / 停止持续调度

```http
POST /api/v1/zsxq/auto-publish/scheduler/start
POST /api/v1/zsxq/auto-publish/scheduler/stop
```

说明：

- `start` 依赖已保存策略，且后端环境变量 `ZSXQ_AUTO_PUBLISH_COOKIE` 已配置。
- `stop` 会优雅终止独立调度进程。

### 5.8 读取最近日志

```http
GET /api/v1/zsxq/auto-publish/scheduler/logs?lines=160
```

返回：

```json
{
  "success": true,
  "data": {
    "lines": ["..."],
    "raw": "..."
  }
}
```

适合 Hermes 直接展示最近日志尾部。

## Hermes 推荐调用顺序

### TTS

```text
POST /api/v2/tts/submit
GET  /api/v2/tts/article/{task_id}
GET  /api/v2/tts/task/{task_id}/segment/{sequence}/audio
```

### 播客视频

```text
POST /api/v1/documents/podcast-visualizer/submit
GET  /api/v1/documents/podcast-visualizer/task/{task_id}
GET  /api/v1/documents/podcast-visualizer/task/{task_id}/download
```

### 普通视频合成

```text
POST /api/v1/resources/upload
POST /api/v1/publisher/submit
GET  /api/v1/publisher/task/{task_id}
GET  /api/v1/resources/view?path={video_path}
```

### PDF 滚动视频

```text
POST /api/v1/documents/pdf-scroll-video -> 直接保存 MP4 响应体
```

### 知识星球自动发帖

```text
POST /api/v1/zsxq/auto-publish/config
POST /api/v1/zsxq/auto-publish/preview
POST /api/v1/zsxq/auto-publish/scheduler/run-once
GET  /api/v1/zsxq/auto-publish/runs
GET  /api/v1/zsxq/auto-publish/scheduler/status
```

## 本地服务依赖

使用上述接口前，至少需要：

```text
后端 Flask: 127.0.0.1:5001
Redis/Celery: render worker
TTS: cloud_tts worker
FFmpeg: 视频和预览相关能力
MLX Whisper: ASR 相关能力
```

开发脚本：

```bash
scripts/dev/run-backend.sh
scripts/dev/run-celery.sh
scripts/dev/run-celery-tts.sh
```
