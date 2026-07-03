#!/usr/bin/env bash
# deploy-stack.sh — Build and start the SSFX remote-services Docker stack.
#
# Run on the target VM by setup-vm.sh after code has been synced.

set -euo pipefail

REMOTE_DIR="${REMOTE_DIR:-~/ssfx-remote-services}"
cd "${REMOTE_DIR}"

set -a
# shellcheck source=/dev/null
source .env.compose 2>/dev/null || true
set +a

docker compose pull 2>/dev/null || true
docker compose build
docker compose down --timeout 30 2>/dev/null || true
docker compose up -d --remove-orphans

echo "[setup-vm] Waiting for services to become healthy ..."
for i in {1..30}; do
  if docker compose ps | grep -q "healthy"; then
    echo "[setup-vm] Services are healthy!"
    break
  fi
  sleep 2
done

docker compose ps
