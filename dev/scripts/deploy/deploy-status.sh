#!/usr/bin/env bash
# dev/scripts/deploy-status.sh — Show remote VM and deployment status.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Load SSH target from vm.env if available; default to the current AWS VM.
VM_ENV="${PROJECT_ROOT}/remote-services/config/vm.env"
SSH_HOST="aws-ssfx"
if [[ -f "${VM_ENV}" ]]; then
  # shellcheck source=/dev/null
  source "${VM_ENV}"
fi
SSH_HOST="${SSH_HOST:-aws-ssfx}"

REMOTE_DIR="/home/${VM_USER:-ec2-user}/ssfx-remote-services"

echo "[dev] Remote VM status (${SSH_HOST}):"
ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "${SSH_HOST}" "
  echo \"Host: \$(hostname)\"
  echo \"Uptime: \$(uptime -p 2>/dev/null || uptime)\"
  echo \"Docker containers:\"
  cd ${REMOTE_DIR} && docker compose ps 2>/dev/null || echo '  (stack not running)'
" || echo "[dev] Could not reach ${SSH_HOST}."

echo ""
echo "[dev] Cloudflare tunnel status:"
if [[ -n "${CF_API_TOKEN:-}" ]]; then
  "${SCRIPT_DIR}/cf_tunnel_status.py"
else
  echo "  CF_API_TOKEN not set; skipping tunnel status." >&2
fi

echo ""
echo "[dev] Deployment status check completed."
