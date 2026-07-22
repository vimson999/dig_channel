#!/bin/bash

# B站 / YouTube / 抖音 / 视频号后投上传专用 Celery Worker
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "🚀 正在启动平台上传 Celery Worker (platform_upload 队列)..."

if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "❌ 未找到虚拟环境 venv，请先创建并安装依赖。"
    exit 1
fi

export PYTHONPATH=$PROJECT_ROOT/src/backend
export DATABASE_TYPE=${DATABASE_TYPE:-postgresql}

cd "$PROJECT_ROOT/src/backend"
# 上传主要消耗网络 I/O；默认允许两个任务并行。仍可通过环境变量按机器带宽覆盖。
celery -A celery_config.celery_app worker --loglevel=info --concurrency=${PLATFORM_UPLOAD_CELERY_CONCURRENCY:-2} -P threads -Q platform_upload --hostname=platform_upload@%h --without-gossip --without-mingle
