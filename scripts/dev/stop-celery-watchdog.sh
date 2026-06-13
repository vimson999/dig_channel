#!/bin/bash

# 停止 Celery worker 自愈守护进程

echo "🔍 正在查找 Celery Watchdog..."

PIDS=$(ps aux | grep 'celery_watchdog_loop.py' | grep -v grep | awk '{print $2}')

if [ -z "$PIDS" ]; then
    echo "✅ 没有发现正在运行的 Celery Watchdog。"
    exit 0
fi

echo "⚠️ 发现 Celery Watchdog: $PIDS"
echo "🚀 正在停止..."
echo $PIDS | xargs kill
sleep 1

LEFTOVER=$(ps aux | grep 'celery_watchdog_loop.py' | grep -v grep | awk '{print $2}')
if [ ! -z "$LEFTOVER" ]; then
    echo "⚠️ 仍有残留进程: $LEFTOVER，正在强制清理..."
    echo $LEFTOVER | xargs kill -9
fi

echo "✨ Celery Watchdog 已停止。"
