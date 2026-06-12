#!/bin/bash

# 内容流水线专用 Celery Worker
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "⚙️ 正在启动 Celery Worker (content_pipeline 队列)..."

if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "❌ 未找到虚拟环境 venv，请先创建并安装依赖。"
    exit 1
fi

export PYTHONPATH=$PROJECT_ROOT/src/backend
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
export DATABASE_TYPE=${DATABASE_TYPE:-postgresql}

cd "$PROJECT_ROOT/src/backend"
celery -A celery_config.celery_app worker --loglevel=info --concurrency=1 -P threads -Q content_pipeline --hostname=content_pipeline@%h --without-gossip --without-mingle

