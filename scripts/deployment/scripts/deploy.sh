#!/bin/bash

# YouTube Analytics 部署脚本
set -e

echo "🚀 开始部署 YouTube Analytics..."

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
cd "$(dirname "$0")/../.."

echo "📂 当前目录: $(pwd)"

# 创建生产环境配置
echo "⚙️ 创建生产环境配置..."
cat > deployment/production.env << EOF
FLASK_ENV=production
FLASK_DEBUG=False
DATABASE_URL=sqlite:///instance/youtube_tracker.db
YOUTUBE_API_KEY=${YOUTUBE_API_KEY}
SECRET_KEY=${SECRET_KEY}
JWT_SECRET_KEY=${JWT_SECRET_KEY:-${SECRET_KEY}}
HOST=0.0.0.0
PORT=5000
LOG_LEVEL=INFO
CORS_ORIGINS=${CORS_ORIGINS:-*}
EOF

# 构建Docker镜像
echo "🐳 构建Docker镜像..."
docker-compose -f deployment/docker/docker-compose.yml build

# 停止现有容器
echo "⏹️ 停止现有服务..."
docker-compose -f deployment/docker/docker-compose.yml down || true

# 启动服务
echo "▶️ 启动服务..."
docker-compose -f deployment/docker/docker-compose.yml up -d

# 等待服务启动
echo "⏳ 等待服务启动..."
sleep 10

# 健康检查
echo "🏥 进行健康检查..."
max_attempts=30
attempt=1

while [ $attempt -le $max_attempts ]; do
    if curl -f http://localhost:5000/health > /dev/null 2>&1; then
        echo "✅ 服务健康检查通过!"
        break
    fi
    
    echo "⏳ 等待服务启动... (尝试 $attempt/$max_attempts)"
    sleep 2
    attempt=$((attempt + 1))
done

if [ $attempt -gt $max_attempts ]; then
    echo "❌ 服务启动失败!"
    docker-compose -f deployment/docker/docker-compose.yml logs
    exit 1
fi

# 初始化数据库
echo "🗄️ 初始化数据库..."
docker-compose -f deployment/docker/docker-compose.yml exec -T youtube-analytics python backend/init_db.py || true

echo "🎉 部署完成!"
echo ""
echo "📝 服务信息:"
echo "  - 前端地址: http://localhost"
echo "  - API地址: http://localhost/api"
echo "  - 健康检查: http://localhost/health"
echo ""
echo "📊 查看日志:"
echo "  docker-compose -f deployment/docker/docker-compose.yml logs -f"
echo ""
echo "🛑 停止服务:"
echo "  docker-compose -f deployment/docker/docker-compose.yml down"