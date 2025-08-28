#!/bin/bash

# YouTube Analytics 完整开发环境启动脚本
echo "🚀 启动YouTube Analytics开发环境..."
echo "============================================"

# 检查虚拟环境
if [ ! -d "venv" ]; then
    echo "❌ 虚拟环境不存在，请先运行: python -m venv venv"
    exit 1
fi

# 停止可能运行的服务器
echo "🛑 清理已有进程..."
pkill -f "python app.py" 2>/dev/null || true
pkill -f "python server.py" 2>/dev/null || true
sleep 2

# 激活虚拟环境
echo "🔧 激活虚拟环境..."
source venv/bin/activate

# 设置PostgreSQL数据库连接
export DATABASE_TYPE=postgresql
export DATABASE_URL=postgresql://cappa_rw:RWcappaDb!!!2025@101.35.56.140:5432/show_ytb_data

echo "💾 数据库类型: PostgreSQL"
echo "🔗 数据库连接: show_ytb_data"

# 启动后端服务器
echo "🌐 启动后端服务器 (Flask)..."
cd src/backend
export FLASK_ENV=development
export FLASK_DEBUG=1
export PYTHONUNBUFFERED=1
python app.py > ../../logs/backend.log 2>&1 &
BACKEND_PID=$!
cd ../..

# 等待后端启动
echo "⏳ 等待后端服务器启动..."
sleep 3

# 检查后端是否启动成功
if curl -s http://localhost:5001/health > /dev/null; then
    echo "✅ 后端服务器启动成功 (PID: $BACKEND_PID)"
else
    echo "❌ 后端服务器启动失败"
    kill $BACKEND_PID 2>/dev/null || true
    exit 1
fi

# 启动前端服务器
echo "🎨 启动前端服务器..."
cd src/frontend
python server.py > ../../logs/frontend.log 2>&1 &
FRONTEND_PID=$!
cd ../..

# 等待前端启动
sleep 2

# 检查前端是否启动成功
if curl -s http://localhost:8080/ > /dev/null; then
    echo "✅ 前端服务器启动成功 (PID: $FRONTEND_PID)"
else
    echo "❌ 前端服务器启动失败"
    kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true
    exit 1
fi

echo ""
echo "🎉 开发环境启动完成！"
echo "============================================"
echo "📱 前端地址:  http://localhost:8080"
echo "⚙️ 后端API:   http://localhost:5001"
echo "📹 视频管理:  http://localhost:8080/videos.html"
echo ""
echo "📋 服务状态:"
echo "   后端进程: $BACKEND_PID"
echo "   前端进程: $FRONTEND_PID"
echo ""
echo "📁 日志文件:"
echo "   后端日志: logs/backend.log"
echo "   前端日志: logs/frontend.log"
echo ""
echo "💡 提示:"
echo "   - 修改后端代码会自动重启"
echo "   - 前端静态文件即时生效"
echo "   - 按 Ctrl+C 或运行 ./stop-dev.sh 停止服务"
echo ""

# 保存PID到文件
echo $BACKEND_PID > .backend.pid
echo $FRONTEND_PID > .frontend.pid

# 监控服务状态
echo "🔍 监控服务状态... (按 Ctrl+C 停止)"
trap "echo ''; echo '🛑 停止开发服务器...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; rm -f .backend.pid .frontend.pid; echo '✅ 开发环境已停止'; exit 0" INT

# 保持脚本运行
while true; do
    sleep 5
    # 检查服务是否还在运行
    if ! kill -0 $BACKEND_PID 2>/dev/null; then
        echo "⚠️ 后端服务器意外停止"
        break
    fi
    if ! kill -0 $FRONTEND_PID 2>/dev/null; then
        echo "⚠️ 前端服务器意外停止"
        break
    fi
done