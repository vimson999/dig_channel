#!/bin/bash

# 停止豆包网页朗读捕获专用 Celery Worker

echo "🔍 正在查找 doubao_browser_tts Celery Worker..."

PIDS=$(ps aux | grep 'celery' | grep 'doubao_browser_tts' | grep -v grep | awk '{print $2}')

if [ -z "$PIDS" ]; then
    echo "✅ 没有发现正在运行的 doubao_browser_tts Worker。"
    exit 0
fi

echo "⚠️ 发现 doubao_browser_tts Worker: $PIDS"
echo "🚀 正在停止..."
echo $PIDS | xargs kill
sleep 1

LEFTOVER=$(ps aux | grep 'celery' | grep 'doubao_browser_tts' | grep -v grep | awk '{print $2}')
if [ ! -z "$LEFTOVER" ]; then
    echo "⚠️ 仍有残留进程: $LEFTOVER，正在强制清理..."
    echo $LEFTOVER | xargs kill -9
fi

echo "✨ doubao_browser_tts Worker 已停止。"

RUNTIME_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/runtime"
rm -f "$RUNTIME_DIR"/doubao_browser_tts.pid "$RUNTIME_DIR"/doubao_browser_tts_*_worker_*.pid 2>/dev/null || true
