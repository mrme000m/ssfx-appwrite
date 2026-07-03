#!/usr/bin/env bash
# dev/scripts/logs.sh — Tail logs from all development services.
# Usage: ./dev.sh logs [service_name]
# If no service specified, tails all services.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
REMOTE_SERVICES_DIR="${PROJECT_ROOT}/remote-services"

cd "${REMOTE_SERVICES_DIR}"

if [[ $# -gt 0 ]]; then
    echo "[dev] Tailing logs for service: $*"
    docker compose logs -f "$@"
else
    echo "[dev] Tailing logs for all services (Ctrl+C to stop)..."
    docker compose logs -f
fi
