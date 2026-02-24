#!/bin/bash

# YouTube Analytics 调度器启动脚本
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "⏰ 正在启动任务调度器..."

# 检查虚拟环境
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "❌ 未找到虚拟环境 venv，请先创建并安装依赖。"
    exit 1
fi

export PYTHONPATH=$SCRIPT_DIR/src/backend
export DATABASE_TYPE=postgresql
export DATABASE_URL=${DATABASE_URL:-"postgresql://cappa_rw:RWcappaDb!!!2025@101.35.56.140:5432/show_ytb_data"}

cd src/backend
python run_scheduler.py
