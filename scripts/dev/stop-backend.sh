#!/bin/bash

# Stop the local Flask backend on the development port.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUNTIME_DIR="$PROJECT_ROOT/runtime"
PID_FILE="${BACKEND_PID_FILE:-$RUNTIME_DIR/backend.pid}"
PORT="${BACKEND_PORT:-5001}"
STOP_TIMEOUT_SECONDS="${BACKEND_STOP_TIMEOUT_SECONDS:-10}"

descendant_pids() {
  local parent="$1"
  local child
  for child in $(pgrep -P "$parent" 2>/dev/null || true); do
    descendant_pids "$child"
    printf '%s\n' "$child"
  done
}

wait_for_exit() {
  local deadline=$((SECONDS + STOP_TIMEOUT_SECONDS))
  local pids=("$@")
  local pid

  while [ "$SECONDS" -lt "$deadline" ]; do
    local alive=0
    for pid in "${pids[@]}"; do
      if ps -p "$pid" >/dev/null 2>&1; then
        alive=1
        break
      fi
    done
    [ "$alive" -eq 0 ] && return 0
    sleep 0.2
  done
  return 1
}

stop_pid() {
  local pid="$1"
  if [ -z "$pid" ] || ! ps -p "$pid" >/dev/null 2>&1; then
    return 0
  fi

  local descendants
  descendants="$(descendant_pids "$pid")"
  local targets=()
  local child
  while IFS= read -r child; do
    [ -n "$child" ] && targets+=("$child")
  done <<< "$descendants"
  targets+=("$pid")

  echo "⚠️ 停止后端进程 PID: ${pid}${descendants:+，后代进程: $descendants}"
  kill -TERM "${targets[@]}" 2>/dev/null || true
  if ! wait_for_exit "${targets[@]}"; then
    echo "⚠️ 后端未在 ${STOP_TIMEOUT_SECONDS}s 内退出，强制终止: ${targets[*]}"
    kill -KILL "${targets[@]}" 2>/dev/null || true
    wait_for_exit "${targets[@]}" || true
  fi
}

echo "🛑 正在停止 Flask 后端，端口 $PORT..."

if [ -f "$PID_FILE" ]; then
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  stop_pid "$pid"
  rm -f "$PID_FILE"
fi

port_pids="$(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$port_pids" ]; then
  echo "⚠️ 端口 $PORT 仍有监听进程: $port_pids"
  for pid in $port_pids; do
    stop_pid "$pid"
  done
fi

remaining="$(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [ -z "$remaining" ]; then
  echo "✅ Flask 后端已停止。"
else
  echo "❌ 端口 $PORT 仍被占用: $remaining"
  exit 1
fi
