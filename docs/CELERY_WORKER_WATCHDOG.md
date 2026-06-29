# Celery Worker 自愈守护

Celery 监控页会识别这种状态：

```text
Worker 在线但未领取积压任务，消费连接可能卡住；建议重启该队列 worker。
```

自愈守护会连续多次确认该状态，确认后可发送邮件，并在启用时自动重启对应队列 worker。

守护进程还会修复一种更隐蔽的业务卡死：数据库里的业务任务仍是 `pending`，但原 `celery_task_id` 在 worker `active/reserved/scheduled` 和 Redis 队列中都不可见。此时它会重投同一个业务任务 ID，并在 `result.queue_repair` 记录旧/新 Celery ID。

守护进程还会收口另一类历史脏状态：`publisher` 视频子任务仍是 `pending/processing`，但父编排任务已经 `completed/failed/canceled/blocked_by_captcha`。这种子任务不会被重投，而是自动标记为 `canceled`，并在 `result.orphan_child_cleanup` 记录父任务状态和旧 Celery ID。

## 配置

```env
# 只告警不自动重启：false；自动重启：true
CELERY_AUTO_HEAL_ENABLED=true

# 是否发送邮件通知
CELERY_AUTO_HEAL_NOTIFY_ENABLED=true

# 默认监控知识星球链路，也可以自行配置
CELERY_AUTO_HEAL_SERVICES=zsxq_workflow,zsxq_sync,content_pipeline,zsxq_publish

# 连续几次检测到异常后才重启
CELERY_AUTO_HEAL_CONFIRMATIONS=3

# 同一个服务两次自动重启之间的冷却时间
CELERY_AUTO_HEAL_COOLDOWN_SECONDS=900

# 守护进程轮询间隔
CELERY_AUTO_HEAL_POLL_SECONDS=60

# 自动修复业务表 pending 但 Celery 消息不可见的孤儿任务
CELERY_AUTO_HEAL_REQUEUE_STALE_TASKS=1
```

邮件发送复用工作流通知配置，见 `docs/WORKFLOW_EMAIL_NOTIFICATIONS.md`。

## 启动

页面启动：

1. 打开 `http://localhost:8888/celery-monitor.html`。
2. 页面顶部找到 `Celery Watchdog` 控制区。
3. 点击 `启动守护`。
4. 需要临时检查时点击 `立即检查`。

命令行启动：

```bash
scripts/dev/run-celery-watchdog.sh
```

停止：

```bash
scripts/dev/stop-celery-watchdog.sh
```

## 手动测试

```bash
curl -X POST http://localhost:5001/api/v1/celery/watchdog/run-once
curl http://localhost:5001/api/v1/celery/watchdog/status
```

状态文件：

```text
runtime/celery/watchdog_state.json
```

## 自动 repair 边界

自动 repair 只处理以下 pending 任务：

- `content_pdf_clean`
- `content_audio_clean`
- `zsxq_clean_output_publish`

它必须同时满足：

- 任务超过 `BUSINESS_TASK_QUEUE_REPAIR_GRACE_SECONDS` 仍未推进。
- 原 `celery_task_id` 在 worker 和 Redis 队列中都不可见。
- 重投次数未超过 `BUSINESS_TASK_QUEUE_REPAIR_LIMIT`。
- 目标 worker 未被人工 pause。

默认不会新建业务任务，只会重投同一个 `PublicationTask.id`。

`publisher` 视频任务的边界不同：watchdog 不会全局重投历史 `publisher` 任务，避免意外拉起旧视频；只会自动收口父编排已经终止的 publisher 子任务。
