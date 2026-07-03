#!/usr/bin/env bash
# sync-and-restart.sh — rsync + restart on the Azure VM.
# Use ./dev.sh remote-services-sync from the repo root instead.
set -euo pipefail

REMOTE_USER="${AZURE_VM_USER:-m}"
REMOTE_HOST="${1:-}"
REMOTE_PATH="~/ctrader-services"

if [[ -z "$REMOTE_HOST" ]]; then
  echo "Usage: $0 <azure-vm-ip-or-host>" >&2
  exit 1
fi

RSYNC_EXCLUDES=(
  --exclude ".git"
  --exclude ".venv"
  --exclude "__pycache__"
  --exclude "*.pyc"
  --exclude ".ruff_cache"
  --exclude ".pytest_cache"
  --exclude "node_modules"
  --exclude ".logs"
  --exclude "logs"
  --exclude "*.db"
)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[sync] Syncing remote-services to ${REMOTE_HOST}..."
rsync -avz --delete "${RSYNC_EXCLUDES[@]}" "${SCRIPT_DIR}/" "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PATH}/"

echo "[sync] Rebuilding and restarting container..."
ssh "${REMOTE_USER}@${REMOTE_HOST}" "cd ${REMOTE_PATH} && docker compose up -d --build && docker compose restart"

echo "[sync] Done."
