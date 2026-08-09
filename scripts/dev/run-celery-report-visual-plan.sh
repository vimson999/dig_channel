#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

WORKER_ID="${REPORT_VISUAL_PLAN_WORKER_ID:-${1:-worker_1}}"
SAFE_WORKER_ID="$(printf "%s" "$WORKER_ID" | tr -c 'A-Za-z0-9_.-' '_')"
PROFILE_POOL_ROOT="${REPORT_VISUAL_PLAN_PROFILE_ROOT:-$PROJECT_ROOT/runtime/browser_profiles/report_visual_plan_gemini_pool}"
QUEUE_NAME="${REPORT_VISUAL_PLAN_QUEUE:-report_visual_plan}"

if [ -n "${REPORT_VISUAL_PLAN_GEMINI_USER_DATA_DIR:-}" ]; then
    export REPORT_PDF_GEMINI_USER_DATA_DIR="$REPORT_VISUAL_PLAN_GEMINI_USER_DATA_DIR"
else
    # Do not inherit REPORT_PDF_GEMINI_USER_DATA_DIR from the PDF/image
    # workers; that would silently make every storyboard worker share one
    # profile and defeat the pool isolation.
    export REPORT_PDF_GEMINI_USER_DATA_DIR="$PROFILE_POOL_ROOT/$WORKER_ID"
fi

if [ ! -d "${REPORT_PDF_GEMINI_USER_DATA_DIR:-}" ]; then
    echo "❌ 分镜 Gemini profile 不存在: ${REPORT_PDF_GEMINI_USER_DATA_DIR:-}"
    echo "   请先运行: REPORT_VISUAL_PLAN_PROFILE_COUNT=2 ./scripts/dev/prepare-report-visual-plan-gemini-profile-pool.sh"
    exit 1
fi

if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "未找到虚拟环境 venv"
    exit 1
fi

export PYTHONPATH=$PROJECT_ROOT/src/backend
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
export DATABASE_TYPE=${DATABASE_TYPE:-postgresql}
export DB_PROCESS_ROLE=${DB_PROCESS_ROLE:-worker}
export WORKER_DB_POOL_MODE=${WORKER_DB_POOL_MODE:-null}
export DB_POOL_SIZE=${DB_POOL_SIZE:-1}
export DB_MAX_OVERFLOW=${DB_MAX_OVERFLOW:-0}

echo "⚙️ 正在启动分镜 Gemini Worker ($QUEUE_NAME 队列)..."
echo "   worker_id: $WORKER_ID"
echo "   queue: $QUEUE_NAME"
echo "   user_data_dir: $REPORT_PDF_GEMINI_USER_DATA_DIR"

if [ "$WORKER_ID" = "default" ]; then
    CELERY_HOSTNAME="report_visual_plan@%h"
else
    CELERY_HOSTNAME="report_visual_plan@%h-${SAFE_WORKER_ID}-${QUEUE_NAME}"
fi

cd "$PROJECT_ROOT/src/backend"
# Each worker owns one independent signed-in profile.  Keep concurrency=1 per
# profile; parallelism comes from the profile pool, not from sharing a Chrome
# user-data directory between threads.
celery -A celery_config.celery_app worker \
    --loglevel=info \
    --concurrency=${REPORT_VISUAL_PLAN_CELERY_CONCURRENCY:-1} \
    -P threads \
    -Q "$QUEUE_NAME" \
    --hostname="$CELERY_HOSTNAME" \
    --without-gossip \
    --without-mingle
