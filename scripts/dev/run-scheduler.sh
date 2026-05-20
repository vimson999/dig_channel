#!/bin/bash

# YouTube Analytics 调度器启动脚本
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "⏰ 正在启动任务调度器..."

# 检查虚拟环境
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "❌ 未找到虚拟环境 venv，请先创建并安装依赖。"
    exit 1
fi

export PYTHONPATH=$PROJECT_ROOT/src/backend
export DATABASE_TYPE=${DATABASE_TYPE:-postgresql}

cd "$PROJECT_ROOT/src/backend"
python run_scheduler.py
