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
export PYTHONUNBUFFERED=1
export DATABASE_TYPE=${DATABASE_TYPE:-postgresql}

cd "$PROJECT_ROOT/src/backend"
python app.py
