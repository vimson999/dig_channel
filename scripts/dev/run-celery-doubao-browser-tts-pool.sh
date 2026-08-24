#!/bin/bash

# 启动多个 doubao_browser_tts Celery worker；每个 worker 绑定一个独立 Chrome user-data-dir。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_DIR="$PROJECT_ROOT/logs"
RUNTIME_DIR="$PROJECT_ROOT/runtime"
PYTHON_BIN="$PROJECT_ROOT/venv/bin/python"

resolve_path() {
    local raw="$1"
    raw="${raw/#\~/$HOME}"
    if [[ "$raw" != /* ]]; then
        raw="$PROJECT_ROOT/$raw"
    fi
    printf "%s" "$raw"
}

COUNT="${DOUBAO_BROWSER_TTS_PROFILE_COUNT:-${1:-6}}"
if ! [[ "$COUNT" =~ ^[0-9]+$ ]] || [ "$COUNT" -lt 1 ]; then
    echo "❌ DOUBAO_BROWSER_TTS_PROFILE_COUNT 必须是正整数，当前: $COUNT"
    exit 1
fi

PROFILE_POOL_ROOT="$(resolve_path "${DOUBAO_BROWSER_TTS_PROFILE_POOL_ROOT:-$PROJECT_ROOT/runtime/browser_profiles/doubao_pool}")"
QUEUE_NAME="${DOUBAO_BROWSER_TTS_QUEUE:-doubao_browser_tts}"
SAFE_QUEUE_NAME="$(printf "%s" "$QUEUE_NAME" | tr -c 'A-Za-z0-9_.-' '_')"
mkdir -p "$LOG_DIR" "$RUNTIME_DIR"

if [ ! -x "$PROJECT_ROOT/venv/bin/celery" ]; then
    echo "❌ 未找到 Celery 可执行文件: $PROJECT_ROOT/venv/bin/celery"
    echo "   请先创建 venv 并安装依赖。"
    exit 1
fi
if [ ! -x "$PYTHON_BIN" ]; then
    echo "❌ 未找到 Python 可执行文件: $PYTHON_BIN"
    echo "   请先创建 venv 并安装依赖。"
    exit 1
fi

echo "⚙️ 正在启动 doubao_browser_tts profile 池..."
echo "   profile_pool_root: $PROFILE_POOL_ROOT"
echo "   queue: $QUEUE_NAME"
echo "   count: $COUNT"

started=0
skipped=0

for index in $(seq 1 "$COUNT"); do
    slot="worker_$index"
    profile_dir="$PROFILE_POOL_ROOT/$slot"
    log_file="$LOG_DIR/doubao_browser_tts_${SAFE_QUEUE_NAME}_${slot}.log"
    pid_file="$RUNTIME_DIR/doubao_browser_tts_${SAFE_QUEUE_NAME}_${slot}.pid"

    if [ ! -d "$profile_dir" ]; then
        echo "❌ 缺少 profile: $profile_dir"
        echo "   请先运行: DOUBAO_BROWSER_TTS_PROFILE_COUNT=$COUNT ./scripts/dev/prepare-doubao-browser-profile-pool.sh"
        exit 1
    fi

    if ps aux | grep 'celery' | grep 'doubao_browser_tts' | grep "$slot" | grep "$QUEUE_NAME" | grep -v grep >/dev/null 2>&1; then
        echo "↪️  $slot 已在运行，跳过。"
        skipped=$((skipped + 1))
        continue
    fi

    {
        echo "⚙️ 正在启动 Celery Worker (doubao_browser_tts 队列)..."
        echo "   worker_id: $slot"
        echo "   queue: $QUEUE_NAME"
        echo "   user_data_dir: $profile_dir"
    } >> "$log_file"

    env \
        PYTHONPATH="$PROJECT_ROOT/src/backend" \
        OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES \
        DATABASE_TYPE="${DATABASE_TYPE:-postgresql}" \
        DB_PROCESS_ROLE="${DB_PROCESS_ROLE:-worker}" \
        DOUBAO_BROWSER_TTS_WORKER_ID="$slot" \
        DOUBAO_BROWSER_TTS_USER_DATA_DIR="$profile_dir" \
        DOUBAO_BROWSER_TTS_PROFILE_POOL_ROOT="$PROFILE_POOL_ROOT" \
        DOUBAO_BROWSER_TTS_PROFILE_COUNT="$COUNT" \
        DOUBAO_BROWSER_TTS_QUEUE="$QUEUE_NAME" \
        WORKFLOW_WORKER_READY_QUEUES="$QUEUE_NAME" \
        "$PYTHON_BIN" - "$pid_file" "$log_file" "$PROJECT_ROOT/src/backend" \
            "$PROJECT_ROOT/venv/bin/celery" -A celery_config.celery_app worker \
                --loglevel=info \
                --concurrency=1 \
                -P solo \
                -Q "$QUEUE_NAME" \
                --hostname="doubao_browser_tts_${slot}@%h" \
                --without-gossip \
                --without-mingle <<'PY'
import os
import sys

pid_file = sys.argv[1]
log_file = sys.argv[2]
cwd = sys.argv[3]
argv = sys.argv[4:]

first = os.fork()
if first:
    os.waitpid(first, 0)
    sys.exit(0)

os.setsid()
second = os.fork()
if second:
    os._exit(0)

os.chdir(cwd)
stdin_fd = os.open("/dev/null", os.O_RDONLY)
log_fd = os.open(log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
try:
    os.dup2(stdin_fd, 0)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
finally:
    os.close(stdin_fd)
    os.close(log_fd)

with open(pid_file, "w", encoding="utf-8") as handle:
    handle.write(str(os.getpid()))

os.execvpe(argv[0], argv, os.environ)
PY

    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [ -z "$pid" ]; then
        echo "❌ $slot 启动失败：没有写入 pid_file=$pid_file"
        exit 1
    fi
    echo "✅ 已启动 $slot PID=$pid log=$log_file"
    started=$((started + 1))
    sleep 0.5
done

echo
echo "启动完成：started=$started skipped=$skipped"
echo "查看状态："
echo "  http://localhost:8888/celery-monitor.html"
echo "查看日志："
echo "  tail -f $LOG_DIR/doubao_browser_tts_${SAFE_QUEUE_NAME}_worker_1.log"
