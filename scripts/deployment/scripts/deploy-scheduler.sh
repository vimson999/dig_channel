#!/bin/bash
# YouTube Analytics 调度器部署脚本

set -e

# 配置变量
APP_DIR="/var/www/youtube-analytics"
SERVICE_NAME="youtube-scheduler"
USER="www-data"

echo "🚀 开始部署 YouTube Analytics 调度器..."

# 1. 复制服务文件
echo "📋 安装 systemd 服务..."
sudo cp deployment/systemd/youtube-scheduler.service /etc/systemd/system/
sudo systemctl daemon-reload

# 2. 创建应用目录
echo "📁 创建应用目录..."
sudo mkdir -p $APP_DIR/logs
sudo chown -R $USER:$USER $APP_DIR

# 3. 复制应用文件
echo "📦 复制应用文件..."
sudo cp -r . $APP_DIR/
sudo chown -R $USER:$USER $APP_DIR

# 4. 安装Python依赖
echo "🐍 安装Python依赖..."
cd $APP_DIR
sudo -u $USER python3 -m venv venv
sudo -u $USER ./venv/bin/pip install -r requirements/production.txt

# 5. 配置环境变量
echo "⚙️ 配置环境变量..."
echo "请手动编辑 /etc/systemd/system/youtube-scheduler.service"
echo "设置正确的 YOUTUBE_API_KEY"

# 6. 启动服务
echo "🎬 启动调度器服务..."
sudo systemctl enable $SERVICE_NAME
sudo systemctl start $SERVICE_NAME

# 7. 检查状态
echo "📊 检查服务状态..."
sudo systemctl status $SERVICE_NAME

echo "✅ 部署完成!"
echo ""
echo "管理命令:"
echo "  查看状态: sudo systemctl status $SERVICE_NAME"
echo "  查看日志: sudo journalctl -u $SERVICE_NAME -f"
echo "  重启服务: sudo systemctl restart $SERVICE_NAME"
echo "  停止服务: sudo systemctl stop $SERVICE_NAME"