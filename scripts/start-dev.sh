#!/bin/bash

# YouTube Analytics 开发服务器启动脚本
echo "🚀 启动YouTube Analytics开发服务器..."
echo "=========================================="

# 检查虚拟环境
if [ ! -d "venv" ]; then
    echo "❌ 虚拟环境不存在，请先运行: python -m venv venv"
    exit 1
fi

# 停止可能运行的服务器
echo "🛑 停止已有服务器进程..."
pkill -f "python app.py" 2>/dev/null || true
sleep 2

# 激活虚拟环境
echo "🔧 激活虚拟环境..."
source venv/bin/activate

# 进入后端目录
cd src/backend

# 设置Flask开发环境变量
export FLASK_ENV=development
export FLASK_DEBUG=1
export PYTHONUNBUFFERED=1

# 数据库连接从 .env 或当前环境变量读取，避免在脚本中写死凭据
export DATABASE_TYPE=${DATABASE_TYPE:-postgresql}

echo "💾 数据库类型: PostgreSQL"
echo "🔗 数据库连接: show_ytb_data"

echo ""
echo "📁 工作目录: $(pwd)"
echo "🌐 服务地址: http://localhost:5001"
echo "🔥 自动重载: 启用 (Flask Reloader)"
echo "📝 调试模式: 启用"
echo ""
echo "💡 提示: 修改Python代码后Flask会自动重启"
echo "   按 Ctrl+C 停止服务器"
echo ""
echo "📋 服务器日志:"
echo "=========================================="

# 启动开发服务器
python app.py
