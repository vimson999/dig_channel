# 音频转文字页面设计

**状态：** 产品设计已确认，功能已实现；独立代码评审发现的数据库断连恢复问题已修复并复核。运行验收记录见 `docs/operations/audio-transcription.md`。

## 目标

在现有系统中新增一个独立的“音频转文字”页面。用户每次上传一个音频文件，系统异步识别一次，并同时交付标准 SRT 字幕和不含时间戳的纯文本音频文案。任务、输入文件和输出结果必须可持久恢复；刷新或关闭页面不能中断后台处理。

本功能复用现有 `PublicationTask`、Celery、`transcribe_with_cache()`、登录鉴权和统一导航，不创建第二套 ASR 实现。

## 已确认的产品原则

1. 新增独立页面，不改造成现有“批量音频转文案”目录扫描页面。
2. 单次只接收一个音频文件。
3. 一次识别同时生成 SRT 和纯文本，用户无需预先二选一。
4. 页面面向通用简体中文音频，不默认注入财经或研报术语偏置。
5. 任务必须接入 `PublicationTask + Celery`，刷新、关闭浏览器或 Worker 重启后仍可查询。
6. 页面展示当前用户的最近任务；失败任务可使用原始上传文件重试。
7. 单文件默认上限为 2 GiB，并允许由环境变量配置。
8. 页面只做上传与文字提取，不加入音频编辑、AI 润色、视频生成或发布能力。

## 非目标

- 不支持一次上传多个文件；批处理继续由现有 `batch-transcription.html` 承担。
- 不提供说话人识别、多语言自动检测、翻译、摘要或内容改写。
- 不允许用户输入服务器本地路径，也不复用当前同步 `/resources/transcribe` 接口。
- 不在 Web 请求线程中执行 ASR。
- 首版不提供任务或原始文件删除功能；数据保留遵循项目现有清理策略。

## 方案选择

采用独立的持久化工作流：专用 API 负责校验上传、建立任务和投递 Celery；专用任务复用 ASR 缓存并生成两个输出；专用页面轮询任务状态并提供预览、复制和下载。

不扩展现有批量转写实现，因为它围绕服务器本地目录扫描，状态只保存在进程内存中。也不组合通用资源上传与同步转写接口，因为长音频会阻塞 HTTP 请求，而且缺少任务所有权、恢复和历史记录。

## 页面与交互

新增 `audio-transcription.html` 和对应脚本，并注册到 `shared-navigation.js`，菜单名称为“音频转文字”。页面使用现有 Tailwind、图标、鉴权和导航模式，不引入新的前端框架。

页面由三块组成：

1. **上传卡片**
   - 拖拽区和文件选择器只接受一个文件。
   - 支持 MP3、M4A、WAV、AAC、FLAC、OGG 和 OPUS。
   - 显示原始文件名、大小及 2 GiB 默认上限。
   - 提供折叠的“识别提示词”输入框。留空时使用稳定的通用简体中文提示词；该字段只帮助专有名词识别，不改变输出格式。
2. **当前任务**
   - 显示 `上传中 → 排队中 → 转写中 → 生成结果 → 已完成` 的真实阶段。
   - 失败时显示明确中文错误和“重试”操作。
   - 完成后提供“纯文本”和“SRT 字幕”两个页签；两者均可预览、复制和下载。
3. **最近任务**
   - 按创建时间倒序分页显示当前用户任务。
   - 显示文件名、大小、状态、进度、创建时间和可用操作。
   - 点击记录可恢复当前任务视图；只有失败任务显示重试。

纯文本由非空识别片段按顺序合并，不包含序号或时间戳。实现保留原识别文本中的标点，并以可读段落分隔；不做大模型润色或事实修正。SRT 使用连续的一基序号和 `HH:MM:SS,mmm` 时间格式，空片段不生成字幕项。

前端轮询必须单飞、有界并只查询活动任务；标签页隐藏时停止轮询，重新可见时立即刷新一次。终态任务从历史列表或详情接口读取，不持续轮询。上传阶段显示基于 `XMLHttpRequest.upload` 的实际字节进度，ASR 阶段只显示服务端真实步骤和任务进度，不伪造百分比。

## 任务与文件模型

父任务 feature 固定为 `audio_transcription`。首版复用 `PublicationTask` 的现有字段，不新增数据库迁移。

`params` 保存冻结输入：

```json
{
  "feature": "audio_transcription",
  "user_id": "<owner>",
  "audio_transcription": {
    "original_filename": "访谈.m4a",
    "source_path": "/受控上传目录/<task-id>/source.m4a",
    "source_bytes": 123456,
    "source_sha256": "...",
    "model_size": "medium",
    "initial_prompt": "可选用户提示词",
    "prompt_mode": "generic_zh"
  }
}
```

`result` 只保存阶段、有限日志、识别统计和输出文件元数据：

```json
{
  "stage": "completed",
  "segment_count": 42,
  "duration_seconds": 318.5,
  "cache": {"hit": false, "key_prefix": "..."},
  "text": {"path": "/受控输出目录/<task-id>/transcript.txt", "bytes": 4567},
  "srt": {"path": "/受控输出目录/<task-id>/transcript.srt", "bytes": 6789},
  "logs": []
}
```

API 响应不返回 `source_path`、输出绝对路径、完整 `params` 或完整 `result`。列表和轮询使用投影列与有限载荷，不能为了进度轮询反复读取大 JSON 或在响应中返回全文。完整 TXT/SRT 内容由有所有权检查的预览/下载接口按需读取。

文件位置遵循项目目录纪律：

- 原始上传：项目根目录 `data/uploads/audio_transcription/<task-id>/source.<ext>`。
- 输出文件：项目根目录 `outputs/audio_transcription/<task-id>/transcript.txt` 和 `transcript.srt`。
- ASR 缓存：继续使用 `asr_cache_service` 的既有缓存目录。

任务目录由受约束的 UUID 构造；服务器内部文件名不使用用户文件名。原始文件名清理控制字符并限制长度后只用于展示和下载名。路径解析必须验证目标仍位于配置的专用根目录内。

## 上传边界与 2 GiB 支持

环境变量 `AUDIO_TRANSCRIPTION_MAX_UPLOAD_MB` 默认 `2048`，解析后约束到 1–8192 MiB。API 同时检查请求声明大小和流式写入后的实际大小；空文件、超限文件立即删除未完成上传并返回 400/413。

服务端不能调用 `read()` 把整个文件载入内存。上传使用 Werkzeug 文件流落盘，并在写入/落盘过程中完成有界大小检查和 SHA-256 计算。扩展名只是第一道检查；入队前使用现有 FFmpeg/ffprobe 能力验证文件包含可解码音轨。校验失败不创建可运行任务，也不保留孤立文件。

当前 Nginx 模板的全局上传上限为 10–100 MiB，因此实现必须同步修改以下部署来源，为创建任务的精确路由增加 `client_max_body_size 2050M`（2048 MiB 文件加 multipart 开销），而不是放大全站上传上限：

- `scripts/deployment/nginx/youtube-analytics.conf`
- `scripts/deployment/docker/nginx.conf`
- `scripts/deployment/scripts/setup-nginx.sh`

该精确 location 继续执行现有 `auth_request`，并设置适合大文件持续上传的 `proxy_request_buffering off` 与有界 `proxy_send_timeout`。生产环境仍需从这些受版本控制的模板重新加载 Nginx 后，2 GiB 上限才真正生效。

## HTTP API

Blueprint 前缀为 `/api/audio-transcriptions`。所有路由均使用现有 `require_login`，并且详情、预览、下载和重试只能访问 `PublicationTask.user_id` 与当前用户匹配的任务。

| 方法 | 路由 | 作用 |
| --- | --- | --- |
| `POST` | `/api/audio-transcriptions/jobs` | 接收单个 multipart 音频，校验、创建任务并投递。 |
| `GET` | `/api/audio-transcriptions/jobs` | 分页返回当前用户最近任务的有限摘要。 |
| `GET` | `/api/audio-transcriptions/jobs/<id>` | 返回任务状态和输出元数据；`poll=1` 时使用最小轮询载荷。 |
| `GET` | `/api/audio-transcriptions/jobs/<id>/text` | 所有权校验后预览或下载 TXT。 |
| `GET` | `/api/audio-transcriptions/jobs/<id>/srt` | 所有权校验后预览或下载 SRT。 |
| `POST` | `/api/audio-transcriptions/jobs/<id>/retry` | 仅用仍然有效且哈希一致的原始音频重投失败任务。 |

创建接口在文件完全保存、校验并提交数据库后才发布 Celery 消息。若消息发布失败，任务进入明确的 `failed`/`enqueue_failed` 状态，文件和任务保留供重试，接口返回可操作错误而不是伪报已排队。

## Celery、ASR 与事务边界

新增 `tasks.audio_transcription.process_job`，注册到 Celery include 与 task routes。首版复用已有 `report_audio_workflow` 长音频队列，避免新增没有 Worker 消费者的队列。任务使用现有 `maybe_guarded_apply_async`、late acknowledgement 和业务表状态，不依赖 Celery result backend 表示业务完成。

任务步骤为：

```text
queued → validating → transcribing → writing_outputs → completed
```

Worker 只在短事务内读取任务快照并把状态更新为 `transcribing`，随后调用 `end_db_transaction_before_external_io()`，再校验源文件哈希并执行 ASR。ASR 完成后重新开启短事务写入有限结果。任何模型加载、音频解码、哈希扫描、ASR、文件写入或重试等待期间都不得持有数据库事务或 ORM 对象。

任务调用 `transcribe_with_cache(..., model_size="medium", initial_prompt=<generic prompt>, include_metadata=True)` 一次取得带时间的片段。通用提示词由本功能服务层定义并显式传入，避免落入 `asr_service` 的财经默认提示词。用户自定义提示词存在时，将其与简短的通用简体中文约束组合，并参与既有缓存键。

TXT 和 SRT 先写入任务目录中的随机临时文件，完成 fsync/关闭后原子替换最终文件。两个输出都存在、可读取且至少含一个有效片段时，任务才进入 `completed`。重复执行已完成任务时验证既有源哈希和输出元数据并安全返回；失败重试清理本任务未登记的临时文件，不破坏已登记终态产物。

## 错误与恢复

- 缺少文件、空文件、格式不支持、超过限制或音轨不可解码：请求阶段拒绝并给出中文原因。
- 用户文件名异常：使用安全展示名；内部路径不受影响。
- 源文件在任务执行前缺失或哈希变化：任务失败，不静默识别另一文件。
- ASR 后端不可用、模型缺失、识别异常或返回空片段：任务进入 `failed`，保留原始文件以便修复环境后重试。
- 写盘失败或任一输出验证失败：任务失败且不暴露半成品下载链接。
- Worker/Redis 暂时不可用：创建阶段记录入队失败；失败任务可重新投递。
- 下载时任务未完成、文件未登记、文件缺失或越过受控目录：分别返回 409、404 或 403，不泄露绝对路径。

重试沿用同一个 `PublicationTask`，增加有界 attempt 记录并清除旧的瞬态错误。活动中的任务返回 409，避免同一源文件并发执行；已完成任务不允许重试。重试前再次验证用户所有权、源路径、大小和 SHA-256。

## 测试策略

实现严格遵循红—绿—重构；每项生产行为先由一个会因功能缺失而失败的测试描述。

1. **纯服务测试**
   - 扩展名、展示文件名、大小上限和受控路径。
   - 通用提示词不包含财经/研报偏置。
   - 纯文本合并、空片段过滤和段落格式。
   - SRT 连续序号、小时边界、毫秒舍入及非法时间修正。
   - 原子输出和源文件哈希验证。
2. **API 测试**
   - 未登录、跨用户读取/重试/下载均被拒绝。
   - 单文件成功上传并精确投递到既有长音频队列。
   - 缺失、多文件、空文件、扩展名错误、不可解码和超过 2 GiB 均有稳定响应。
   - 列表分页、最小轮询载荷、预览、下载和失败重试。
   - 入队失败不会留下伪运行状态。
3. **Celery 任务测试**
   - ASR 只调用一次，两个结果来自同一组片段。
   - 成功、空结果、ASR 异常和写盘异常的终态正确。
   - 重复执行、失败重试和缓存命中保持幂等。
   - 外部 I/O 前释放数据库事务，会话在任务结束时清理。
4. **前端契约与交互测试**
   - 页面接入统一导航，只接受一个文件并展示 2 GiB 限制。
   - 上传真实进度、任务阶段、TXT/SRT 页签、复制、下载、历史恢复和重试入口。
   - 轮询单飞、有界，只处理活动任务，并在隐藏页暂停。
   - 用户文件名和错误消息使用安全 DOM 写入，不通过未转义 `innerHTML` 注入。
5. **部署配置测试**
   - 三个 Nginx 配置来源都只对创建任务路由声明 2050M 请求上限，应用限制实际文件为 2048 MiB，并保留既有鉴权。
   - Celery include 和 task route 与运行 Worker 使用的队列一致。

## 验收标准

1. 登录用户可从统一菜单进入“音频转文字”，上传一个受支持的通用中文音频。
2. 最大 2 GiB 的合同在前端、Flask 校验和所有受版本控制的 Nginx 配置来源中一致；上传过程不把整个文件载入应用内存。
3. 页面刷新或关闭后任务继续运行，重新进入页面能从最近任务恢复状态和结果。
4. 每个成功任务只执行一次 ASR，并同时提供内容一致的 TXT 与毫秒级标准 SRT 预览、复制和下载。
5. 任务历史、详情、重试、预览和下载均严格隔离用户，API 不泄露本地绝对路径或完整任务 JSON。
6. 失败不会误报成功或暴露半成品；原始音频有效时可从同一任务安全重试并复用 ASR 缓存。
7. Web 请求和 ASR 外部 I/O 不持有数据库事务；轮询不读取大 JSON、不重叠请求、不在隐藏页面持续请求。
8. 新增服务、API、任务、页面、部署配置和导航契约测试通过，相关既有 ASR、任务与共享导航测试继续通过。

## 实施约束

- 保持当前工作树中用户已有的未提交改动不变；提交只包含本功能文件和必要的共享注册点。
- 不修改现有批量转写和通用资源接口的行为。
- 不新增数据库表或迁移，除非实施阶段发现无法用现有投影满足轮询纪律；若出现该情况必须停止并重新确认设计。
- 所有源代码位于 `src/`；测试位于根目录 `tests/`；上传、输出、缓存和日志只能位于项目根目录对应数据目录。
- 不在实现中扩大已确认范围。
