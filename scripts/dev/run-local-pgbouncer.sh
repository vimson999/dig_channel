#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUNTIME_DIR="$PROJECT_ROOT/runtime/pgbouncer"
PID_FILE="$RUNTIME_DIR/pgbouncer.pid"
CONFIG_FILE="$RUNTIME_DIR/pgbouncer.ini"

PGBOUNCER_BIN="${PGBOUNCER_BIN:-$(command -v pgbouncer || true)}"
if [ -z "$PGBOUNCER_BIN" ]; then
  echo "❌ 未找到 pgbouncer，请先执行：brew install pgbouncer"
  exit 1
fi

cd "$PROJECT_ROOT"
python3 "$SCRIPT_DIR/setup-local-pgbouncer.py"

if [ -f "$PID_FILE" ]; then
  existing_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$existing_pid" ] && ps -p "$existing_pid" >/dev/null 2>&1; then
    echo "✅ 本地 PgBouncer 已运行 PID: $existing_pid"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

listening_pid="$(lsof -ti "tcp:${LOCAL_PGBOUNCER_PORT:-6432}" -sTCP:LISTEN 2>/dev/null | head -n 1 || true)"
if [ -n "$listening_pid" ] && ps -p "$listening_pid" -o command= 2>/dev/null | grep -q "pgbouncer"; then
  echo "$listening_pid" > "$PID_FILE"
  echo "✅ 本地 PgBouncer 已运行 PID: $listening_pid"
  exit 0
fi

"$PGBOUNCER_BIN" -d "$CONFIG_FILE"
sleep 0.8

if [ -f "$PID_FILE" ]; then
  echo "✅ 本地 PgBouncer 已启动 PID: $(cat "$PID_FILE")"
else
  echo "❌ PgBouncer 启动后未写入 PID，请查看 logs/pgbouncer.log"
  exit 1
fi
