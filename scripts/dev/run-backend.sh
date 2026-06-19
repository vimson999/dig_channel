#!/bin/bash

# YouTube Analytics 后端启动脚本
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "🚀 正在启动后端服务器 (Flask)..."

# 检查虚拟环境
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "❌ 未找到虚拟环境 venv，请先创建并安装依赖。"
    exit 1
fi

# 设置环境变量
export FLASK_ENV=development
export FLASK_DEBUG=1
export FLASK_USE_RELOADER=${FLASK_USE_RELOADER:-0}
export PYTHONUNBUFFERED=1
export DATABASE_TYPE=${DATABASE_TYPE:-postgresql}
export BACKEND_PROCESS_BOOTSTRAP_ENABLED=${BACKEND_PROCESS_BOOTSTRAP_ENABLED:-1}
export BACKEND_PROCESS_BOOTSTRAP_MODE=${BACKEND_PROCESS_BOOTSTRAP_MODE:-ensure}
export BACKEND_PROCESS_BOOTSTRAP_CELERY_SERVICES=${BACKEND_PROCESS_BOOTSTRAP_CELERY_SERVICES:-all}
export BACKEND_PROCESS_BOOTSTRAP_SCHEDULERS=${BACKEND_PROCESS_BOOTSTRAP_SCHEDULERS:-zsxq_auto,zsxq_auto_publish,content_retention,report_video_asset,report_video_auto}
export BACKEND_PROCESS_BOOTSTRAP_WATCHDOG=${BACKEND_PROCESS_BOOTSTRAP_WATCHDOG:-1}

cd "$PROJECT_ROOT/src/backend"
python app.py
