#!/bin/bash

# YouTube Analytics 后端启动脚本
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

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
export DATABASE_TYPE=postgresql
export DATABASE_URL=${DATABASE_URL:-"postgresql://cappa_rw:RWcappaDb!!!2025@101.35.56.140:5432/show_ytb_data"}

cd src/backend
python app.py
