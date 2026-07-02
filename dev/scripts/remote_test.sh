#!/usr/bin/env bash
# dev/scripts/remote-test.sh — Run remote verification tests on the Azure VM.
# Usage: ./dev.sh remote-test
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR}/../.."
REMOTE_SERVICES_DIR="${PROJECT_ROOT}/remote-services"
VERIFY_SCRIPT="${REMOTE_SERVICES_DIR}/remote-verify.sh"

echo "[dev] Running remote verification tests on Azure VM ..."

if [[ ! -x "${VERIFY_SCRIPT}" ]]; then
    echo "ERROR: Remote verification script not found: ${VERIFY_SCRIPT}"
    exit 1
fi

# Resolve VM IP
VM_IP=$(az vm list-ip-addresses \
  --name "ubuntu-server" \
  --resource-group "RG-UBUNTU-VM" \
  --query '[0].virtualMachine.network.publicIpAddresses[0].ipAddress' \
  --output tsv)

if [[ -z "${VM_IP}" ]]; then
    echo "ERROR: Could not resolve VM IP"
    exit 1
fi

echo "[dev] Target VM: ${VM_IP}"

# Copy the verify script to the VM and run it
scp -q "${VERIFY_SCRIPT}" "m@${VM_IP}:/tmp/remote-verify.sh"
ssh "m@${VM_IP}" "VM_IP=localhost bash /tmp/remote-verify.sh"

# Clean up
ssh "m@${VM_IP}" "rm -f /tmp/remote-verify.sh" 2>/dev/null || true

echo "[dev] Remote verification completed."
