#!/bin/bash

# 停止播客视觉化专用 Celery Worker

echo "🔍 正在查找 podcast_visualizer Celery Worker..."

PIDS=$(ps aux | grep 'celery' | grep 'podcast_visualizer' | grep -v grep | awk '{print $2}')

if [ -z "$PIDS" ]; then
    echo "✅ 没有发现正在运行的 podcast_visualizer Worker。"
    exit 0
fi

echo "⚠️ 发现 podcast_visualizer Worker: $PIDS"
echo "🚀 正在停止..."
echo $PIDS | xargs kill
sleep 1

LEFTOVER=$(ps aux | grep 'celery' | grep 'podcast_visualizer' | grep -v grep | awk '{print $2}')
if [ ! -z "$LEFTOVER" ]; then
    echo "⚠️ 仍有残留进程: $LEFTOVER，正在强制清理..."
    echo $LEFTOVER | xargs kill -9
fi

echo "✨ podcast_visualizer Worker 已停止。"
