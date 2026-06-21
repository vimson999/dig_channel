#!/bin/bash

# Fully restart the local development stack:
# Flask backend, Celery workers, Celery watchdog, and managed schedulers.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_DIR="$PROJECT_ROOT/logs"
RUNTIME_DIR="$PROJECT_ROOT/runtime"
BACKEND_LOG="$LOG_DIR/backend.log"
BACKEND_PID="$RUNTIME_DIR/backend.pid"
PORT="${BACKEND_PORT:-5001}"

mkdir -p "$LOG_DIR" "$RUNTIME_DIR"

echo "🔁 正在全面重启开发环境..."

echo
echo "1/3 停止 Flask 后端..."
"$SCRIPT_DIR/stop-backend.sh" || true

echo
echo "2/3 停止所有 Celery worker、Watchdog、计划任务..."
"$SCRIPT_DIR/stop-all-background-jobs.sh"

echo
echo "3/3 启动 Flask 后端，并按安全默认值补齐托管进程..."
export DB_PROCESS_ROLE="${DB_PROCESS_ROLE:-web}"
export WEB_DB_POOL_SIZE="${WEB_DB_POOL_SIZE:-4}"
export WEB_DB_MAX_OVERFLOW="${WEB_DB_MAX_OVERFLOW:-0}"
export WORKER_DB_POOL_SIZE="${WORKER_DB_POOL_SIZE:-1}"
export WORKER_DB_MAX_OVERFLOW="${WORKER_DB_MAX_OVERFLOW:-0}"
export SCHEDULER_DB_POOL_SIZE="${SCHEDULER_DB_POOL_SIZE:-1}"
export SCHEDULER_DB_MAX_OVERFLOW="${SCHEDULER_DB_MAX_OVERFLOW:-0}"
export DB_POOL_SIZE="${DB_POOL_SIZE:-1}"
export DB_MAX_OVERFLOW="${DB_MAX_OVERFLOW:-0}"
export DB_CONNECT_TIMEOUT="${DB_CONNECT_TIMEOUT:-5}"
export BACKEND_PROCESS_BOOTSTRAP_ENABLED="${BACKEND_PROCESS_BOOTSTRAP_ENABLED:-1}"
export BACKEND_PROCESS_BOOTSTRAP_MODE="${BACKEND_PROCESS_BOOTSTRAP_MODE:-ensure}"
export BACKEND_PROCESS_BOOTSTRAP_CELERY_SERVICES="${BACKEND_PROCESS_BOOTSTRAP_CELERY_SERVICES:-all}"
export BACKEND_PROCESS_BOOTSTRAP_SCHEDULERS="${BACKEND_PROCESS_BOOTSTRAP_SCHEDULERS:-none}"
export BACKEND_PROCESS_BOOTSTRAP_WATCHDOG="${BACKEND_PROCESS_BOOTSTRAP_WATCHDOG:-1}"
export CELERY_AUTO_HEAL_SERVICES="${CELERY_AUTO_HEAL_SERVICES:-$BACKEND_PROCESS_BOOTSTRAP_CELERY_SERVICES}"

PYTHON_LAUNCHER="${PYTHON_LAUNCHER:-python3}"
backend_pid="$("$PYTHON_LAUNCHER" - "$SCRIPT_DIR/run-backend.sh" "$BACKEND_LOG" <<'PY'
import subprocess
import sys

script_path = sys.argv[1]
log_path = sys.argv[2]
log_file = open(log_path, "ab", buffering=0)
process = subprocess.Popen(
    [script_path],
    stdin=subprocess.DEVNULL,
    stdout=log_file,
    stderr=subprocess.STDOUT,
    start_new_session=True,
    close_fds=True,
)
print(process.pid)
PY
)"
echo "$backend_pid" > "$BACKEND_PID"

echo "后端启动 PID: $backend_pid"
echo "后端日志: $BACKEND_LOG"

for _ in $(seq 1 30); do
  if ! ps -p "$backend_pid" >/dev/null 2>&1; then
    echo "❌ 后端进程已退出，最近日志："
    tail -80 "$BACKEND_LOG" 2>/dev/null || true
    exit 1
  fi
  if lsof -ti "tcp:$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "✅ 后端已监听 http://localhost:$PORT"
    echo "✅ 后端 bootstrap 将按配置补齐托管进程。"
    echo
    echo "查看状态："
    echo "  http://localhost:8888/celery-monitor.html"
    echo
    echo "查看日志："
    echo "  tail -f $BACKEND_LOG"
    exit 0
  fi
  sleep 1
done

echo "⚠️ 后端进程仍在运行，但 $PORT 端口 30 秒内未监听。请查看日志："
echo "  tail -f $BACKEND_LOG"
exit 1
