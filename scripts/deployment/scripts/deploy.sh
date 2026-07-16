#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/scripts/deployment/docker/docker-compose.yml"
ENV_FILE="$PROJECT_ROOT/.env.production"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Missing $ENV_FILE. Copy .env.production.example and fill it on the server."
    exit 1
fi

for key in SECRET_KEY JWT_SECRET_KEY LOGIN_PORTAL_TOKEN DATABASE_URL CORS_ORIGINS; do
    if ! grep -q "^${key}=." "$ENV_FILE"; then
        echo "Missing required production setting: $key"
        exit 1
    fi
done

cd "$PROJECT_ROOT"
echo "Building production images..."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" build

echo "Applying database migrations before starting the public service..."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" run --rm \
    -w /app/backend youtube-analytics flask --app app:create_app db upgrade

echo "Starting private backend/Redis and public Nginx..."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --remove-orphans

echo "Checking the internal application health endpoint..."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T youtube-analytics \
    python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=5)"

echo "Deployment completed. Use LOGIN_PATH from the protected authentication bundle."
