#!/bin/bash

# YouTube Analytics Celery Worker 启动脚本
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "⚙️ 正在启动 Celery Worker..."

# 检查虚拟环境
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "❌ 未找到虚拟环境 venv，请先创建并安装依赖。"
    exit 1
fi

# 设置环境变量
export PYTHONPATH=$SCRIPT_DIR/src/backend
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
export DATABASE_TYPE=postgresql
export DATABASE_URL=${DATABASE_URL:-"postgresql://cappa_rw:RWcappaDb!!!2025@101.35.56.140:5432/show_ytb_data"}
export REDIS_URL=${REDIS_URL:-"redis://localhost:6379/0"}

cd src/backend
celery -A celery_config.celery_app worker --loglevel=info --concurrency=1 -P threads
