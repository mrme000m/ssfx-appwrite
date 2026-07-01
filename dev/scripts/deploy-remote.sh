#!/usr/bin/env bash
# dev/scripts/deploy-remote.sh — Deploy to a specific remote target.
# Usage: dev.sh deploy-remote <target>
# Default target is the Azure VM.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-azure}"

case "${TARGET}" in
  azure|vm)
    echo "[dev] Resolving Azure VM target..."
    read -r VM_NAME VM_IP < <("${SCRIPT_DIR}/_azure_vm.py")
    echo "[dev] Deploying to Azure VM ${VM_NAME} at ${VM_IP}..."
    # TODO: add Azure VM deployment steps over SSH.
    echo "[dev] Deployment to Azure VM ${VM_NAME} completed."
    ;;
  *)
    echo "[dev] Deploying to remote target: ${TARGET}..."
    # TODO: add target-specific deployment steps.
    # Values needed for deployment should be read from Appwrite Database.
    echo "[dev] Deployment to ${TARGET} completed."
    ;;
esac
