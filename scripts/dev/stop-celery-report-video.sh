#!/bin/bash

# 停止研报导图视频工厂专用 Celery Worker

echo "🔍 正在查找 report_video Celery Worker..."

PIDS=$(ps aux | grep 'celery' | grep 'report_video' | grep -v grep | awk '{print $2}')

if [ -z "$PIDS" ]; then
    echo "✅ 没有发现正在运行的 report_video Worker。"
    exit 0
fi

echo "⚠️ 发现 report_video Worker: $PIDS"
echo "🚀 正在停止..."
echo $PIDS | xargs kill
sleep 1

LEFTOVER=$(ps aux | grep 'celery' | grep 'report_video' | grep -v grep | awk '{print $2}')
if [ ! -z "$LEFTOVER" ]; then
    echo "⚠️ 仍有残留进程: $LEFTOVER，正在强制清理..."
    echo $LEFTOVER | xargs kill -9
fi

echo "✨ report_video Worker 已停止。"
