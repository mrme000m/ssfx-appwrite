#!/usr/bin/env bash
# Azure VM deployment script for remote-services.
# Idempotent — safe to re-run.
#
# Prerequisites on the Azure VM:
#   - Docker & docker compose installed
#   - APPWRITE_API_KEY, CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET set in ~/.env
#   - SSH key-based auth from this machine to the VM
#
# Usage (from local dev machine):
#   cd /Volumes/ExMac/code/ssfx/appwrite-auth/remote-services
#   ./deploy-azure.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_NAME="ssfx-remote-services"
VM_USER="m"
VM_NAME="ubuntu-server"
VM_RG="RG-UBUNTU-VM"
REMOTE_DIR="/home/${VM_USER}/${PROJECT_NAME}"

# ---------------------------------------------------------------------------
# Resolve VM IP via Azure CLI
# ---------------------------------------------------------------------------
echo "[deploy-azure] Resolving VM IP ..."
VM_IP=$(az vm list-ip-addresses \
  --name "${VM_NAME}" \
  --resource-group "${VM_RG}" \
  --query '[0].virtualMachine.network.publicIpAddresses[0].ipAddress' \
  --output tsv)

if [[ -z "${VM_IP}" ]]; then
  echo "ERROR: Could not resolve public IP for ${VM_NAME} in ${VM_RG}"
  exit 1
fi
echo "[deploy-azure] Target VM: ${VM_IP}"

# ---------------------------------------------------------------------------
# Sync files to VM (rsync over SSH)
# ---------------------------------------------------------------------------
echo "[deploy-azure] Syncing code to ${REMOTE_DIR} ..."
ssh "${VM_USER}@${VM_IP}" "mkdir -p ${REMOTE_DIR}"
rsync -avz \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='.mypy_cache' \
  --exclude='.ruff_cache' \
  --exclude='logs/*.log' \
  --exclude='*.db' \
  "${SCRIPT_DIR}/" "${VM_USER}@${VM_IP}:${REMOTE_DIR}/"

# ---------------------------------------------------------------------------
# Remote execution: build & start
# ---------------------------------------------------------------------------
echo "[deploy-azure] Building & starting on VM ..."
ssh "${VM_USER}@${VM_IP}" bash <<'REMOTE'
set -euo pipefail
cd ~/ssfx-remote-services

# Pull latest base image to warm layer cache
docker compose pull 2>/dev/null || true

# Build with layer cache
docker compose build

# Graceful rolling restart
docker compose down --timeout 30
docker compose up -d --remove-orphans

# Wait for health checks
echo "[deploy-azure] Waiting for services to become healthy ..."
for i in {1..30}; do
  if docker compose ps | grep -q "healthy"; then
    echo "[deploy-azure] Services are healthy!"
    break
  fi
  sleep 2
done

REMOTE

echo "[deploy-azure] Deployment complete. Services running on ${VM_IP}:"
echo "  - ssfx-server:        http://${VM_IP}:8000"
echo "  - dataservice control: http://${VM_IP}:9000"
echo "  - dataservice SSE:     http://${VM_IP}:9001"
echo "  - dataservice API:     http://${VM_IP}:9002"
echo "  - ctrader:            http://${VM_IP}:9300"
echo "  - account-hub WS:     ws://${VM_IP}:9301"
