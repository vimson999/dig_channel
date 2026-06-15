#!/bin/bash

# 研报 PDF Gem 解析专用 Celery Worker
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "⚙️ 正在启动 Celery Worker (report_pdf_gemini 队列)..."

if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "❌ 未找到虚拟环境 venv，请先创建并安装依赖。"
    exit 1
fi

export PYTHONPATH=$PROJECT_ROOT/src/backend
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
export DATABASE_TYPE=${DATABASE_TYPE:-postgresql}
# 真实浏览器任务必须串行执行，避免同一 Chrome profile 被并发控制。

cd "$PROJECT_ROOT/src/backend"
celery -A celery_config.celery_app worker --loglevel=info --concurrency=1 -P solo -Q report_pdf_gemini --hostname=report_pdf_gemini@%h --without-gossip --without-mingle
