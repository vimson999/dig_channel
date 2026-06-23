#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PID_FILE="$PROJECT_ROOT/runtime/pgbouncer/pgbouncer.pid"

if [ ! -f "$PID_FILE" ]; then
  echo "✅ 本地 PgBouncer 未运行。"
  exit 0
fi

pid="$(cat "$PID_FILE" 2>/dev/null || true)"
if [ -z "$pid" ] || ! ps -p "$pid" >/dev/null 2>&1; then
  rm -f "$PID_FILE"
  echo "✅ 本地 PgBouncer 未运行。"
  exit 0
fi

echo "🛑 正在停止本地 PgBouncer PID: $pid"
kill "$pid" 2>/dev/null || true
for _ in $(seq 1 20); do
  if ! ps -p "$pid" >/dev/null 2>&1; then
    rm -f "$PID_FILE"
    echo "✅ 本地 PgBouncer 已停止。"
    exit 0
  fi
  sleep 0.2
done

kill -9 "$pid" 2>/dev/null || true
rm -f "$PID_FILE"
echo "✅ 本地 PgBouncer 已强制停止。"
