#!/bin/bash
# Reset OJS to a clean state. Entrypoint generates config + runs install automatically.
# Usage: ./docker/reset-ojs.sh
set -e

cd "$(dirname "$0")/.."

echo "==> Stopping containers..."
docker compose down

echo "==> Removing OJS data volume (code + config)..."
docker volume rm wpojs_ojs_data 2>/dev/null || true

echo "==> Removing OJS files volume..."
docker volume rm wpojs_ojs_files 2>/dev/null || true

echo "==> Starting containers..."
docker compose up -d

echo "==> Waiting for OJS database to be healthy..."
until docker compose exec ojs-db mariadb -u root -p"${OJS_DB_ROOT_PASSWORD:-devroot123}" -e "SELECT 1" >/dev/null 2>&1; do
    sleep 2
done

echo "==> Resetting OJS database..."
docker compose exec ojs-db mariadb -u root -p"${OJS_DB_ROOT_PASSWORD:-devroot123}" -e "DROP DATABASE IF EXISTS ojs; CREATE DATABASE ojs;"

echo "==> Restarting OJS (entrypoint generates config + runs install automatically)..."
docker compose restart ojs

echo "==> Waiting for OJS to be installed..."
for i in $(seq 1 60); do
    if docker compose exec ojs grep -q "installed = On" /var/www/html/config.inc.php 2>/dev/null; then
        echo "==> OJS reset + install complete."
        echo "    URL:      http://localhost:8081"
        echo "    Admin:    admin / admin123"
        echo "    Next:     docker compose exec ojs bash /scripts/setup-ojs.sh"
        exit 0
    fi
    sleep 2
done

echo "==> WARNING: OJS install didn't complete within 2 minutes. Check logs."
echo "    docker compose logs ojs --tail=30"
