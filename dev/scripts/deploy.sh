#!/usr/bin/env bash
# dev/scripts/deploy.sh — Deploy to the Azure VM (primary remote environment).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[dev] Resolving Azure VM target..."
read -r VM_NAME VM_IP < <("${SCRIPT_DIR}/_azure_vm.py")
echo "[dev] Target VM: ${VM_NAME} (${VM_IP})"

echo "[dev] Deploying to Azure VM ${VM_NAME} at ${VM_IP}..."
# TODO: add deployment steps over SSH, e.g.:
#   ssh "m@${VM_IP}" 'bash -s' < scripts/remote-deploy.sh
# Values needed for the deployment should be read from Appwrite Database by the remote runtime.
echo "[dev] Deployment to Azure VM ${VM_NAME} completed."
