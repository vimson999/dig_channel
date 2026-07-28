# 视频管理 YouTube 每日同步

## 目标

- 复用“视频管理 → 同步”的优化视频同步能力。
- 每天 01:00（Asia/Shanghai）同步最近 3 天的视频。
- 覆盖系统内全部启用频道。
- 启动、完成、部分失败和整体失败进入统一工作流邮件链路。
- 在 Celery Monitor 可见、可启停，并由后端 bootstrap 自动补齐。

## 数据库配置

- key：`youtube_video_sync.schedule`
- type：`youtube_video_sync`
- scope：`global`

```json
{
  "enabled": true,
  "daily_at": "01:00",
  "timezone": "Asia/Shanghai",
  "days_back": 3,
  "notify_milestones": true,
  "max_workers": 5
}
```

## 成功经验复用

- 独立 scheduler、PID、日志和进程控制沿用 B站充电及频道同步计划。
- 调度、时区、参数和通知开关统一存入 `system_configs`。
- 业务执行复用 `OptimizedVideoSyncService`，保留页面的缓存、批量写入和并发安全上限。
- 远端 YouTube 请求前将频道数据转成标量并释放 Session。
- backend bootstrap、Celery Monitor、停止脚本和运行时 schema 门禁同步注册。

## 验收

- 配置值为 01:00、3 天、Asia/Shanghai。
- 日志明确记录下一次执行时间。
- Monitor 能识别独立进程。
- 邮件事件覆盖 `workflow_started`、`sync_completed`、`sync_failed`。
- 远端视频抓取期间没有超过 60 秒的 `idle in transaction`。
