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

kill_project_processes() {
  local label="$1"
  local awk_filter="$2"
  local pids

  pids="$(ps -axo pid=,command= | awk -v project="$PROJECT_ROOT" "$awk_filter")"
  if [ -z "$pids" ]; then
    echo "✅ 未发现 ${label}。"
    return 0
  fi

  echo "⚠️ 发现 ${label}: $pids"
  echo "$pids" | xargs kill -TERM 2>/dev/null || true
  sleep 1

  local pending
  pending="$(ps -axo pid=,command= | awk -v project="$PROJECT_ROOT" "$awk_filter")"
  if [ -n "$pending" ]; then
    echo "⚠️ ${label} 未及时退出，强制清理: $pending"
    echo "$pending" | xargs kill -KILL 2>/dev/null || true
  fi
}

echo
echo "1/5 停止 Celery Watchdog，避免 worker 被自动拉起..."
run_if_exists "$SCRIPT_DIR/stop-celery-watchdog.sh"

echo
echo "2/5 停止计划任务..."
run_if_exists "$SCRIPT_DIR/stop-zsxq-auto-scheduler.sh"
run_if_exists "$SCRIPT_DIR/stop-zsxq-auto-publish-scheduler.sh"
run_if_exists "$SCRIPT_DIR/stop-report-video-asset-scheduler.sh"
stop_by_pattern "研报视频自动扫描调度器" "run_report_video_auto_scheduler.py"
stop_by_pattern "内容保留清理调度器" "run_content_retention_cleanup_scheduler.py"
stop_by_pattern "B站充电人数采集调度器" "run_bilibili_charging_scheduler.py"
stop_by_pattern "B站情报每小时同步调度器" "run_bilibili_intelligence_scheduler.py"
stop_by_pattern "YouTube 频道数据同步调度器" "run_youtube_channel_sync_scheduler.py"
stop_by_pattern "YouTube 视频数据同步调度器" "run_youtube_video_sync_scheduler.py"
stop_by_pattern "通用任务调度器" "src/backend/run_scheduler.py|run_scheduler.py"

echo
echo "3/5 停止各队列 Celery worker..."
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
run_if_exists "$SCRIPT_DIR/stop-celery-platform-upload.sh"

echo
echo "4/5 最后做一次 Celery 残留清理..."
run_if_exists "$SCRIPT_DIR/stop-celery.sh"

echo
echo "5/5 项目范围兜底清理，避免旧 worker/调度器残留..."
kill_project_processes "本项目 Celery worker 残留进程" '
  index($0, project "/venv/bin/celery") && $0 ~ / worker( |$)/ { print $1 }
'
kill_project_processes "本项目 Celery 启动包装脚本残留进程" '
  index($0, project "/scripts/dev/run-celery-") { print $1 }
'
kill_project_processes "本项目 Watchdog/调度器残留进程" '
  index($0, project) && ($0 ~ /celery_watchdog_loop/ || $0 ~ /run_.*scheduler/ || $0 ~ /run_scheduler\.py/) { print $1 }
'

echo
echo "🔍 剩余后台进程检查："
if ! remaining="$(ps -axo pid=,command= | awk -v project="$PROJECT_ROOT" '
  index($0, project) && ($0 ~ /\/venv\/bin\/celery/ || $0 ~ /\/scripts\/dev\/run-celery-/ || $0 ~ /celery_watchdog_loop/ || $0 ~ /run_.*scheduler/ || $0 ~ /run_scheduler\.py/) { print }
')"; then
  echo "❌ 无法完成后台进程残留检查，拒绝报告重启前环境已清理。"
  exit 1
fi
if [ -z "$remaining" ]; then
  echo "✅ 已停干净。"
else
  echo "$remaining"
  echo "❌ 上面这些本项目后台进程仍存在；请先处理残留，再启动新服务。"
  exit 1
fi

echo
echo "✨ 后台任务停止流程结束。"
