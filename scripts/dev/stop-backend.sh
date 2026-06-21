#!/bin/bash

# Stop the local Flask backend on the development port.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUNTIME_DIR="$PROJECT_ROOT/runtime"
PID_FILE="$RUNTIME_DIR/backend.pid"
PORT="${BACKEND_PORT:-5001}"

stop_pid() {
  local pid="$1"
  if [ -z "$pid" ] || ! ps -p "$pid" >/dev/null 2>&1; then
    return 0
  fi

  local children
  children="$(pgrep -P "$pid" 2>/dev/null || true)"
  echo "⚠️ 停止后端进程 PID: ${pid}${children:+，子进程: $children}"
  if [ -n "$children" ]; then
    kill -TERM $children 2>/dev/null || true
  fi
  kill -TERM "$pid" 2>/dev/null || true
  sleep 1

  children="$(pgrep -P "$pid" 2>/dev/null || true)"
  if [ -n "$children" ]; then
    kill -KILL $children 2>/dev/null || true
  fi
  if ps -p "$pid" >/dev/null 2>&1; then
    kill -KILL "$pid" 2>/dev/null || true
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
  echo "⚠️ 端口 $PORT 仍被占用: $remaining"
  exit 1
fi
