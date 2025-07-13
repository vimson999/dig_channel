#!/bin/bash
# YouTube Analytics 生产环境部署脚本
# 目标服务器: 10.0.12.13 (server 2)

set -e

# 配置变量
APP_DIR="/code/bot_app/youtube-analytics"
SERVICE_USER="ubuntu"
LOGS_DIR="/logs/youtube_analytics"
VENV_PATH="$APP_DIR/venv"

echo "🚀 开始部署 YouTube Analytics 到生产环境..."
echo "📍 目标服务器: 10.0.12.13"

# 1. 创建应用目录结构
echo "📁 创建应用目录结构..."
sudo mkdir -p $APP_DIR
sudo mkdir -p $LOGS_DIR
sudo chown -R $SERVICE_USER:$SERVICE_USER $APP_DIR
sudo chown -R $SERVICE_USER:$SERVICE_USER $LOGS_DIR

# 2. 复制应用文件
echo "📦 复制应用文件..."
sudo cp -r . $APP_DIR/
sudo chown -R $SERVICE_USER:$SERVICE_USER $APP_DIR

# 3. 设置Python虚拟环境和清华镜像
echo "🐍 设置Python虚拟环境和清华镜像..."
cd $APP_DIR
sudo -u $SERVICE_USER python3 -m venv $VENV_PATH
sudo -u $SERVICE_USER $VENV_PATH/bin/pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
sudo -u $SERVICE_USER $VENV_PATH/bin/pip install --upgrade pip
sudo -u $SERVICE_USER $VENV_PATH/bin/pip install -r requirements/production.txt

# 4. 配置数据库
echo "🗄️ 配置数据库..."
sudo mkdir -p $APP_DIR/instance
sudo chown -R $SERVICE_USER:$SERVICE_USER $APP_DIR/instance
echo "SQLite数据库将创建在: $APP_DIR/instance/youtube_tracker.db"

# 5. 安装 systemd 服务文件
echo "⚙️ 安装系统服务..."
sudo cp deployment/systemd/youtube-channel-sync.service /etc/systemd/system/
sudo cp deployment/systemd/youtube-channel-sync.timer /etc/systemd/system/
sudo cp deployment/systemd/youtube-video-sync.service /etc/systemd/system/
sudo cp deployment/systemd/youtube-video-sync.timer /etc/systemd/system/

# 6. 安装 supervisor 配置
echo "📋 安装 supervisor 配置..."
sudo cp deployment/supervisor/youtube_analytics.conf /etc/supervisor/conf.d/
sudo supervisorctl reread
sudo supervisorctl update

# 7. 配置 Nginx 反向代理
echo "🌐 配置 Nginx..."
sudo bash deployment/scripts/setup-nginx.sh $APP_DIR

# 8. 配置 Promtail 日志收集
echo "📊 配置日志收集..."
sudo cp deployment/promtail/youtube_analytics.yml /etc/promtail/config_youtube_analytics.yml

# 9. 重新加载 systemd
echo "🔄 重新加载系统配置..."
sudo systemctl daemon-reload

# 10. 启用定时任务
echo "⏰ 启用定时任务..."
sudo systemctl enable youtube-channel-sync.timer
sudo systemctl enable youtube-video-sync.timer
sudo systemctl start youtube-channel-sync.timer
sudo systemctl start youtube-video-sync.timer

# 11. 启动 API 服务
echo "🎬 启动 API 服务..."
sudo supervisorctl start youtube_analytics_api
sudo supervisorctl start youtube_scheduler

# 12. 检查服务状态
echo "📊 检查服务状态..."
echo ""
echo "=== Timer 状态 ==="
sudo systemctl list-timers youtube-*

echo ""
echo "=== Supervisor 状态 ==="
sudo supervisorctl status youtube_*

echo ""
echo "=== 最近日志 ==="
echo "Channel Sync Timer:"
sudo systemctl status youtube-channel-sync.timer --no-pager -l

echo ""
echo "Video Sync Timer:"
sudo systemctl status youtube-video-sync.timer --no-pager -l

echo ""
echo "✅ 部署完成!"
echo ""
echo "🔧 管理命令:"
echo "  查看定时器: sudo systemctl list-timers youtube-*"
echo "  查看API状态: sudo supervisorctl status youtube_*"
echo "  手动同步频道: sudo systemctl start youtube-channel-sync.service"
echo "  手动同步视频: sudo systemctl start youtube-video-sync.service"
echo "  查看同步日志: tail -f $LOGS_DIR/channel_sync.log"
echo "  查看API日志: tail -f /var/log/supervisor/youtube_analytics_api_stdout.log"
echo "  重启API服务: sudo supervisorctl restart youtube_analytics_api"
echo ""
echo "⚠️  环境变量配置:"
echo "  请编辑以下文件设置正确的API密钥:"
echo "  - /etc/systemd/system/youtube-channel-sync.service"
echo "  - /etc/systemd/system/youtube-video-sync.service"
echo "  - /etc/supervisor/conf.d/youtube_analytics.conf"
echo ""
echo "📈 监控地址:"
echo "  Grafana: http://10.0.16.19:3000"
echo "  日志查询: job=youtube_analytics_*"