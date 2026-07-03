#!/usr/bin/env bash
# deploy-master.sh — Master deployment script for remote-services to Azure VM.
# DEPRECATED: Use `python3 remote-services/setup_vm.py` for the current AWS VM.
# Kept only for legacy Azure VM compatibility.
#
# This script orchestrates:
#   1. VM cleanup (stop old services, remove old containers)
#   2. Cloudflare tunnel config clean-up + update
#   3. Fresh Docker deployment to VM
#   4. Health verification
#
# Usage:
#   cd /Volumes/ExMac/code/ssfx/appwrite-auth/remote-services
#   ./deploy-master.sh [--skip-cleanup] [--skip-tunnel]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR}"
VM_USER="m"
VM_NAME="ubuntu-server"
VM_RG="RG-UBUNTU-VM"
REMOTE_DIR="/home/${VM_USER}/ssfx-remote-services"
SKIP_CLEANUP=false
SKIP_TUNNEL=false

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-cleanup) SKIP_CLEANUP=true; shift ;;
    --skip-tunnel) SKIP_TUNNEL=true; shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Resolve VM IP
# ---------------------------------------------------------------------------
echo "[deploy-master] Resolving VM IP ..."
VM_IP=$(az vm list-ip-addresses \
  --name "${VM_NAME}" \
  --resource-group "${VM_RG}" \
  --query '[0].virtualMachine.network.publicIpAddresses[0].ipAddress' \
  --output tsv)

if [[ -z "${VM_IP}" ]]; then
  echo "ERROR: Could not resolve public IP for ${VM_NAME} in ${VM_RG}"
  exit 1
fi
echo "[deploy-master] Target VM: ${VM_IP}"

# ---------------------------------------------------------------------------
# Step 1: Cleanup old deployments on VM
# ---------------------------------------------------------------------------
if [[ "${SKIP_CLEANUP}" != "true" ]]; then
  echo ""
  echo "[deploy-master] === Step 1: Cleaning up old deployments ==="
  
  # Copy cleanup script to VM
  scp "${PROJECT_ROOT}/cleanup-vm.sh" "${VM_USER}@${VM_IP}:/tmp/cleanup-vm.sh"
  
  # Run cleanup
  ssh "${VM_USER}@${VM_IP}" bash /tmp/cleanup-vm.sh
  
  echo "[deploy-master] Cleanup complete."
else
  echo "[deploy-master] === Step 1: Skipping cleanup ==="
fi

# ---------------------------------------------------------------------------
# Step 2: Update Cloudflare tunnel
# ---------------------------------------------------------------------------
if [[ "${SKIP_TUNNEL}" != "true" ]]; then
  echo ""
  echo "[deploy-master] === Step 2: Updating Cloudflare tunnel ==="
  bash "${PROJECT_ROOT}/setup-cf-tunnel.sh"
else
  echo "[deploy-master] === Step 2: Skipping tunnel update ==="
fi

# ---------------------------------------------------------------------------
# Step 3: Sync & deploy fresh Docker stack
# ---------------------------------------------------------------------------
echo ""
echo "[deploy-master] === Step 3: Deploying fresh Docker stack ==="

# Ensure remote directory exists
ssh "${VM_USER}@${VM_IP}" "mkdir -p ${REMOTE_DIR}/config ${REMOTE_DIR}/logs"

# Sync code (excluding local build artifacts and sensitive files)
echo "[deploy-master] Syncing source code ..."
rsync -avz \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='.mypy_cache' \
  --exclude='.ruff_cache' \
  --exclude='logs/*.log' \
  --exclude='*.db' \
  --exclude='integration_test.py' \
  --exclude='docker-compose.test.yml' \
  --exclude='cleanup-vm.sh' \
  --exclude='setup-cf-tunnel.sh' \
  --exclude='deploy-master.sh' \
  "${PROJECT_ROOT}/" "${VM_USER}@${VM_IP}:${REMOTE_DIR}/"

# Build and start on VM
echo "[deploy-master] Building Docker image on VM ..."
ssh "${VM_USER}@${VM_IP}" bash <<REMOTE
set -euo pipefail
cd ${REMOTE_DIR}

# Ensure proper permissions
chmod +x bin/* 2>/dev/null || true

# Pull latest base image to warm layer cache
docker compose pull 2>/dev/null || true

# Build with layer cache
docker compose build

# Graceful stop of any existing container
docker compose down --timeout 30 2>/dev/null || true

# Start fresh
docker compose up -d --remove-orphans

# Wait for health checks
echo "[deploy-master] Waiting for services to become healthy ..."
for i in {1..30}; do
  if docker compose ps | grep -q "healthy"; then
    echo "[deploy-master] Services are healthy!"
    break
  fi
  sleep 2
done

# Show running containers
echo "[deploy-master] Running containers:"
docker compose ps

REMOTE

# ---------------------------------------------------------------------------
# Step 4: Verify deployment
# ---------------------------------------------------------------------------
echo ""
echo "[deploy-master] === Step 4: Verification ==="

echo "[deploy-master] Checking local ports on VM ..."
ssh "${VM_USER}@${VM_IP}" "ss -tlnp | grep -E ':(8000|9000|9001|9002|9300|9301)\\b'"

echo ""
echo "[deploy-master] Checking container logs ..."
ssh "${VM_USER}@${VM_IP}" "docker logs --tail 20 ctrader-services 2>/dev/null || echo 'Container not found'"

echo ""
echo "[deploy-master] === Deployment Complete ==="
echo ""
echo "Services now available via Cloudflare tunnel:"
echo "  https://dataservice.mrme.tech   -> Market Data REST API (port 9002)"
echo "  https://ds-sse.mrme.tech        -> MCP SSE Server (port 9001)"
echo "  https://ssfx-api.mrme.tech      -> Telegram Webhook / Admin API (port 8000)"
echo "  https://ctrader.mrme.tech       -> cTrader Unified Service (port 9300)"
echo "  https://account-hub.mrme.tech   -> Account Hub WebSocket (port 9301)"
echo ""
echo "Direct VM access:"
echo "  ssh ${VM_USER}@${VM_IP}"
echo "  cd ${REMOTE_DIR}"
echo "  docker compose logs -f"
