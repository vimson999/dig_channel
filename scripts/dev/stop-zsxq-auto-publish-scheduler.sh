#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

RUNTIME_DIR="$PROJECT_ROOT/runtime"
PID_FILE="$RUNTIME_DIR/zsxq_auto_publish_scheduler.pid"

echo "🛑 正在停止 ZSXQ 自动发帖调度器..."

read_pid_from_json() {
  python3 - <<'PY' "$1"
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit(0)
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(0)
pid = data.get("pid")
if pid:
    print(pid)
PY
}

if [ -f "$PID_FILE" ]; then
  PID="$(read_pid_from_json "$PID_FILE" || true)"
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

PIDS="$(pgrep -f 'run_zsxq_auto_publish_scheduler.py' || true)"
if [ -n "$PIDS" ]; then
  echo "⚠️ 未发现 PID 文件，尝试按脚本名清理残留进程：$PIDS"
  echo "$PIDS" | xargs kill -TERM
  sleep 0.5
  PENDING="$(pgrep -f 'run_zsxq_auto_publish_scheduler.py' || true)"
  if [ -n "$PENDING" ]; then
    echo "$PENDING" | xargs kill -KILL
  fi
  echo "✅ 调度器残留进程已清理。"
else
  echo "✅ 未发现运行中的 ZSXQ 自动发帖调度器进程。"
fi
