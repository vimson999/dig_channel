# Celery Worker 自愈守护

Celery 监控页会识别这种状态：

```text
Worker 在线但未领取积压任务，消费连接可能卡住；建议重启该队列 worker。
```

自愈守护会连续多次确认该状态，确认后可发送邮件，并在启用时自动重启对应队列 worker。

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
