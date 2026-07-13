#!/bin/bash

# 强力清理所有 Celery 进程的脚本
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CELERY_BIN="$PROJECT_ROOT/venv/bin/celery"

echo "🔍 正在查找残留的 Celery 进程..."

# Only match the real project Celery executable. Script names and controller
# shells may also contain the word "celery" and must never be terminated.
PIDS="$(ps -axo pid=,command= | awk -v celery="$CELERY_BIN" '
  index($0, celery) && $0 ~ / worker( |$)/ { print $1 }
')"

if [ -z "$PIDS" ]; then
    echo "✅ 没有发现正在运行的 Celery 进程。"
else
    echo "⚠️ 发现以下进程: $PIDS"
    echo "🚀 正在强制杀掉 (kill -9)..."
    echo "$PIDS" | xargs kill -9 2>/dev/null || true
    echo "✅ 清理完成。"
fi

# 检查是否有残留的 python 进程在执行 publisher 任务（可选）
echo "🔍 检查是否有残留的任务子进程..."
STRAY_PIDS="$(ps -axo pid=,command= | awk -v project="$PROJECT_ROOT" '
  index($0, project "/src/backend/tasks/publisher.py") && $0 ~ /Python|python/ { print $1 }
')"
if [ -n "$STRAY_PIDS" ]; then
    echo "⚠️ 发现残留子进程: $STRAY_PIDS，正在清理..."
    echo "$STRAY_PIDS" | xargs kill -9 2>/dev/null || true
fi

echo "✨ Celery 环境已重置。"
