#!/bin/bash

# YouTube Analytics Celery Worker 启动脚本
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "⚙️ 正在启动 Celery Worker (render 队列)..."

# 检查虚拟环境
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "❌ 未找到虚拟环境 venv，请先创建并安装依赖。"
    exit 1
fi

# 设置环境变量
export PYTHONPATH=$PROJECT_ROOT/src/backend
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
export DATABASE_TYPE=${DATABASE_TYPE:-postgresql}
# Redis / Celery broker is loaded by src/backend/celery_config.py from .env.
# Do not set a shell default here, otherwise python-dotenv cannot override it.

cd "$PROJECT_ROOT/src/backend"
celery -A celery_config.celery_app worker --loglevel=info --concurrency=1 -P threads -Q render --hostname=render@%h --without-gossip --without-mingle
