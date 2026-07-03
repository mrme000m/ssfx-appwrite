#!/usr/bin/env bash
# dev/scripts/deploy-remote.sh — Deploy to a specific remote target.
# Usage: dev.sh deploy-remote <target>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TARGET="${1:-aws}"

SETUP_SCRIPT="${PROJECT_ROOT}/remote-services/setup_vm.py"

case "${TARGET}" in
  aws|vm|default)
    echo "[dev] Deploying to primary AWS VM via ${SETUP_SCRIPT} ..."
    cd "${PROJECT_ROOT}"
    SKIP_VM_SETUP=1 SKIP_TUNNEL=1 python3 "${SETUP_SCRIPT}"
    echo "[dev] Deployment to AWS VM completed."
    ;;
  azure)
    echo "[dev] Azure VM deployment is deprecated. Use the AWS target instead." >&2
    echo "[dev] Falling back to legacy Azure deployer ..."
    read -r VM_NAME VM_IP < <("${SCRIPT_DIR}/_azure_vm.py")
    echo "[dev] Deploying to Azure VM ${VM_NAME} at ${VM_IP} ..."
    REMOTE_DIR="~/ssfx-remote-services"
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
    ssh "m@${VM_IP}" "cd ${REMOTE_DIR} && docker compose pull 2>/dev/null || true && docker compose build && docker compose up -d --remove-orphans"
    echo "[dev] Deployment to Azure VM ${VM_NAME} completed."
    ;;
  *)
    echo "[dev] Unknown remote target: ${TARGET}" >&2
    echo "[dev] Supported targets: aws (default), azure" >&2
    exit 1
    ;;
esac
