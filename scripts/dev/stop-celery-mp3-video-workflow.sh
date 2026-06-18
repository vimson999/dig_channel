#!/bin/bash

# 停止已同步 MP3 到视频编排专用 Celery Worker

echo "🔍 正在查找 mp3_video_workflow Celery Worker..."

PIDS=$(ps aux | grep 'celery' | grep 'mp3_video_workflow' | grep -v grep | awk '{print $2}')

if [ -z "$PIDS" ]; then
    echo "✅ 没有发现正在运行的 mp3_video_workflow Worker。"
    exit 0
fi

echo "⚠️ 发现 mp3_video_workflow Worker: $PIDS"
echo "🚀 正在停止..."
echo $PIDS | xargs kill
sleep 1

LEFTOVER=$(ps aux | grep 'celery' | grep 'mp3_video_workflow' | grep -v grep | awk '{print $2}')
if [ ! -z "$LEFTOVER" ]; then
    echo "⚠️ 仍有残留进程: $LEFTOVER，正在强制清理..."
    echo $LEFTOVER | xargs kill -9
fi

echo "✨ mp3_video_workflow Worker 已停止。"
