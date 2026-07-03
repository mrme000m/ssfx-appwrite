#!/usr/bin/env bash
# Deployment script for remote-services.
# Idempotent — safe to re-run.
#
# Supports either an explicit SSH host alias (recommended) or auto-resolving the
# Azure VM IP. Set SSH_HOST to any host defined in ~/.ssh/config, e.g.:
#
#   SSH_HOST=aws-ssfx ./deploy-azure.sh
#
# Defaults (for backwards compatibility):
#   SSH_HOST=m@<azure-vm-ip>
#
# Prerequisites on the target host:
#   - Docker & docker compose installed
#   - SSH key-based auth from this machine
#   - Local .env must contain APPWRITE_ENDPOINT, APPWRITE_PROJECT_ID, APPWRITE_API_KEY
#
# Usage (from local dev machine):
#   cd /Volumes/ExMac/code/ssfx/appwrite-auth/remote-services
#   ./deploy-azure.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_NAME="ssfx-remote-services"

# Allow overriding the SSH target via environment. If not set, fall back to the
# Azure VM public IP.
VM_USER="${VM_USER:-m}"
VM_NAME="${VM_NAME:-ubuntu-server}"
VM_RG="${VM_RG:-RG-UBUNTU-VM}"

if [[ -n "${SSH_HOST:-}" ]]; then
  echo "[deploy] Using explicit SSH host: ${SSH_HOST}"
else
  echo "[deploy] Resolving Azure VM IP ..."
  VM_IP=$(az vm list-ip-addresses \
    --name "${VM_NAME}" \
    --resource-group "${VM_RG}" \
    --query '[0].virtualMachine.network.publicIpAddresses[0].ipAddress' \
    --output tsv)

  if [[ -z "${VM_IP}" ]]; then
    echo "ERROR: Could not resolve public IP for ${VM_NAME} in ${VM_RG}"
    echo "       Set SSH_HOST to deploy to a different host."
    exit 1
  fi
  SSH_HOST="${VM_USER}@${VM_IP}"
  echo "[deploy] Target VM: ${VM_IP}"
fi

# Derive remote user/dir from SSH_HOST for rsync paths. This is a best-effort
# default; adjust REMOTE_USER/REMOTE_DIR via env vars if needed.
if [[ -n "${REMOTE_USER:-}" ]]; then
  :
elif [[ "${SSH_HOST}" == *@* ]]; then
  REMOTE_USER="$(echo "${SSH_HOST}" | cut -s -d@ -f1)"
else
  # Host is an SSH alias; ask ssh for the configured user.
  REMOTE_USER="$(ssh -G "${SSH_HOST}" | awk '/^user / {print $2; exit}')"
fi
REMOTE_USER="${REMOTE_USER:-${VM_USER}}"
REMOTE_DIR="${REMOTE_DIR:-/home/${REMOTE_USER}/${PROJECT_NAME}}"
REMOTE_PPLX_DIR="${REMOTE_PPLX_DIR:-/home/${REMOTE_USER}/pplx-agent}"
PPLX_DIR="${PROJECT_ROOT}/pplx-agent"
LOCAL_COOKIE_PATH="${HOME}/.config/perplexity/cookies.json"
REMOTE_COOKIE_PATH="${REMOTE_COOKIE_PATH:-/home/${REMOTE_USER}/.config/perplexity/cookies.json}"

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
# Sync files to host (rsync over SSH)
# ---------------------------------------------------------------------------
echo "[deploy] Syncing code to ${REMOTE_DIR} ..."
ssh "${SSH_HOST}" "mkdir -p ${REMOTE_DIR}"
rsync -avz \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='.mypy_cache' \
  --exclude='.ruff_cache' \
  --exclude='logs/*.log' \
  --exclude='*.db' \
  "${SCRIPT_DIR}/" "${SSH_HOST}:${REMOTE_DIR}/"

# Sync the PPLX Agent package so the Docker build context can reach ../pplx-agent.
echo "[deploy] Syncing PPLX agent code to ${REMOTE_PPLX_DIR} ..."
ssh "${SSH_HOST}" "mkdir -p ${REMOTE_PPLX_DIR}"
rsync -avz \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='.mypy_cache' \
  --exclude='.ruff_cache' \
  --exclude='logs/*.log' \
  --exclude='*.db' \
  --exclude='reports' \
  --exclude='.cache' \
  "${PPLX_DIR}/" "${SSH_HOST}:${REMOTE_PPLX_DIR}/"

# Sync Perplexity cookies if they exist locally.
if [[ -f "${LOCAL_COOKIE_PATH}" ]]; then
  echo "[deploy] Syncing Perplexity cookies ..."
  ssh "${SSH_HOST}" "mkdir -p $(dirname "${REMOTE_COOKIE_PATH}")"
  rsync -avz "${LOCAL_COOKIE_PATH}" "${SSH_HOST}:${REMOTE_COOKIE_PATH}"
else
  echo "WARN: Perplexity cookies not found at ${LOCAL_COOKIE_PATH}"
  echo "      The PPLX Agent will not be able to query Perplexity on the host."
fi

# Upload bootstrap compose env file.
echo "[deploy] Uploading compose bootstrap env file ..."
cat > "${SCRIPT_DIR}/.env.compose" <<EOF
APPWRITE_ENDPOINT=${APPWRITE_ENDPOINT}
APPWRITE_PROJECT_ID=${APPWRITE_PROJECT_ID}
APPWRITE_API_KEY=${APPWRITE_API_KEY}
EOF
rsync -avz "${SCRIPT_DIR}/.env.compose" "${SSH_HOST}:${REMOTE_DIR}/.env.compose"
rm -f "${SCRIPT_DIR}/.env.compose"

# ---------------------------------------------------------------------------
# Remote execution: build & start
# ---------------------------------------------------------------------------
echo "[deploy] Building & starting on ${SSH_HOST} ..."
ssh "${SSH_HOST}" bash <<'REMOTE'
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
echo "[deploy] Waiting for services to become healthy ..."
for i in {1..30}; do
  if docker compose ps | grep -q "healthy"; then
    echo "[deploy] Services are healthy!"
    break
  fi
  sleep 2
done

REMOTE

echo "[deploy] Deployment complete. Services available via Cloudflare Tunnel:"
echo "  - ssfx-server:        https://ssfx-api.mrme.tech"
echo "  - dataservice control: https://ds-control.mrme.tech"
echo "  - dataservice SSE:     https://ds-sse.mrme.tech"
echo "  - dataservice API:     https://dataservice.mrme.tech"
echo "  - agent-harness:      https://agent.mrme.tech"
echo "  - pplx-agent:         https://pplx-agent.mrme.tech"
echo "  - ctrader:            https://ctrader.mrme.tech"
echo "  - account-hub WS:     wss://account-hub.mrme.tech"
