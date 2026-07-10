#!/bin/bash

# 豆包网页朗读捕获专用 Celery Worker
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

WORKER_ID="${DOUBAO_BROWSER_TTS_WORKER_ID:-${1:-default}}"
PROFILE_POOL_ROOT="${DOUBAO_BROWSER_TTS_PROFILE_POOL_ROOT:-$PROJECT_ROOT/runtime/browser_profiles/doubao_pool}"
QUEUE_NAME="${DOUBAO_BROWSER_TTS_QUEUE:-doubao_browser_tts}"

if [ "$WORKER_ID" != "default" ] && [ -z "${DOUBAO_BROWSER_TTS_USER_DATA_DIR:-}" ]; then
    export DOUBAO_BROWSER_TTS_USER_DATA_DIR="$PROFILE_POOL_ROOT/$WORKER_ID"
fi

if [ "$WORKER_ID" != "default" ] && [ ! -d "${DOUBAO_BROWSER_TTS_USER_DATA_DIR:-}" ]; then
    echo "❌ 缺少豆包 profile: $DOUBAO_BROWSER_TTS_USER_DATA_DIR"
    echo "   请先运行: DOUBAO_BROWSER_TTS_PROFILE_COUNT=2 ./scripts/dev/prepare-doubao-browser-profile-pool.sh"
    exit 1
fi

echo "⚙️ 正在启动 Celery Worker (doubao_browser_tts 队列)..."
echo "   worker_id: $WORKER_ID"
echo "   queue: $QUEUE_NAME"
if [ -n "${DOUBAO_BROWSER_TTS_USER_DATA_DIR:-}" ]; then
    echo "   user_data_dir: $DOUBAO_BROWSER_TTS_USER_DATA_DIR"
fi

if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "❌ 未找到虚拟环境 venv，请先创建并安装依赖。"
    exit 1
fi

export PYTHONPATH=$PROJECT_ROOT/src/backend
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
export DATABASE_TYPE=${DATABASE_TYPE:-postgresql}
export DB_PROCESS_ROLE=${DB_PROCESS_ROLE:-worker}
export DOUBAO_BROWSER_TTS_WORKER_ID="$WORKER_ID"
# 真实浏览器任务必须串行执行，避免同一 Chrome profile 被并发控制。

cd "$PROJECT_ROOT/src/backend"
celery -A celery_config.celery_app worker --loglevel=info --concurrency=1 -P solo -Q "$QUEUE_NAME" --hostname="doubao_browser_tts_${WORKER_ID}@%h" --without-gossip --without-mingle
