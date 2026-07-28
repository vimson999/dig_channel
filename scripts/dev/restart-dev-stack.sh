#!/bin/bash

# Fully restart the local development stack:
# Flask backend, Celery workers, Celery watchdog, and managed schedulers.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_DIR="$PROJECT_ROOT/logs"
RUNTIME_DIR="$PROJECT_ROOT/runtime"
BACKEND_LOG="$LOG_DIR/backend.log"
BACKEND_PID="${BACKEND_PID_FILE:-$RUNTIME_DIR/backend.pid}"
PORT="${BACKEND_PORT:-5001}"

mkdir -p "$LOG_DIR" "$RUNTIME_DIR"

echo "🔁 正在全面重启开发环境..."

echo
echo "0/8 检查 Redis 运行依赖..."
if [ ! -x "$PROJECT_ROOT/venv/bin/python" ]; then
  echo "❌ 无法执行运行依赖预检：缺少 $PROJECT_ROOT/venv/bin/python"
  exit 1
fi
"$PROJECT_ROOT/venv/bin/python" "$SCRIPT_DIR/check-runtime-redis.py"

echo
echo "1/8 检查数据库结构迁移是否完整..."
"$PROJECT_ROOT/venv/bin/python" "$SCRIPT_DIR/check-runtime-schema.py"

if [ "${FORCE_RESTART_ACTIVE_TASKS:-0}" = "1" ]; then
  echo "⚠️ 已显式允许中断活动业务任务，跳过重启安全预检。"
else
  echo
  echo "2/8 首次检查是否仍有不可中断的业务任务..."
  PYTHONPATH="$PROJECT_ROOT/src/backend" \
    "$PROJECT_ROOT/venv/bin/python" "$SCRIPT_DIR/check-restart-safety.py"
fi

echo
echo "3/8 停止 Watchdog 并冻结 Celery 新消费..."
"$SCRIPT_DIR/stop-celery-watchdog.sh"
PYTHONPATH="$PROJECT_ROOT/src/backend" \
  "$PROJECT_ROOT/venv/bin/python" "$SCRIPT_DIR/quiesce-celery-consumers.py"

echo
echo "4/8 停止 Flask 后端，关闭新的 API 入队入口..."
"$SCRIPT_DIR/stop-backend.sh"

if old_listener="$(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null)" && [ -n "$old_listener" ]; then
  echo "❌ 拒绝继续启动：端口 $PORT 仍由旧进程监听: $old_listener"
  exit 1
fi

echo
echo "5/8 冻结后再次检查，消除检查与停机之间的竞态..."
if [ "${FORCE_RESTART_ACTIVE_TASKS:-0}" = "1" ]; then
  echo "⚠️ 已显式允许中断活动业务任务，跳过冻结后的安全复检。"
else
  drain_timeout="${RESTART_DRAIN_TIMEOUT_SECONDS:-900}"
  drain_interval="${RESTART_DRAIN_CHECK_INTERVAL_SECONDS:-10}"
  drain_started="$(date +%s)"
  while true; do
    set +e
    PYTHONPATH="$PROJECT_ROOT/src/backend" \
      "$PROJECT_ROOT/venv/bin/python" "$SCRIPT_DIR/check-restart-safety.py"
    guard_status=$?
    set -e
    if [ "$guard_status" -eq 0 ]; then
      break
    fi
    if [ "$guard_status" -ne 2 ]; then
      echo "❌ 冻结后的安全复检失败，拒绝停止 worker。"
      exit "$guard_status"
    fi
    elapsed=$(( $(date +%s) - drain_started ))
    if [ "$elapsed" -ge "$drain_timeout" ]; then
      echo "❌ 等待活动任务自然结束已超过 ${drain_timeout}s，拒绝强杀 worker。"
      exit 2
    fi
    echo "⏳ 有任务在首次检查后被领取，等待自然结束后再复检..."
    sleep "$drain_interval"
  done
fi

echo
echo "6/8 停止所有 Celery worker、Watchdog、计划任务..."
"$SCRIPT_DIR/stop-all-background-jobs.sh"

echo
echo "7/8 启动 Flask 后端，并按安全默认值补齐托管进程..."
export DB_PROCESS_ROLE="${DB_PROCESS_ROLE:-web}"
export WEB_DB_POOL_MODE="${WEB_DB_POOL_MODE:-queue}"
export WEB_DB_POOL_SIZE="${WEB_DB_POOL_SIZE:-4}"
export WEB_DB_MAX_OVERFLOW="${WEB_DB_MAX_OVERFLOW:-0}"
export WORKER_DB_POOL_MODE="${WORKER_DB_POOL_MODE:-null}"
export WORKER_DB_POOL_SIZE="${WORKER_DB_POOL_SIZE:-1}"
export WORKER_DB_MAX_OVERFLOW="${WORKER_DB_MAX_OVERFLOW:-0}"
export SCHEDULER_DB_POOL_MODE="${SCHEDULER_DB_POOL_MODE:-null}"
export SCHEDULER_DB_POOL_SIZE="${SCHEDULER_DB_POOL_SIZE:-1}"
export SCHEDULER_DB_MAX_OVERFLOW="${SCHEDULER_DB_MAX_OVERFLOW:-0}"
export DB_POOL_MODE="${DB_POOL_MODE:-queue}"
export DB_POOL_SIZE="${DB_POOL_SIZE:-1}"
export DB_MAX_OVERFLOW="${DB_MAX_OVERFLOW:-0}"
export DB_CONNECT_TIMEOUT="${DB_CONNECT_TIMEOUT:-5}"
export TASK_ADMISSION_GUARD_ENABLED="${TASK_ADMISSION_GUARD_ENABLED:-1}"
export TASK_ADMISSION_DB_MIN_FREE_CONNECTIONS="${TASK_ADMISSION_DB_MIN_FREE_CONNECTIONS:-10}"
export TASK_ADMISSION_DB_MAX_USED_RATIO="${TASK_ADMISSION_DB_MAX_USED_RATIO:-0.85}"
export TASK_ADMISSION_DB_MAX_IDLE_IN_TX="${TASK_ADMISSION_DB_MAX_IDLE_IN_TX:-5}"
export TASK_ADMISSION_RETRY_DELAY_SECONDS="${TASK_ADMISSION_RETRY_DELAY_SECONDS:-180}"
export TASK_ADMISSION_FEATURE_BACKLOG_LIMITS="${TASK_ADMISSION_FEATURE_BACKLOG_LIMITS:-publisher=12,report_audio_workflow=10,report_pdf_audio_workflow=10,report_script_material_workflow=50,report_top10_workflow=5,mp3_video_workflow=5,content_pdf_clean=10,content_audio_clean=5,zsxq_clean_output_publish=5}"
export ZSXQ_SYNC_DB_RETRIES="${ZSXQ_SYNC_DB_RETRIES:-5}"
export ZSXQ_SYNC_DB_RETRY_DELAY_SECONDS="${ZSXQ_SYNC_DB_RETRY_DELAY_SECONDS:-60}"
export LOCAL_PGBOUNCER_ENABLED="${LOCAL_PGBOUNCER_ENABLED:-1}"
export BACKEND_PROCESS_BOOTSTRAP_ENABLED="${BACKEND_PROCESS_BOOTSTRAP_ENABLED:-1}"
export BACKEND_PROCESS_BOOTSTRAP_MODE="${BACKEND_PROCESS_BOOTSTRAP_MODE:-ensure}"
export BACKEND_PROCESS_BOOTSTRAP_CELERY_SERVICES="${BACKEND_PROCESS_BOOTSTRAP_CELERY_SERVICES:-all}"
export BACKEND_PROCESS_BOOTSTRAP_SCHEDULERS="${BACKEND_PROCESS_BOOTSTRAP_SCHEDULERS:-zsxq_auto,content_retention,bilibili_charging,youtube_channel_sync,youtube_video_sync}"
export BACKEND_PROCESS_BOOTSTRAP_WATCHDOG="${BACKEND_PROCESS_BOOTSTRAP_WATCHDOG:-1}"
export CELERY_AUTO_HEAL_SERVICES="${CELERY_AUTO_HEAL_SERVICES:-$BACKEND_PROCESS_BOOTSTRAP_CELERY_SERVICES}"
export CELERY_AUTO_HEAL_REQUEUE_STALE_TASKS="${CELERY_AUTO_HEAL_REQUEUE_STALE_TASKS:-1}"

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
  listener_pid="$(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$listener_pid" ]; then
    if [ "$listener_pid" != "$backend_pid" ]; then
      echo "❌ 启动验收失败：本次启动 PID 为 $backend_pid，但端口 $PORT 由 $listener_pid 监听。"
      "$SCRIPT_DIR/stop-backend.sh" || true
      exit 1
    fi
    echo "✅ 后端已监听 http://localhost:$PORT"
    echo "✅ 已确认监听 PID 为本次启动的新进程: $backend_pid"
    echo "✅ 后端 bootstrap 将按配置补齐托管进程。"
    echo "✅ 8/8 新进程监听验收通过。"
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
