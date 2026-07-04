#!/usr/bin/env bash
# dev/scripts/deploy-remote.sh — Deploy to a specific remote target.
# Usage: dev.sh deploy-remote <target>
#   dev.sh deploy-remote aws     # Primary AWS VM (default)
#   dev.sh deploy-remote azure   # Legacy Azure VM (deprecated)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
TARGET="${1:-aws}"

SETUP_SCRIPT="${PROJECT_ROOT}/dev/scripts/setup_vm.py"

case "${TARGET}" in
  aws|vm|default)
    echo "[dev] Deploying to primary AWS VM via ${SETUP_SCRIPT} ..."
    cd "${PROJECT_ROOT}"
    SKIP_VM_SETUP=1 SKIP_TUNNEL=1 python3 "${SETUP_SCRIPT}"
    echo "[dev] Deployment to AWS VM completed."
    ;;
  azure)
    echo "[dev] Azure VM deployment is deprecated and no longer supported." >&2
    echo "[dev] Use 'aws' target instead: ./dev.sh deploy-remote aws" >&2
    exit 1
    ;;
  *)
    echo "[dev] Unknown remote target: ${TARGET}" >&2
    echo "[dev] Supported targets: aws (default)" >&2
    exit 1
    ;;
esac
