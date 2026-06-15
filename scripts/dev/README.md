# Development Scripts

This directory holds local development entrypoints for the frontend, backend, Celery worker, and scheduler.

Run them from the project root or by path, for example `./scripts/dev/run-backend.sh`.
For ZSXQ scheduler lifecycle:

- `./scripts/dev/run-zsxq-auto-scheduler.sh` 启动 `run_zsxq_auto_scheduler.py`
- `./scripts/dev/stop-zsxq-auto-scheduler.sh` 停止对应进程（优先读 pid 文件，兜底清理残留）
- `./scripts/dev/run-zsxq-auto-publish-scheduler.sh` 启动 `run_zsxq_auto_publish_scheduler.py`
- `./scripts/dev/stop-zsxq-auto-publish-scheduler.sh` 停止自动发帖调度器

Content pipeline worker:

- `./scripts/dev/run-celery-content-pipeline.sh` 启动 `content_pipeline` 队列，负责 PDF 清洗引擎适配与文件搬运
- `./scripts/dev/stop-celery-content-pipeline.sh` 停止 `content_pipeline` 队列

Runtime configuration should come from `.env` or shell environment variables. Do not hardcode service credentials in these scripts.

Backend startup bootstrap:

- `./scripts/dev/run-backend.sh` 默认启用 `BACKEND_PROCESS_BOOTSTRAP_ENABLED=1`
- 默认 `BACKEND_PROCESS_BOOTSTRAP_MODE=restart`，后端启动后会重启 Celery Monitor 中登记的 worker、Celery watchdog，并恢复已配置的调度器
- 临时只确保进程存在、不强制重启：`BACKEND_PROCESS_BOOTSTRAP_MODE=ensure ./scripts/dev/run-backend.sh`
- 临时关闭自动恢复：`BACKEND_PROCESS_BOOTSTRAP_ENABLED=0 ./scripts/dev/run-backend.sh`
- 可用 `BACKEND_PROCESS_BOOTSTRAP_CELERY_SERVICES` 和 `BACKEND_PROCESS_BOOTSTRAP_SCHEDULERS` 指定逗号分隔的服务列表
