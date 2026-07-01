#!/usr/bin/env bash
# dev/scripts/deploy-status.sh — Show Azure VM and deployment status.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VM_NAME="${AZURE_VM_NAME:-ubuntu-server}"
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-RG-UBUNTU-VM}"

echo "[dev] Azure VM status:"
az vm show \
  --name "${VM_NAME}" \
  --resource-group "${RESOURCE_GROUP}" \
  --show-details \
  --query "{Name:name, ResourceGroup:resourceGroup, Location:location, Size:hardwareProfile.vmSize, PowerState:powerState, PublicIp:publicIps, Fqdn:fqdns, ProvisioningState:provisioningState}" \
  --output table

echo "[dev] Cloudflare tunnel status:"
if [[ -n "${CF_API_TOKEN:-}" ]]; then
  "${SCRIPT_DIR}/cf_tunnel_status.py"
else
  echo "  CF_API_TOKEN not set; skipping tunnel status." >&2
fi

echo ""
echo "[dev] Deployment status:"
# TODO: add commands to query deployed service status on the VM.
echo "[dev] Deployment status check completed."
