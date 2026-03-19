#!/bin/bash

# 强力清理所有 Celery 进程的脚本

echo "🔍 正在查找残留的 Celery 进程..."

# 获取所有包含 celery 关键字的 PID
PIDS=$(ps aux | grep 'celery' | grep -v grep | awk '{print $2}')

if [ -z "$PIDS" ]; then
    echo "✅ 没有发现正在运行的 Celery 进程。"
else
    echo "⚠️ 发现以下进程: $PIDS"
    echo "🚀 正在强制杀掉 (kill -9)..."
    echo $PIDS | xargs kill -9
    echo "✅ 清理完成。"
fi

# 检查是否有残留的 python 进程在执行 publisher 任务（可选）
echo "🔍 检查是否有残留的任务子进程..."
STRAY_PIDS=$(ps aux | grep 'tasks/publisher.py' | grep -v grep | awk '{print $2}')
if [ ! -z "$STRAY_PIDS" ]; then
    echo "⚠️ 发现残留子进程: $STRAY_PIDS，正在清理..."
    echo $STRAY_PIDS | xargs kill -9
fi

echo "✨ Celery 环境已重置。"
