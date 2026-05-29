# 2026-05-29 工作归档

## 知识星球附件类型筛选

- 场景：前沿信息收录的“调研纪要”tab 同时包含 MP3 和 PDF，用户希望该 tab 只下载 MP3。
- 前端 `zsxq-lab.html`：
  - 在主题工具栏新增“附件类型”下拉框。
  - 支持 `只下载 PDF`、`只下载 MP3`、`全部附件`。
- 前端 `zsxq-lab.js`：
  - “排队下载选中附件”会按当前附件类型过滤文件。
  - “同步当前 Tab / 同步全部 Tab”会把当前附件类型传给后端。
  - 附件列表不再固定显示 PDF，改为按扩展名显示 `PDF/MP3/FILE` 标签。
  - 同步日志文案从“PDF 下载”改为“附件下载”，避免类型切换后误导。
- 后端 `zsxq_sync_service.py`：
  - 新增 `download_file_types` 参数，兼容旧的 `download_pdf_only`。
  - 自动同步下载支持 `pdf`、`mp3`、`all`。
  - 下载文件不再强制追加 `.pdf` 后缀；PDF 仍保留文件头校验，MP3 按原文件名保存。
  - 仍保持附件下载之间随机等待 9-12 秒。
- 后端 `zsxq_service.py`：
  - 附件 payload 识别补充 `mp3/m4a/wav/aac` 等音频扩展名。

## 使用方式

- 在“调研纪要”tab 中，把工具栏里的“附件类型”切换为 `只下载 MP3`。
- 再点击“同步当前 Tab”，后端只会自动下载 MP3，PDF 会跳过。
- 如果手动批量下载，勾选主题后点击“排队下载 MP3 附件”，也只会下载 MP3。

## 验证

- `node --check src/frontend/js/zsxq-lab.js`
- `python3 -m py_compile src/backend/services/zsxq_sync_service.py src/backend/apis/v1/zsxq.py src/backend/services/zsxq_service.py`
- `git diff --check`

## Video Editor 任务失败排查

- 现象：`video-editor.html` 最近提交的 5 个任务在前端没有执行成功。
- 排查结果：
  - 最近 5 个 `PublicationTask` 在 DB 中均停留在 `pending/init/0%`。
  - 对应 Celery result 中，4 个任务已经是 `FAILURE`，主要错误为云端 PostgreSQL 连接超时后触发 `PendingRollbackError`。
  - 当前 Celery 在线 worker 只有 `cloud_tts`，没有 `render` worker；`render` 队列仍有积压任务。
- 根因判断：
  - 视频合成任务在长时间 FFmpeg/ASR 处理过程中，多次复用同一个 SQLAlchemy Session 提交进度。
  - 云端 PostgreSQL 短暂超时后，Session 未 rollback，后续失败状态写入也失败，导致 DB 状态仍显示 `pending`。
  - render worker 当前未在线，新的 render 队列任务不会被消费。
- 修复：
  - `src/backend/tasks/publisher.py` 新增 `update_task_record()`，每次任务状态写入使用短事务、rollback 和重试。
  - 发布流水线中的状态、进度、结果写入改为通过该函数持久化，避免 DB 短暂超时污染长任务 Session。
  - 异常处理阶段先 rollback/retry 再写入 `failed`，避免再次出现 `PendingRollbackError` 把真实错误盖掉。

## 验证补充

- `python3 -m py_compile src/backend/tasks/publisher.py src/backend/services/video_editor.py src/backend/celery_config.py`
- `git diff --check`

## 知识星球同步 `Failed to fetch` 排查

- 现象：`zsxq-lab.html` 同步日志显示 `同步失败：Failed to fetch`。
- 排查纪律：先查看项目文档与约定日志目录，只检查 `logs/app.log`、`logs/scheduler.log`、`logs/slow_query.log` 的相关尾部内容。
- 结论：
  - 本地后端接口仍可访问，`/api/v1/zsxq/sync/runs` 返回正常。
  - 同步 run `41061904-6863-4705-ad69-21023cc5eb3c` 从 15:54:58 开始后停留在 `running`。
  - `logs/scheduler.log` 记录到 16:05:50、16:06:16 Flask 因 `src/backend/tasks/publisher.py` 文件变化触发 debug reloader 自动重启。
  - 重启打断了正在执行的同步 HTTP 请求，浏览器因此报 `Failed to fetch`，DB run 没机会写成 `completed/failed`。
- 修复：
  - `src/backend/app.py` 改为读取 `FLASK_DEBUG` 和 `FLASK_USE_RELOADER`。
  - `scripts/dev/run-backend.sh` 默认设置 `FLASK_USE_RELOADER=0`，避免长任务被自动重载打断。
  - `src/backend/services/zsxq_sync_service.py` 在同步过程中按 topic 提交进度，降低中断后状态丢失。
  - 同步日志查询时会把 15 分钟无进展的 `running` run 自动标记为 `failed`，错误信息说明为后端重启、连接断开或长时间无进度。
- 验证：
  - `python3 -m py_compile src/backend/app.py src/backend/services/zsxq_sync_service.py`
  - 调用本地 `/api/v1/zsxq/sync/runs`，旧 `running` run 已自动标记为 `failed`。
