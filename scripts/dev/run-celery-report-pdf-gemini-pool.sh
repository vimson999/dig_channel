#!/bin/bash

# 启动多个 report_pdf_gemini Celery worker；每个 worker 绑定一个独立 Chrome user-data-dir。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_DIR="$PROJECT_ROOT/logs"
RUNTIME_DIR="$PROJECT_ROOT/runtime"

resolve_path() {
    local raw="$1"
    raw="${raw/#\~/$HOME}"
    if [[ "$raw" != /* ]]; then
        raw="$PROJECT_ROOT/$raw"
    fi
    printf "%s" "$raw"
}

COUNT="${REPORT_PDF_GEMINI_PROFILE_COUNT:-${1:-2}}"
if ! [[ "$COUNT" =~ ^[0-9]+$ ]] || [ "$COUNT" -lt 1 ]; then
    echo "❌ REPORT_PDF_GEMINI_PROFILE_COUNT 必须是正整数，当前: $COUNT"
    exit 1
fi

PROFILE_POOL_ROOT="$(resolve_path "${REPORT_PDF_GEMINI_PROFILE_POOL_ROOT:-$PROJECT_ROOT/runtime/browser_profiles/report_pdf_gemini_pool}")"
QUEUE_NAME="${REPORT_PDF_GEMINI_QUEUE:-report_pdf_gemini}"
SAFE_QUEUE_NAME="$(printf "%s" "$QUEUE_NAME" | tr -c 'A-Za-z0-9_.-' '_')"
mkdir -p "$LOG_DIR" "$RUNTIME_DIR"

echo "⚙️ 正在启动 report_pdf_gemini profile 池..."
echo "   profile_pool_root: $PROFILE_POOL_ROOT"
echo "   queue: $QUEUE_NAME"
echo "   count: $COUNT"

started=0
skipped=0

for index in $(seq 1 "$COUNT"); do
    slot="worker_$index"
    profile_dir="$PROFILE_POOL_ROOT/$slot"
    log_file="$LOG_DIR/report_pdf_gemini_${SAFE_QUEUE_NAME}_${slot}.log"
    pid_file="$RUNTIME_DIR/report_pdf_gemini_${SAFE_QUEUE_NAME}_${slot}.pid"

    if [ ! -d "$profile_dir" ]; then
        echo "❌ 缺少 profile: $profile_dir"
        echo "   请先运行: REPORT_PDF_GEMINI_PROFILE_COUNT=$COUNT ./scripts/dev/prepare-report-pdf-gemini-profile-pool.sh"
        exit 1
    fi

    if ps aux | grep 'celery' | grep 'report_pdf_gemini' | grep "$slot" | grep "$QUEUE_NAME" | grep -v grep >/dev/null 2>&1; then
        echo "↪️  $slot 已在运行，跳过。"
        skipped=$((skipped + 1))
        continue
    fi

    nohup env \
        REPORT_PDF_GEMINI_WORKER_ID="$slot" \
        REPORT_PDF_GEMINI_USER_DATA_DIR="$profile_dir" \
        REPORT_PDF_GEMINI_QUEUE="$QUEUE_NAME" \
        "$SCRIPT_DIR/run-celery-report-pdf-gemini.sh" >> "$log_file" 2>&1 &
    pid=$!

    echo "$pid" > "$pid_file"
    echo "✅ 已启动 $slot PID=$pid log=$log_file"
    started=$((started + 1))
    sleep 0.5
done

echo
echo "启动完成：started=$started skipped=$skipped"
echo "查看状态："
echo "  http://localhost:8888/celery-monitor.html"
echo "查看日志："
echo "  tail -f $LOG_DIR/report_pdf_gemini_${SAFE_QUEUE_NAME}_worker_1.log"
