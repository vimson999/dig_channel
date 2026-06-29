#!/bin/bash

# 停止研报 PDF Gem 解析专用 Celery Worker

echo "🔍 正在查找 report_pdf_gemini Celery Worker..."

PIDS=$(ps aux | grep 'celery' | grep 'report_pdf_gemini' | grep -v grep | awk '{print $2}')

if [ -z "$PIDS" ]; then
    echo "✅ 没有发现正在运行的 report_pdf_gemini Worker。"
    exit 0
fi

echo "⚠️ 发现 report_pdf_gemini Worker: $PIDS"
echo "🚀 正在停止..."
echo $PIDS | xargs kill
sleep 1

LEFTOVER=$(ps aux | grep 'celery' | grep 'report_pdf_gemini' | grep -v grep | awk '{print $2}')
if [ ! -z "$LEFTOVER" ]; then
    echo "⚠️ 仍有残留进程: $LEFTOVER，正在强制清理..."
    echo $LEFTOVER | xargs kill -9
fi

echo "✨ report_pdf_gemini Worker 已停止。"

RUNTIME_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/runtime"
rm -f "$RUNTIME_DIR"/report_pdf_gemini.pid "$RUNTIME_DIR"/report_pdf_gemini_worker_*.pid 2>/dev/null || true
