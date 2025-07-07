#!/bin/bash
# YouTube数据抓取调度器启动脚本

echo "🚀 启动YouTube数据抓取调度器"
echo "================================"

# 检查虚拟环境
if [ ! -d "venv" ]; then
    echo "❌ 虚拟环境不存在，请先运行 python -m venv venv"
    exit 1
fi

# 激活虚拟环境
source venv/bin/activate

# 检查依赖包
echo "📦 检查依赖包..."
pip install -q schedule

# 切换到后端目录
cd src/backend

# 检查配置
if [ ! -f ".env" ]; then
    echo "⚠️  .env文件不存在，请确保YouTube API密钥已配置"
fi

# 启动调度器
echo "🔧 启动调度器..."
python run_scheduler.py start