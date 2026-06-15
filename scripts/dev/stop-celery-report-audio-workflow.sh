#!/bin/bash

# 停止研报 PDF 到豆包浏览器语音编排专用 Celery Worker

echo "🔍 正在查找 report_audio_workflow Celery Worker..."

PIDS=$(ps aux | grep 'celery' | grep 'report_audio_workflow' | grep -v grep | awk '{print $2}')

if [ -z "$PIDS" ]; then
    echo "✅ 没有发现正在运行的 report_audio_workflow Worker。"
    exit 0
fi

echo "⚠️ 发现 report_audio_workflow Worker: $PIDS"
echo "🚀 正在停止..."
echo $PIDS | xargs kill
sleep 1

LEFTOVER=$(ps aux | grep 'celery' | grep 'report_audio_workflow' | grep -v grep | awk '{print $2}')
if [ ! -z "$LEFTOVER" ]; then
    echo "⚠️ 仍有残留进程: $LEFTOVER，正在强制清理..."
    echo $LEFTOVER | xargs kill -9
fi

echo "✨ report_audio_workflow Worker 已停止。"
