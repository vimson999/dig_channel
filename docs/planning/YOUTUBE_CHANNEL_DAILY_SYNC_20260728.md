# 首页 YouTube 频道每日同步

## 目标

- 复用首页“频道管理 → 同步”的全部启用频道同步能力。
- 每天 00:03（Asia/Shanghai）执行一次。
- 启动、完成、部分失败或整体失败均进入统一工作流邮件通知链路。
- 在 Celery Monitor 中可见、可启停，并由后端 bootstrap 自动补齐进程。

## 配置

配置保存在 `system_configs`：

- key：`youtube_channel_sync.schedule`
- type：`youtube_channel_sync`
- scope：`global`
- 默认值：

```json
{
  "enabled": true,
  "daily_at": "00:03",
  "timezone": "Asia/Shanghai",
  "notify_milestones": true,
  "use_parallel": true,
  "max_workers": 5,
  "active_only": true
}
```

## 成功经验复用

- 参考实现：B站充电人数每日采集计划。
- 复用能力：
  - 独立 scheduler 进程、PID 文件和独立日志。
  - 数据库配置校验与安全默认值。
  - `workflow_notification_service` 的启动/完成/失败邮件事件。
  - backend bootstrap 默认自启动。
  - Celery Monitor 的状态、日志、启动、停止和重启入口。
  - scheduler 角色连接池与每个工作单元的 Session 清理。
- 未复用项：
  - 旧 YouTube scheduler 的每天三次与每小时补抓规则；它不符合“每天 00:03
    执行一次”的业务要求，也没有真实邮件通知。

## 验收

- 数据库配置为 00:03、Asia/Shanghai、5 线程、只同步启用频道。
- scheduler 新进程持续在线，日志显示准确规则。
- Celery Monitor 显示相同规则。
- 启动/完成/失败事件使用统一邮件队列。
- YouTube 远端请求期间不持有数据库事务。
