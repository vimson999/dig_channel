#!/bin/bash

# 研报 PDF 到豆包浏览器语音编排专用 Celery Worker
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "⚙️ 正在启动 Celery Worker (report_audio_workflow 队列)..."

if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "❌ 未找到虚拟环境 venv，请先创建并安装依赖。"
    exit 1
fi

export PYTHONPATH=$PROJECT_ROOT/src/backend
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
export DATABASE_TYPE=${DATABASE_TYPE:-postgresql}
export WORKFLOW_WORKER_READY_QUEUES=report_audio_workflow
export DB_PROCESS_ROLE=${DB_PROCESS_ROLE:-worker}
export WORKER_DB_POOL_MODE=${WORKER_DB_POOL_MODE:-null}
export WORKER_DB_POOL_SIZE=${WORKER_DB_POOL_SIZE:-1}
export WORKER_DB_MAX_OVERFLOW=${WORKER_DB_MAX_OVERFLOW:-0}
export DB_POOL_MODE=${DB_POOL_MODE:-queue}
export DB_POOL_SIZE=${DB_POOL_SIZE:-1}
export DB_MAX_OVERFLOW=${DB_MAX_OVERFLOW:-0}
export DB_CONNECT_TIMEOUT=${DB_CONNECT_TIMEOUT:-5}
export TASK_ADMISSION_GUARD_ENABLED=${TASK_ADMISSION_GUARD_ENABLED:-1}
export TASK_ADMISSION_DB_MIN_FREE_CONNECTIONS=${TASK_ADMISSION_DB_MIN_FREE_CONNECTIONS:-10}
export TASK_ADMISSION_DB_MAX_USED_RATIO=${TASK_ADMISSION_DB_MAX_USED_RATIO:-0.85}
export TASK_ADMISSION_DB_MAX_IDLE_IN_TX=${TASK_ADMISSION_DB_MAX_IDLE_IN_TX:-5}
export TASK_ADMISSION_RETRY_DELAY_SECONDS=${TASK_ADMISSION_RETRY_DELAY_SECONDS:-180}
export TASK_ADMISSION_FEATURE_BACKLOG_LIMITS=${TASK_ADMISSION_FEATURE_BACKLOG_LIMITS:-publisher=12,report_audio_workflow=10,report_pdf_audio_workflow=10,mp3_video_workflow=5,content_pdf_clean=10,content_audio_clean=5,zsxq_clean_output_publish=5}

cd "$PROJECT_ROOT/src/backend"
REPORT_AUDIO_WORKFLOW_CONCURRENCY=${REPORT_AUDIO_WORKFLOW_CONCURRENCY:-3}
celery -A celery_config.celery_app worker --loglevel=info --concurrency="$REPORT_AUDIO_WORKFLOW_CONCURRENCY" -P threads -Q report_audio_workflow --hostname=report_audio_workflow@%h --without-gossip --without-mingle
