#!/bin/bash

# YouTube Analytics 开发环境停止脚本
echo "🛑 停止YouTube Analytics开发环境..."

# 从PID文件停止服务
if [ -f ".backend.pid" ]; then
    BACKEND_PID=$(cat .backend.pid)
    if kill -0 $BACKEND_PID 2>/dev/null; then
        echo "🔴 停止后端服务器 (PID: $BACKEND_PID)..."
        kill $BACKEND_PID
    fi
    rm -f .backend.pid
fi

if [ -f ".frontend.pid" ]; then
    FRONTEND_PID=$(cat .frontend.pid)
    if kill -0 $FRONTEND_PID 2>/dev/null; then
        echo "🔴 停止前端服务器 (PID: $FRONTEND_PID)..."
        kill $FRONTEND_PID
    fi
    rm -f .frontend.pid
fi

# 强制清理所有相关进程
echo "🧹 清理残留进程..."
pkill -f "python app.py" 2>/dev/null || true
pkill -f "python server.py" 2>/dev/null || true

sleep 2

echo "✅ 开发环境已停止"