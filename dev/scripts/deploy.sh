#!/usr/bin/env bash
# dev/scripts/deploy.sh — Deploy remote-services to the primary AWS VM.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SETUP_SCRIPT="${PROJECT_ROOT}/remote-services/setup_vm.py"

echo "[dev] Starting code-only deployment to primary remote VM ..."

if [[ ! -f "${SETUP_SCRIPT}" ]]; then
    echo "ERROR: Deployment script not found: ${SETUP_SCRIPT}"
    exit 1
fi

cd "${PROJECT_ROOT}"
SKIP_VM_SETUP=1 SKIP_TUNNEL=1 python3 "${SETUP_SCRIPT}"

echo "[dev] Deployment completed successfully."
