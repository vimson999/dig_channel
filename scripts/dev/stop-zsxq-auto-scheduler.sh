#!/bin/bash

# 停止知识星球系统计划任务进程
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

RUNTIME_DIR="$PROJECT_ROOT/runtime"
PID_FILE="$RUNTIME_DIR/zsxq_auto_scheduler.pid"

echo "🛑 正在停止 ZSXQ 自动同步调度器..."

if [ -f "$PID_FILE" ]; then
  PID="$(tr -d '[:space:]' < "$PID_FILE" || true)"
  if [ -n "${PID}" ] && kill -0 "$PID" 2>/dev/null; then
    echo "✅ 发现运行中的进程：$PID，尝试优雅退出..."
    kill -TERM "$PID" || true

    for _ in $(seq 1 20); do
      if ! kill -0 "$PID" 2>/dev/null; then
        rm -f "$PID_FILE"
        echo "✅ 调度器已停止。"
        exit 0
      fi
      sleep 0.25
    done

    echo "⚠️ 进程未及时退出，改为强制结束..."
    kill -KILL "$PID" 2>/dev/null || true
    sleep 0.5
    rm -f "$PID_FILE"
    if ! kill -0 "$PID" 2>/dev/null; then
      echo "✅ 调度器已强制停止。"
      exit 0
    fi
    echo "❌ 强制停止失败，请检查进程状态。"
    exit 1
  else
    echo "⚠️ PID 文件存在但进程已不存在，清理文件。"
    rm -f "$PID_FILE"
  fi
fi

PIDS="$(pgrep -f 'run_zsxq_auto_scheduler.py' || true)"
if [ -n "$PIDS" ]; then
  echo "⚠️ 未发现 PID 文件，尝试按脚本名清理残留进程：$PIDS"
  echo "$PIDS" | xargs kill -TERM
  sleep 0.5
  PENDING="$(pgrep -f 'run_zsxq_auto_scheduler.py' || true)"
  if [ -n "$PENDING" ]; then
    echo "$PENDING" | xargs kill -KILL
  fi
  echo "✅ 调度器残留进程已清理。"
else
  echo "✅ 未发现运行中的 ZSXQ 自动同步调度器进程。"
fi
