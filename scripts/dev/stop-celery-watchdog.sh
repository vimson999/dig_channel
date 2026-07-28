#!/bin/bash

# 停止 Celery worker 自愈守护进程

echo "🔍 正在查找 Celery Watchdog..."

# Match only a process whose command line ends with the watchdog entrypoint.
# A plain grep also matches the caller when a compound maintenance command
# happens to mention the same filename.
PIDS=$(pgrep -f 'scripts/dev/celery_watchdog_loop[.]py$' || true)

if [ -z "$PIDS" ]; then
    echo "✅ 没有发现正在运行的 Celery Watchdog。"
    exit 0
fi

echo "⚠️ 发现 Celery Watchdog: $PIDS"
echo "🚀 正在停止..."
for pid in $PIDS; do
    kill "$pid" 2>/dev/null || true
done
sleep 1

LEFTOVER=$(pgrep -f 'scripts/dev/celery_watchdog_loop[.]py$' || true)
if [ ! -z "$LEFTOVER" ]; then
    echo "⚠️ 仍有残留进程: $LEFTOVER，正在强制清理..."
    for pid in $LEFTOVER; do
        kill -9 "$pid" 2>/dev/null || true
    done
fi

echo "✨ Celery Watchdog 已停止。"
