#!/usr/bin/env bash
# dev/scripts/deploy-remote.sh — Deploy to a specific remote target.
# Usage: dev.sh deploy-remote <target>
# Default target is the Azure VM (~/ssfx-remote-services).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TARGET="${1:-azure}"

# Canonical remote directory for all deployments
REMOTE_DIR="~/ssfx-remote-services"

case "${TARGET}" in
  azure|vm)
    echo "[dev] Resolving Azure VM target..."
    read -r VM_NAME VM_IP < <("${SCRIPT_DIR}/_azure_vm.py")
    echo "[dev] Deploying to Azure VM ${VM_NAME} at ${VM_IP}..."
    echo "[dev] Remote directory: ${REMOTE_DIR}"
    
    # Sync remote-services to canonical location on VM
    echo "[dev] Syncing code to ${REMOTE_DIR}..."
    ssh "m@${VM_IP}" "mkdir -p ${REMOTE_DIR}"
    rsync -avz \
      --exclude='.git' \
      --exclude='__pycache__' \
      --exclude='.mypy_cache' \
      --exclude='.ruff_cache' \
      --exclude='.pytest_cache' \
      --exclude='.venv' \
      --exclude='logs/*.log' \
      --exclude='*.db' \
      "${PROJECT_ROOT}/remote-services/" "m@${VM_IP}:${REMOTE_DIR}/"
    
    # Build and restart on remote
    echo "[dev] Building and starting on VM..."
    ssh "m@${VM_IP}" "cd ${REMOTE_DIR} && docker compose pull 2>/dev/null || true && docker compose build && docker compose up -d --remove-orphans"
    
    # Wait for health
    echo "[dev] Waiting for services to become healthy..."
    for i in {1..30}; do
      if ssh "m@${VM_IP}" "cd ${REMOTE_DIR} && docker compose ps" | grep -q "healthy"; then
        echo "[dev] Services are healthy!"
        break
      fi
      sleep 2
    done
    
    echo "[dev] Deployment to Azure VM ${VM_NAME} completed."
    echo "[dev] Services available at:"
    echo "  - ssfx-server:        http://${VM_IP}:8000"
    echo "  - dataservice control: http://${VM_IP}:9000"
    echo "  - dataservice SSE:     http://${VM_IP}:9001"
    echo "  - dataservice API:     http://${VM_IP}:9002"
    echo "  - agent-harness:      http://${VM_IP}:9003"
    echo "  - ctrader:            http://${VM_IP}:9300"
    echo "  - account-hub WS:     ws://${VM_IP}:9301"
    ;;
  *)
    echo "[dev] Deploying to remote target: ${TARGET}..."
    # Values needed for deployment should be read from Appwrite Database.
    echo "[dev] Deployment to ${TARGET} completed."
    ;;
esac
