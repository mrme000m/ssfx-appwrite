#!/usr/bin/env bash
# dev/scripts/start.sh — Start local development services using Docker Compose.
# Services include: ssfx-server, dataservice, account-hub, agent-harness, ctrader
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
REMOTE_SERVICES_DIR="${PROJECT_ROOT}/remote-services"

cd "${REMOTE_SERVICES_DIR}"

echo "[dev] Starting local development services with Docker Compose..."
echo "[dev] Project root: ${PROJECT_ROOT}"
echo "[dev] Remote services dir: ${REMOTE_SERVICES_DIR}"

# Check if Docker is running
if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker daemon is not running" >&2
    exit 1
fi

# Ensure config files exist
if [[ ! -f "${REMOTE_SERVICES_DIR}/config/v2.env" ]]; then
    echo "[dev] Creating v2.env from example..."
    cp "${REMOTE_SERVICES_DIR}/config/v2.env.example" "${REMOTE_SERVICES_DIR}/config/v2.env"
    echo "[dev] EDIT ${REMOTE_SERVICES_DIR}/config/v2.env with your actual credentials before starting!"
fi

# Pull latest base image to warm layer cache
echo "[dev] Pulling latest base images..."
docker compose pull 2>/dev/null || echo "[dev] Pull completed (or no images to pull)"

# Build with layer cache
echo "[dev] Building services..."
docker compose build

# Start services in detached mode
echo "[dev] Starting services..."
docker compose up -d --remove-orphans

# Wait for health checks
echo "[dev] Waiting for services to become healthy..."
for i in {1..30}; do
    if docker compose ps | grep -q "healthy"; then
        echo "[dev] Services are healthy!"
        break
    fi
    sleep 2
done

echo "[dev] Local development services started successfully."
echo ""
echo "[dev] Services running at:"
echo "  - ssfx-server:        http://localhost:8000"
echo "  - dataservice control: http://localhost:9000"
echo "  - dataservice SSE:     http://localhost:9001"
echo "  - dataservice API:     http://localhost:9002"
echo "  - agent-harness:      http://localhost:9003"
echo "  - ctrader:            http://localhost:9300"
echo "  - account-hub WS:     ws://localhost:9301"
echo ""
echo "[dev] To stop: ./dev.sh stop"
echo "[dev] To view logs: ./dev.sh logs"
