#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON="$PROJECT_ROOT/venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    echo "ERROR: Python virtual environment not found: $PYTHON" >&2
    exit 2
fi

run_stage() {
    local label="$1"
    shift
    printf '\n[%s] %s\n' "$label" "$*"
    "$@"
}

cd "$PROJECT_ROOT"

run_stage "1/7" "$PYTHON" scripts/dev/review_bug_records.py --strict
run_stage "2/7" env PYTHONPATH=src/backend "$PYTHON" -m pytest \
    -q tests/unit tests/integration --tb=short --maxfail=20
run_stage "3/7" "$PYTHON" -m compileall -q \
    src/backend scripts/dev scripts/maintenance
run_stage "4/7" npm --prefix src/remotion_renderer run check
run_stage "5/7" env PYTHONPATH=src/backend "$PYTHON" -m pytest \
    -q tests/unit/test_db_projection_discipline.py \
    tests/unit/test_publication_task_query_analyzer.py \
    tests/unit/test_restart_safety_guard.py --tb=short

printf '\n[6/7] Verify the Alembic graph has one head\n'
heads="$({ cd "$PROJECT_ROOT/src/backend" && "$PYTHON" -m flask db heads; } 2>&1)"
printf '%s\n' "$heads"
head_count="$(printf '%s\n' "$heads" | grep -Ec '^[[:alnum:]_]+ \(head\)$' || true)"
if [[ "$head_count" -ne 1 ]]; then
    echo "ERROR: expected exactly one Alembic head, found $head_count" >&2
    exit 1
fi

run_stage "7/7" git diff --check

if [[ -n "${REGRESSION_LOG_SINCE:-}" ]]; then
    run_stage "runtime-log" "$PYTHON" scripts/dev/analyze-publication-task-queries.py \
        --since "$REGRESSION_LOG_SINCE" --limit 40
fi

cat <<'EOF'

PASS: deterministic known-issue regression completed.

Live suites are intentionally opt-in because they call external services,
write the configured database, submit Celery jobs, or render full videos:
  RUN_LIVE_API_TESTS=1
  RUN_YOUTUBE_API_INTEGRATION=1
  RUN_REPORT_VIDEO_INTEGRATION=1
  RUN_REPORT_VIDEO_RENDER=1

Database migration and post-restart real-load acceptance remain separate gates.
EOF
