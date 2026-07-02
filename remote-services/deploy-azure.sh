#!/usr/bin/env bash
# Azure VM deployment script for remote-services.
# Idempotent — safe to re-run.
#
# Prerequisites on the Azure VM:
#   - Docker & docker compose installed
#   - SSH key-based auth from this machine to the VM
#   - Local .env must contain APPWRITE_ENDPOINT, APPWRITE_PROJECT_ID, APPWRITE_API_KEY
#
# Usage (from local dev machine):
#   cd /Volumes/ExMac/code/ssfx/appwrite-auth/remote-services
#   ./deploy-azure.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_NAME="ssfx-remote-services"
VM_USER="m"
VM_NAME="ubuntu-server"
VM_RG="RG-UBUNTU-VM"
REMOTE_DIR="/home/${VM_USER}/${PROJECT_NAME}"
PPLX_DIR="${PROJECT_ROOT}/pplx-agent"
REMOTE_PPLX_DIR="/home/${VM_USER}/pplx-agent"
LOCAL_COOKIE_PATH="${HOME}/.config/perplexity/cookies.json"
REMOTE_COOKIE_PATH="/home/${VM_USER}/.config/perplexity/cookies.json"

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
# Load bootstrap secrets from project .env
# ---------------------------------------------------------------------------
ENV_FILE="${PROJECT_ROOT}/.env"
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "ERROR: Project .env not found at ${ENV_FILE}"
  exit 1
fi
set -a
# shellcheck source=/dev/null
source "${ENV_FILE}"
set +a

for var in APPWRITE_ENDPOINT APPWRITE_PROJECT_ID APPWRITE_API_KEY; do
  if [[ -z "${!var:-}" ]]; then
    echo "ERROR: ${var} is not set in ${ENV_FILE}"
    exit 1
  fi
done

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

# Sync the PPLX Agent package so the Docker build context can reach ../pplx-agent.
echo "[deploy-azure] Syncing PPLX agent code to ${REMOTE_PPLX_DIR} ..."
ssh "${VM_USER}@${VM_IP}" "mkdir -p ${REMOTE_PPLX_DIR}"
rsync -avz \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='.mypy_cache' \
  --exclude='.ruff_cache' \
  --exclude='logs/*.log' \
  --exclude='*.db' \
  --exclude='reports' \
  --exclude='.cache' \
  "${PPLX_DIR}/" "${VM_USER}@${VM_IP}:${REMOTE_PPLX_DIR}/"

# Sync Perplexity cookies if they exist locally.
if [[ -f "${LOCAL_COOKIE_PATH}" ]]; then
  echo "[deploy-azure] Syncing Perplexity cookies ..."
  ssh "${VM_USER}@${VM_IP}" "mkdir -p $(dirname "${REMOTE_COOKIE_PATH}")"
  rsync -avz "${LOCAL_COOKIE_PATH}" "${VM_USER}@${VM_IP}:${REMOTE_COOKIE_PATH}"
else
  echo "WARN: Perplexity cookies not found at ${LOCAL_COOKIE_PATH}"
  echo "      The PPLX Agent will not be able to query Perplexity on the VM."
fi

# Upload bootstrap compose env file.
echo "[deploy-azure] Uploading compose bootstrap env file ..."
cat > "${SCRIPT_DIR}/.env.compose" <<EOF
APPWRITE_ENDPOINT=${APPWRITE_ENDPOINT}
APPWRITE_PROJECT_ID=${APPWRITE_PROJECT_ID}
APPWRITE_API_KEY=${APPWRITE_API_KEY}
EOF
rsync -avz "${SCRIPT_DIR}/.env.compose" "${VM_USER}@${VM_IP}:${REMOTE_DIR}/.env.compose"
rm -f "${SCRIPT_DIR}/.env.compose"

# ---------------------------------------------------------------------------
# Remote execution: build & start
# ---------------------------------------------------------------------------
echo "[deploy-azure] Building & starting on VM ..."
ssh "${VM_USER}@${VM_IP}" bash <<'REMOTE'
set -euo pipefail
cd ~/ssfx-remote-services

# Load bootstrap secrets into the compose environment.
set -a
# shellcheck source=/dev/null
source .env.compose
set +a

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
echo "  - agent-harness:      http://${VM_IP}:9003"
echo "  - pplx-agent:         http://${VM_IP}:9004"
echo "  - ctrader:            http://${VM_IP}:9300"
echo "  - account-hub WS:     ws://${VM_IP}:9301"
