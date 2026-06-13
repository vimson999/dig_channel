#!/bin/bash

# Celery worker 自愈守护进程
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "🩺 正在启动 Celery Watchdog..."

if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "❌ 未找到虚拟环境 venv，请先创建并安装依赖。"
    exit 1
fi

mkdir -p "$PROJECT_ROOT/logs/celery" "$PROJECT_ROOT/runtime/celery"

export PYTHONPATH=$PROJECT_ROOT/src/backend
export DATABASE_TYPE=${DATABASE_TYPE:-postgresql}

python "$PROJECT_ROOT/scripts/dev/celery_watchdog_loop.py"
