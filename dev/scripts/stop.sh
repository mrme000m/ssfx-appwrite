#!/usr/bin/env bash
# dev/scripts/stop.sh — Stop local development services.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
REMOTE_SERVICES_DIR="${PROJECT_ROOT}/remote-services"

cd "${REMOTE_SERVICES_DIR}"

echo "[dev] Stopping local development services..."

# Stop containers gracefully
docker compose down --timeout 30

echo "[dev] Local development services stopped."
