# 2026-06-23 知识星球系统同步计划未随本地重启启动

## 现象

- 本地重启后，知识星球相关 Celery worker 和 watchdog 在线。
- 页面仍没有自动进入“知识星球同步 -> 清洗 -> 发布”的系统同步编排。
- `/api/v1/zsxq/system-sync-scheduler/status` 返回 `running: false`。

## 影响

- 用户会看到 worker 在线，但核心计划任务没有触发。
- 如果只看 Celery 监控，容易误判为系统已经完整恢复。

## 根因

- 后端进程自举默认值是 `BACKEND_PROCESS_BOOTSTRAP_SCHEDULERS=none`。
- `scripts/dev/run-backend.sh`、`scripts/dev/restart-dev-stack.sh` 和 `src/backend/app.py` 都沿用了这个默认值。
- 因此重启只补齐了 Celery worker 和 watchdog，没有补齐核心系统同步 scheduler。

## 修复

- 将后端自举默认 scheduler 改为 `zsxq_auto`。
- 本地重启脚本默认启动知识星球系统同步计划。
- 独立自动发帖 scheduler `zsxq_auto_publish` 仍保持手动启动，避免每 3 分钟误发布本地文件夹内容。
- 如需在修复验证期间禁止 scheduler 自动产生新批次，可显式设置：

```bash
BACKEND_PROCESS_BOOTSTRAP_SCHEDULERS=none ./scripts/dev/restart-dev-stack.sh
```

## 验证

- 核心系统同步 scheduler 状态：

```bash
curl http://localhost:5001/api/v1/zsxq/system-sync-scheduler/status
```

- 修复后实测：
  - `running: true`
  - `pid: 29274`
  - `next_run: 2026-06-23T23:15:36+08:00`

## 纪律

- 以后判断“知识星球是否启动”，必须同时确认：
  - `zsxq_sync`、`zsxq_workflow`、`zsxq_publish` worker 在线。
  - Celery watchdog 在线。
  - `/api/v1/zsxq/system-sync-scheduler/status` 为 `running: true`。
- 自动发帖 scheduler 与系统同步 scheduler 是两条不同链路，不允许用一个的状态替代另一个。
