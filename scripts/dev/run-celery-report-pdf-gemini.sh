#!/bin/bash

# 研报 PDF Gem 解析专用 Celery Worker
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

WORKER_ID="${REPORT_PDF_GEMINI_WORKER_ID:-${1:-default}}"
SAFE_WORKER_ID="$(printf "%s" "$WORKER_ID" | tr -c 'A-Za-z0-9_.-' '_')"
PROFILE_POOL_ROOT="${REPORT_PDF_GEMINI_PROFILE_POOL_ROOT:-$PROJECT_ROOT/runtime/browser_profiles/report_pdf_gemini_pool}"
QUEUE_NAME="${REPORT_PDF_GEMINI_QUEUE:-report_pdf_gemini}"
SAFE_QUEUE_NAME="$(printf "%s" "$QUEUE_NAME" | tr -c 'A-Za-z0-9_.-' '_')"

if [ "$WORKER_ID" != "default" ] && [ -z "${REPORT_PDF_GEMINI_USER_DATA_DIR:-}" ]; then
    export REPORT_PDF_GEMINI_USER_DATA_DIR="$PROFILE_POOL_ROOT/$WORKER_ID"
fi

if [ "$WORKER_ID" != "default" ] && [ ! -d "${REPORT_PDF_GEMINI_USER_DATA_DIR:-}" ]; then
    echo "❌ worker profile 不存在: ${REPORT_PDF_GEMINI_USER_DATA_DIR:-}"
    echo "   请先运行: REPORT_PDF_GEMINI_PROFILE_COUNT=3 ./scripts/dev/prepare-report-pdf-gemini-profile-pool.sh"
    exit 1
fi

if [ "$WORKER_ID" = "default" ]; then
    CELERY_HOSTNAME="report_pdf_gemini@%h"
else
    CELERY_HOSTNAME="report_pdf_gemini@%h-${SAFE_WORKER_ID}-${SAFE_QUEUE_NAME}"
fi

echo "⚙️ 正在启动 Celery Worker ($QUEUE_NAME 队列)..."
echo "   worker_id: $WORKER_ID"
echo "   queue: $QUEUE_NAME"
if [ -n "${REPORT_PDF_GEMINI_USER_DATA_DIR:-}" ]; then
    echo "   user_data_dir: $REPORT_PDF_GEMINI_USER_DATA_DIR"
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
export WORKER_DB_POOL_MODE=${WORKER_DB_POOL_MODE:-null}
export WORKER_DB_POOL_SIZE=${WORKER_DB_POOL_SIZE:-1}
export WORKER_DB_MAX_OVERFLOW=${WORKER_DB_MAX_OVERFLOW:-0}
export DB_POOL_MODE=${DB_POOL_MODE:-queue}
export DB_POOL_SIZE=${DB_POOL_SIZE:-1}
export DB_MAX_OVERFLOW=${DB_MAX_OVERFLOW:-0}
# 真实浏览器任务必须串行执行，避免同一 Chrome profile 被并发控制。

cd "$PROJECT_ROOT/src/backend"
celery -A celery_config.celery_app worker --loglevel=info --concurrency=1 -P solo -Q "$QUEUE_NAME" --hostname="$CELERY_HOSTNAME" --without-gossip --without-mingle
