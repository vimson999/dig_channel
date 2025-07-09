#!/bin/bash

# YouTube Analytics 开发服务器启动脚本
echo "🚀 启动YouTube Analytics开发服务器..."

# 检查虚拟环境
if [ ! -d "venv" ]; then
    echo "❌ 虚拟环境不存在，请先运行: python -m venv venv"
    exit 1
fi

# 激活虚拟环境
source venv/bin/activate

# 进入后端目录
cd src/backend

# 设置Flask开发环境变量
export FLASK_ENV=development
export FLASK_DEBUG=1
export PYTHONUNBUFFERED=1

# 启动开发服务器
echo "📁 工作目录: $(pwd)"
echo "🌐 服务地址: http://localhost:5001"
echo "🔥 自动重载: 启用"
echo "📝 调试模式: 启用"
echo ""
echo "💡 提示: 修改代码后服务器会自动重启"
echo "   按 Ctrl+C 停止服务器"
echo ""

python app.py