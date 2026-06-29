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

Report Top10 workflow worker:

- `./scripts/dev/run-celery-report-top10-workflow.sh` 启动 `report_top10_workflow` 队列，负责 10 篇研报单篇文案缓存/解析与总编辑 Gem 编排
- `./scripts/dev/stop-celery-report-top10-workflow.sh` 停止 `report_top10_workflow` 队列

Report script material workflow worker:

- `./scripts/dev/run-celery-report-script-material-workflow.sh` 启动 `report_script_material_workflow` 队列，负责 PDF 到可复用单篇口播文案物料
- `./scripts/dev/stop-celery-report-script-material-workflow.sh` 停止 `report_script_material_workflow` 队列

Runtime configuration should come from `.env` or shell environment variables. Do not hardcode service credentials in these scripts.

Backend startup bootstrap:

- `./scripts/dev/run-backend.sh` 默认启用 `BACKEND_PROCESS_BOOTSTRAP_ENABLED=1`
- 默认 `BACKEND_PROCESS_BOOTSTRAP_MODE=ensure`，后端启动后补启动缺失的托管 worker；开发环境按进程角色分配数据库连接：Web 后端 `WEB_DB_POOL_SIZE=4` 保留小连接池，Celery worker 与 scheduler/watchdog 默认 `WORKER_DB_POOL_MODE=null` / `SCHEDULER_DB_POOL_MODE=null`，用完即释放连接，且默认 `*_DB_MAX_OVERFLOW=0`
- 每个 Flask/Celery/scheduler 进程都会独立初始化 SQLAlchemy engine，`DB_POOL_SIZE=1` 仅作为未设置角色变量时的兜底值；连接会设置 `application_name`，便于在 `pg_stat_activity` 中定位占用来源
- 任务准入保护默认启用：数据库压力过高、同类运行任务超过并发预算或同类排队任务超过积压预算时，任务会进入 `deferred_db_pressure` / `deferred_capacity` / `deferred_backlog` 并自动延迟重试，不会继续压数据库
- 手动停止 worker 会写入暂停状态；看门狗和后端启动自举不会自动拉起已暂停的服务。恢复时使用监控页启动按钮或接口 `/api/v1/celery/resume/<service_id>`
- 默认 `LOCAL_PGBOUNCER_ENABLED=1`，`run-backend.sh` 会确保本地 PgBouncer 启动；真实直连库地址保存到 `.env` 的 `DIRECT_DATABASE_URL`，应用侧 `DATABASE_URL` 指向 `127.0.0.1:6432`
- 临时强制重启托管进程：`BACKEND_PROCESS_BOOTSTRAP_MODE=restart ./scripts/dev/run-backend.sh`
- 临时关闭自动恢复：`BACKEND_PROCESS_BOOTSTRAP_ENABLED=0 ./scripts/dev/run-backend.sh`
- 默认托管队列是 `all`，watchdog 默认跟随 `BACKEND_PROCESS_BOOTSTRAP_CELERY_SERVICES`；默认启动知识星球系统同步计划 `zsxq_auto`。可用 `BACKEND_PROCESS_BOOTSTRAP_CELERY_SERVICES`、`CELERY_AUTO_HEAL_SERVICES` 和 `BACKEND_PROCESS_BOOTSTRAP_SCHEDULERS` 指定逗号分隔的服务列表，设为 `none` 可临时禁用 scheduler 自启动
