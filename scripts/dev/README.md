# Development Scripts

This directory holds local development entrypoints for the frontend, backend, Celery worker, and scheduler.

Run them from the project root or by path, for example `./scripts/dev/run-backend.sh`.

Full local restart:

- `./scripts/dev/restart-dev-stack.sh` 先停止 Flask 后端，再停止所有 Celery worker、Watchdog 和计划任务，最后后台启动后端；后端启动后默认补齐所有 Celery worker，但不启动 scheduler
- `./scripts/dev/stop-backend.sh` 只停止本地 Flask 后端，默认端口 `5001`
- `./scripts/dev/stop-all-background-jobs.sh` 只停止 Celery worker、Watchdog 和计划任务，不停止 Flask 后端

For ZSXQ scheduler lifecycle:

- `./scripts/dev/run-zsxq-auto-scheduler.sh` 启动 `run_zsxq_auto_scheduler.py`
- `./scripts/dev/stop-zsxq-auto-scheduler.sh` 停止对应进程（优先读 pid 文件，兜底清理残留）
- `./scripts/dev/run-zsxq-auto-publish-scheduler.sh` 启动 `run_zsxq_auto_publish_scheduler.py`
- `./scripts/dev/stop-zsxq-auto-publish-scheduler.sh` 停止自动发帖调度器

Content pipeline worker:

- `./scripts/dev/run-celery-content-pipeline.sh` 启动 `content_pipeline` 队列，负责 PDF 清洗引擎适配与文件搬运
- `./scripts/dev/stop-celery-content-pipeline.sh` 停止 `content_pipeline` 队列

MP3 video workflow worker:

- `./scripts/dev/run-celery-mp3-video-workflow.sh` 启动 `mp3_video_workflow` 队列，负责已同步 MP3 到 Gemini 信息图与横竖屏视频编排
- `./scripts/dev/stop-celery-mp3-video-workflow.sh` 停止 `mp3_video_workflow` 队列

Runtime configuration should come from `.env` or shell environment variables. Do not hardcode service credentials in these scripts.

Backend startup bootstrap:

- `./scripts/dev/run-backend.sh` 默认启用 `BACKEND_PROCESS_BOOTSTRAP_ENABLED=1`
- 默认 `BACKEND_PROCESS_BOOTSTRAP_MODE=ensure`，后端启动后补启动缺失的托管 worker；开发环境按进程角色分配数据库池：Web 后端 `WEB_DB_POOL_SIZE=4`，Celery worker `WORKER_DB_POOL_SIZE=1`，scheduler/watchdog `SCHEDULER_DB_POOL_SIZE=1`，且默认 `*_DB_MAX_OVERFLOW=0`
- 每个 Flask/Celery/scheduler 进程都有独立 SQLAlchemy pool，`DB_POOL_SIZE=1` 仅作为未设置角色变量时的兜底值
- 临时强制重启托管进程：`BACKEND_PROCESS_BOOTSTRAP_MODE=restart ./scripts/dev/run-backend.sh`
- 临时关闭自动恢复：`BACKEND_PROCESS_BOOTSTRAP_ENABLED=0 ./scripts/dev/run-backend.sh`
- 默认托管队列是 `all`，watchdog 默认跟随 `BACKEND_PROCESS_BOOTSTRAP_CELERY_SERVICES`；默认不启动 scheduler。可用 `BACKEND_PROCESS_BOOTSTRAP_CELERY_SERVICES`、`CELERY_AUTO_HEAL_SERVICES` 和 `BACKEND_PROCESS_BOOTSTRAP_SCHEDULERS` 指定逗号分隔的服务列表
