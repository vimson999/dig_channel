#!/bin/bash

# 停止知识星球发布专用 Celery Worker

echo "🔍 正在查找 zsxq_publish Celery Worker..."

PIDS=$(ps aux | grep 'celery' | grep 'zsxq_publish' | grep -v grep | awk '{print $2}')

if [ -z "$PIDS" ]; then
    echo "✅ 没有发现正在运行的 zsxq_publish Worker。"
    exit 0
fi

echo "⚠️ 发现 zsxq_publish Worker: $PIDS"
echo "🚀 正在停止..."
echo $PIDS | xargs kill
sleep 1

LEFTOVER=$(ps aux | grep 'celery' | grep 'zsxq_publish' | grep -v grep | awk '{print $2}')
if [ ! -z "$LEFTOVER" ]; then
    echo "⚠️ 仍有残留进程: $LEFTOVER，正在强制清理..."
    echo $LEFTOVER | xargs kill -9
fi

echo "✨ zsxq_publish Worker 已停止。"
