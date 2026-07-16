#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_PATH="$(realpath "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"

find_deploy_dir() {
    local candidate
    for candidate in \
        "${DIG_YTB_DEPLOY_DIR:-}" \
        "$PWD" \
        "$SCRIPT_DIR" \
        "$SCRIPT_DIR/../../.."; do
        if [[ -n "$candidate" && -f "$candidate/docker-compose.yml" ]]; then
            (cd "$candidate" && pwd)
            return 0
        fi
    done
    return 1
}

DEPLOY_DIR="$(find_deploy_dir)" || {
    echo "Unable to find the production docker-compose.yml." >&2
    echo "Set DIG_YTB_DEPLOY_DIR to the server deployment directory." >&2
    exit 1
}

cd "$DEPLOY_DIR"

if ! docker compose config --services | grep -qx "web"; then
    echo "Compose project at $DEPLOY_DIR does not define the web service." >&2
    exit 1
fi

if docker compose config --services | grep -qx "worker"; then
    echo "Stopping the server-side Celery worker..."
    docker compose stop --timeout 60 worker
fi

echo "Starting the web backend only..."
docker compose up -d --no-deps web

for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:5001/health >/dev/null; then
        echo "Web backend is healthy."
        docker compose ps -a web worker 2>/dev/null || docker compose ps -a web
        exit 0
    fi
    sleep 2
done

echo "Web backend did not become healthy within 60 seconds." >&2
docker compose ps -a web worker 2>/dev/null || true
docker compose logs --tail=100 web >&2 || true
exit 1
