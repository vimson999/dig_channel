#!/bin/bash

# YouTube Analytics 传统部署脚本
set -e

echo "🚀 开始传统方式部署 YouTube Analytics..."

# 检查必要的环境变量
if [ -z "$YOUTUBE_API_KEY" ]; then
    echo "❌ 错误: 请设置 YOUTUBE_API_KEY 环境变量"
    exit 1
fi

if [ -z "$SECRET_KEY" ]; then
    echo "❌ 错误: 请设置 SECRET_KEY 环境变量"
    exit 1
fi

# 进入项目根目录
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "📂 项目目录: $PROJECT_ROOT"

# 检查Python版本
echo "🐍 检查Python版本..."
python_version=$(python3 --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
required_version="3.11"

if [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" != "$required_version" ]; then
    echo "❌ 错误: 需要Python 3.11或更高版本，当前版本: $python_version"
    exit 1
fi

echo "✅ Python版本检查通过: $python_version"

# 创建虚拟环境
echo "📦 创建Python虚拟环境..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

# 激活虚拟环境
echo "🔄 激活虚拟环境..."
source venv/bin/activate

# 安装依赖
echo "📚 安装Python依赖..."
pip install --upgrade pip
pip install -r requirements/production.txt

# 创建生产环境配置
echo "⚙️ 创建生产环境配置..."
cat > .env << EOF
# 生产环境配置
FLASK_ENV=production
FLASK_DEBUG=False

# 数据库配置
DATABASE_URL=sqlite:///instance/youtube_tracker.db

# YouTube API配置
YOUTUBE_API_KEY=${YOUTUBE_API_KEY}

# 安全配置
SECRET_KEY=${SECRET_KEY}
JWT_SECRET_KEY=${JWT_SECRET_KEY:-${SECRET_KEY}}

# 服务器配置
HOST=127.0.0.1
PORT=5000

# 日志配置
LOG_LEVEL=INFO

# CORS配置
CORS_ORIGINS=${CORS_ORIGINS:-*}

# 限流配置 (如果有Redis)
RATELIMIT_STORAGE_URL=${REDIS_URL:-memory://}
EOF

# 创建必要目录
echo "📁 创建必要目录..."
mkdir -p logs
mkdir -p instance
mkdir -p static/uploads

# 初始化数据库
echo "🗄️ 初始化数据库..."
cd src/backend
python init_db.py

# 回到项目根目录
cd "$PROJECT_ROOT"

# 创建systemd服务文件
echo "🔧 创建systemd服务..."
sudo tee /etc/systemd/system/youtube-analytics.service > /dev/null << EOF
[Unit]
Description=YouTube Analytics Web Application
After=network.target

[Service]
Type=exec
User=$USER
Group=$USER
WorkingDirectory=$PROJECT_ROOT
Environment=PATH=$PROJECT_ROOT/venv/bin
EnvironmentFile=$PROJECT_ROOT/.env
ExecStart=$PROJECT_ROOT/venv/bin/gunicorn --bind 127.0.0.1:5000 --workers 4 --timeout 120 --chdir $PROJECT_ROOT/src/backend app:create_app()
ExecReload=/bin/kill -s HUP \$MAINPID
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 重新加载systemd
echo "🔄 重新加载systemd..."
sudo systemctl daemon-reload

# 启用并启动服务
echo "▶️ 启动YouTube Analytics服务..."
sudo systemctl enable youtube-analytics
sudo systemctl start youtube-analytics

# 等待服务启动
echo "⏳ 等待服务启动..."
sleep 5

# 检查服务状态
if sudo systemctl is-active --quiet youtube-analytics; then
    echo "✅ YouTube Analytics服务启动成功!"
else
    echo "❌ YouTube Analytics服务启动失败!"
    sudo systemctl status youtube-analytics
    exit 1
fi

# 健康检查
echo "🏥 进行健康检查..."
max_attempts=30
attempt=1

while [ $attempt -le $max_attempts ]; do
    if curl -f http://127.0.0.1:5000/health > /dev/null 2>&1; then
        echo "✅ 服务健康检查通过!"
        break
    fi
    
    echo "⏳ 等待服务启动... (尝试 $attempt/$max_attempts)"
    sleep 2
    attempt=$((attempt + 1))
done

if [ $attempt -gt $max_attempts ]; then
    echo "❌ 服务健康检查失败!"
    sudo systemctl status youtube-analytics
    sudo journalctl -u youtube-analytics --no-pager
    exit 1
fi

echo "🎉 部署完成!"
echo ""
echo "📝 服务信息:"
echo "  - 后端API: http://127.0.0.1:5000"
echo "  - 健康检查: http://127.0.0.1:5000/health"
echo "  - 日志位置: $PROJECT_ROOT/logs/"
echo ""
echo "🔧 管理命令:"
echo "  启动服务: sudo systemctl start youtube-analytics"
echo "  停止服务: sudo systemctl stop youtube-analytics"
echo "  重启服务: sudo systemctl restart youtube-analytics"
echo "  查看状态: sudo systemctl status youtube-analytics"
echo "  查看日志: sudo journalctl -u youtube-analytics -f"
echo ""
echo "⚠️  下一步: 配置Nginx反向代理前端服务"