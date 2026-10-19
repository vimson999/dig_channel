#!/bin/bash

# Login/boot entrypoint. It never bypasses dependency or business-task gates.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATTEMPTS="${BOOT_RUNTIME_READY_ATTEMPTS:-40}"
INTERVAL="${BOOT_RUNTIME_READY_INTERVAL_SECONDS:-15}"

for attempt in $(seq 1 "$ATTEMPTS"); do
  if "$PROJECT_ROOT/venv/bin/python" "$SCRIPT_DIR/check-boot-runtime-dependencies.py"; then
    break
  fi
  if [ "$attempt" -eq "$ATTEMPTS" ]; then
    echo "BLOCKED: runtime dependencies did not become ready after $ATTEMPTS attempts."
    exit 3
  fi
  sleep "$INTERVAL"
done

if curl --noproxy '*' -fsS --max-time 3 http://127.0.0.1:5001/health >/dev/null 2>&1; then
  echo "OK: backend is already healthy; boot bootstrap is idempotent."
  exit 0
fi

"$SCRIPT_DIR/run-local-pgbouncer.sh"
PYTHONPATH="$PROJECT_ROOT/src/backend" \
  "$PROJECT_ROOT/venv/bin/python" "$SCRIPT_DIR/check-restart-safety.py"

exec "$SCRIPT_DIR/run-backend.sh"
