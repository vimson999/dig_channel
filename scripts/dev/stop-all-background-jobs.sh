#!/bin/bash

# Stop all local Celery workers, Celery watchdog, and development schedulers.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "🛑 正在停止所有后台任务：Celery worker、Watchdog、计划任务..."

run_if_exists() {
  local script="$1"
  if [ -x "$script" ]; then
    echo
    echo "▶ $script"
    "$script" || echo "⚠️ $script 执行失败，继续清理后续进程。"
  elif [ -f "$script" ]; then
    echo
    echo "▶ bash $script"
    bash "$script" || echo "⚠️ $script 执行失败，继续清理后续进程。"
  fi
}

stop_by_pattern() {
  local label="$1"
  local pattern="$2"
  local pids
  pids="$(pgrep -f "$pattern" 2>/dev/null || true)"
  if [ -z "$pids" ]; then
    echo "✅ 未发现 ${label}。"
    return 0
  fi

  echo "⚠️ 发现 ${label}: $pids"
  echo "$pids" | xargs kill -TERM 2>/dev/null || true
  sleep 0.8

  local pending
  pending="$(pgrep -f "$pattern" 2>/dev/null || true)"
  if [ -n "$pending" ]; then
    echo "⚠️ ${label} 未及时退出，强制清理: $pending"
    echo "$pending" | xargs kill -KILL 2>/dev/null || true
  fi
}

echo
echo "1/4 停止 Celery Watchdog，避免 worker 被自动拉起..."
run_if_exists "$SCRIPT_DIR/stop-celery-watchdog.sh"

echo
echo "2/4 停止计划任务..."
run_if_exists "$SCRIPT_DIR/stop-zsxq-auto-scheduler.sh"
run_if_exists "$SCRIPT_DIR/stop-zsxq-auto-publish-scheduler.sh"
run_if_exists "$SCRIPT_DIR/stop-report-video-asset-scheduler.sh"
stop_by_pattern "研报视频自动扫描调度器" "run_report_video_auto_scheduler.py"
stop_by_pattern "内容保留清理调度器" "run_content_retention_cleanup_scheduler.py"
stop_by_pattern "通用任务调度器" "src/backend/run_scheduler.py|run_scheduler.py"

echo
echo "3/4 停止各队列 Celery worker..."
run_if_exists "$SCRIPT_DIR/stop-celery-report-audio-workflow.sh"
run_if_exists "$SCRIPT_DIR/stop-celery-report-pdf-gemini.sh"
run_if_exists "$SCRIPT_DIR/stop-celery-doubao-browser-tts.sh"
run_if_exists "$SCRIPT_DIR/stop-celery-mp3-video-workflow.sh"
run_if_exists "$SCRIPT_DIR/stop-celery-zsxq-workflow.sh"
run_if_exists "$SCRIPT_DIR/stop-celery-zsxq-publish.sh"
run_if_exists "$SCRIPT_DIR/stop-celery-zsxq.sh"
run_if_exists "$SCRIPT_DIR/stop-celery-content-pipeline.sh"
run_if_exists "$SCRIPT_DIR/stop-celery-report-video.sh"
run_if_exists "$SCRIPT_DIR/stop-celery-podcast.sh"
run_if_exists "$SCRIPT_DIR/stop-celery-tts.sh"

echo
echo "4/4 最后做一次 Celery 残留清理..."
run_if_exists "$SCRIPT_DIR/stop-celery.sh"

echo
echo "🔍 剩余后台进程检查："
remaining="$(pgrep -af 'celery|celery_watchdog_loop|run_.*scheduler|run_scheduler.py' 2>/dev/null || true)"
if [ -z "$remaining" ]; then
  echo "✅ 已停干净。"
else
  echo "$remaining"
  echo "⚠️ 上面这些进程仍存在，可能需要手工确认是否属于本项目。"
fi

echo
echo "✨ 后台任务停止流程结束。"
