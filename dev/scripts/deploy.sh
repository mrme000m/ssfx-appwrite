#!/usr/bin/env bash
# dev/scripts/deploy.sh — Deploy to the Azure VM (primary remote environment).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR}/../.."

REMOTE_SERVICES_DIR="${PROJECT_ROOT}/remote-services"
DEPLOY_SCRIPT="${REMOTE_SERVICES_DIR}/deploy-azure.sh"

echo "[dev] Starting deployment to Azure VM ..."

if [[ ! -x "${DEPLOY_SCRIPT}" ]]; then
    echo "ERROR: Deployment script not found or not executable: ${DEPLOY_SCRIPT}"
    exit 1
fi

cd "${REMOTE_SERVICES_DIR}"
bash "${DEPLOY_SCRIPT}"

echo "[dev] Deployment completed successfully."