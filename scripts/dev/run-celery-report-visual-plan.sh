#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

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

cd "$PROJECT_ROOT/src/backend"
# One signed-in Gemini profile can only execute one browser session at a time.
# Keep storyboard jobs in the broker instead of occupying threads that will
# eventually hit the profile-lock wait deadline.
celery -A celery_config.celery_app worker --loglevel=info --concurrency=${REPORT_VISUAL_PLAN_CELERY_CONCURRENCY:-1} -P threads -Q report_visual_plan --hostname=report_visual_plan@%h --without-gossip --without-mingle
