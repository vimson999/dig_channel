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
