#!/bin/bash

# Start multiple report_visual_plan workers, one isolated Chrome profile each.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_DIR="$PROJECT_ROOT/logs/celery"
RUNTIME_DIR="$PROJECT_ROOT/runtime"

COUNT="${REPORT_VISUAL_PLAN_PROFILE_COUNT:-${1:-2}}"
if ! [[ "$COUNT" =~ ^[0-9]+$ ]] || [ "$COUNT" -lt 1 ]; then
    echo "❌ REPORT_VISUAL_PLAN_PROFILE_COUNT 必须是正整数，当前: $COUNT"
    exit 1
fi

PROFILE_ROOT="${REPORT_VISUAL_PLAN_PROFILE_ROOT:-$PROJECT_ROOT/runtime/browser_profiles/report_visual_plan_gemini_pool}"
QUEUE_NAME="${REPORT_VISUAL_PLAN_QUEUE:-report_visual_plan}"
SAFE_QUEUE_NAME="$(printf "%s" "$QUEUE_NAME" | tr -c 'A-Za-z0-9_.-' '_')"
mkdir -p "$LOG_DIR" "$RUNTIME_DIR"

echo "⚙️ 正在启动 report_visual_plan profile 池..."
echo "   profile_pool_root: $PROFILE_ROOT"
echo "   queue: $QUEUE_NAME"
echo "   count: $COUNT"

started=0
skipped=0
for index in $(seq 1 "$COUNT"); do
    slot="worker_$index"
    profile_dir="$PROFILE_ROOT/$slot"
    log_file="$LOG_DIR/report_visual_plan_${SAFE_QUEUE_NAME}_${slot}.log"
    pid_file="$RUNTIME_DIR/report_visual_plan_${SAFE_QUEUE_NAME}_${slot}.pid"
    celery_hostname="report_visual_plan@%h-${slot}-${QUEUE_NAME}"

    if [ ! -d "$profile_dir" ]; then
        echo "❌ 缺少 profile: $profile_dir"
        echo "   请先运行: REPORT_VISUAL_PLAN_PROFILE_COUNT=$COUNT ./scripts/dev/prepare-report-visual-plan-gemini-profile-pool.sh"
        exit 1
    fi

    if ps -axo command= | grep -F -- "$celery_hostname" | grep -v grep >/dev/null 2>&1; then
        echo "↪️  $slot 已在运行，跳过（hostname=${celery_hostname}）。"
        skipped=$((skipped + 1))
        continue
    fi

    if [ -f "$pid_file" ]; then
        existing_pid="$(tr -d '[:space:]' < "$pid_file")"
        if [ -n "$existing_pid" ] && kill -0 "$existing_pid" 2>/dev/null; then
            echo "↪️  $slot 已在运行，跳过（PID=$existing_pid）。"
            skipped=$((skipped + 1))
            continue
        fi
    fi

    nohup env \
        REPORT_VISUAL_PLAN_WORKER_ID="$slot" \
        REPORT_VISUAL_PLAN_PROFILE_ROOT="$PROFILE_ROOT" \
        REPORT_VISUAL_PLAN_QUEUE="$QUEUE_NAME" \
        "$SCRIPT_DIR/run-celery-report-visual-plan.sh" \
        >> "$log_file" 2>&1 &
    pid=$!
    echo "$pid" > "$pid_file"
    echo "✅ 已启动 $slot PID=$pid log=$log_file"
    started=$((started + 1))
    sleep 0.5
done

echo
echo "启动完成：started=$started skipped=$skipped"
echo "查看状态：http://localhost:8888/celery-monitor.html"
