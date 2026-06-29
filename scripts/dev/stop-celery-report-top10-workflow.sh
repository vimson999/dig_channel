#!/bin/bash

# 停止今日研报 Top10 总编辑编排 Celery Worker
echo "🔍 正在查找 report_top10_workflow Celery Worker..."

PIDS=$(ps aux | grep 'celery' | grep 'report_top10_workflow' | grep -v grep | awk '{print $2}')

if [ -z "$PIDS" ]; then
    echo "✅ 没有发现正在运行的 report_top10_workflow Worker。"
    exit 0
fi

echo "⚠️ 发现 report_top10_workflow Worker: $PIDS"
kill $PIDS
sleep 2

LEFTOVER=$(ps aux | grep 'celery' | grep 'report_top10_workflow' | grep -v grep | awk '{print $2}')
if [ -n "$LEFTOVER" ]; then
    echo "⚠️ 仍有残留进程，执行强制停止: $LEFTOVER"
    kill -9 $LEFTOVER
fi

echo "✨ report_top10_workflow Worker 已停止。"
