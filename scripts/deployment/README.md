# YouTube Analytics 生产环境部署文档

## 部署架构

### 目标环境
- **服务器**: 10.0.12.13 (server 2)
- **用户**: ubuntu
- **应用目录**: `/code/bot_app/youtube-analytics`
- **日志目录**: `/logs/youtube_analytics`
- **数据库**: SQLite @ `/code/bot_app/youtube-analytics/instance/youtube_tracker.db`
- **监控**: Loki @ 10.0.16.19:3100, Grafana @ 10.0.16.19:3000

## 调度策略

### 频道同步 (Channel Sync)
- **频率**: 每天1次
- **时间**: 凌晨2:00
- **实现**: systemd timer + oneshot service
- **日志**: `/logs/youtube_analytics/channel_sync.log`

### 视频同步 (Video Sync)  
- **频率**: 每天8次
- **时间**: 0:00, 3:00, 6:00, 9:00, 12:00, 15:00, 18:00, 21:00
- **实现**: systemd timer + oneshot service
- **日志**: `/logs/youtube_analytics/video_sync.log`

### API 服务
- **端口**: 8084
- **进程**: 2个 uvicorn worker
- **管理**: supervisor
- **日志**: `/var/log/supervisor/youtube_analytics_api_*.log`

## 快速部署

### 1. 执行部署脚本
```bash
# 在项目根目录运行
sudo ./deployment/scripts/deploy-production.sh
```

### 2. 配置环境变量
部署脚本完成后，编辑以下文件设置API密钥：

```bash
# systemd 服务文件
sudo vim /etc/systemd/system/youtube-channel-sync.service
sudo vim /etc/systemd/system/youtube-video-sync.service

# supervisor 配置
sudo vim /etc/supervisor/conf.d/youtube_analytics.conf

# 重新加载配置
sudo systemctl daemon-reload
sudo supervisorctl reread && sudo supervisorctl update
```

### 3. 验证部署
```bash
# 检查定时器状态
sudo systemctl list-timers youtube-*

# 检查API服务状态
sudo supervisorctl status youtube_*

# 手动测试同步
sudo systemctl start youtube-channel-sync.service
```

## 管理命令

### 定时器管理
```bash
# 查看定时器状态
sudo systemctl list-timers youtube-*

# 手动执行同步
sudo systemctl start youtube-channel-sync.service
sudo systemctl start youtube-video-sync.service
```

### API服务管理
```bash
# 查看服务状态
sudo supervisorctl status youtube_*

# 重启服务
sudo supervisorctl restart youtube_analytics_api
```

### 日志查看
```bash
# 同步任务日志
tail -f /logs/youtube_analytics/channel_sync.log
tail -f /logs/youtube_analytics/video_sync.log

# API服务日志
tail -f /var/log/supervisor/youtube_analytics_api_stdout.log
```

## 监控集成

### Grafana查询
- **访问地址**: http://10.0.16.19:3000
- **日志查询**: `{service="youtube_analytics"}`
- **错误查询**: `{service="youtube_analytics"} |= "ERROR"`

## 文件结构

```
deployment/
├── supervisor/youtube_analytics.conf     # API服务配置
├── systemd/                              # 定时任务配置
│   ├── youtube-channel-sync.service
│   ├── youtube-channel-sync.timer
│   ├── youtube-video-sync.service
│   └── youtube-video-sync.timer
├── promtail/youtube_analytics.yml        # 日志收集配置
└── scripts/deploy-production.sh          # 部署脚本
```