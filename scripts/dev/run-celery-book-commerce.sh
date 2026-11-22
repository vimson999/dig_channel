#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
if [ ! -x "$PROJECT_ROOT/venv/bin/celery" ]; then
    echo "未找到 venv/bin/celery，请先安装项目依赖。" >&2
    exit 1
fi
export PYTHONPATH="$PROJECT_ROOT/src/backend"
export DB_PROCESS_ROLE=worker
cd "$PROJECT_ROOT/src/backend"
exec "$PROJECT_ROOT/venv/bin/celery" -A celery_config.celery_app worker --loglevel=info --concurrency=1 -P threads -Q book_commerce --hostname=book_commerce@%h --without-gossip --without-mingle
