#!/usr/bin/env bash
# install-cloudflared.sh — Install cloudflared and register the tunnel service.
#
# Run on the target VM by setup_vm.py.
# Reads the tunnel token from /tmp/setup-vm-tunnel-token.env (written by the
# orchestrator) so the secret is never visible in the remote process list.

set -euo pipefail

OS_FAMILY="${VM_OS_FAMILY:-amazonlinux}"
TOKEN_FILE="/tmp/setup-vm-tunnel-token.env"

if [[ -f "${TOKEN_FILE}" ]]; then
  # shellcheck source=/dev/null
  source "${TOKEN_FILE}"
  rm -f "${TOKEN_FILE}"
fi

TUNNEL_TOKEN="${TUNNEL_TOKEN:-}"

if [[ -z "${TUNNEL_TOKEN}" ]]; then
  echo "ERROR: TUNNEL_TOKEN is not set." >&2
  exit 1
fi

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "[setup-vm] Installing cloudflared ..."
  curl -sSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-x86_64.rpm" \
    -o /tmp/cloudflared.rpm
  if [[ "${OS_FAMILY}" == "ubuntu" ]]; then
    # Convert rpm to a usable install on Debian-family by extracting the binary
    # (cloudflared does not publish a .deb for every release; the binary is static)
    sudo rpm2cpio /tmp/cloudflared.rpm | cpio -idmv -D /
  else
    sudo yum localinstall -y /tmp/cloudflared.rpm
  fi
  rm -f /tmp/cloudflared.rpm
else
  echo "[setup-vm] cloudflared already installed"
fi

if ! systemctl is-active --quiet cloudflared 2>/dev/null; then
  echo "[setup-vm] Registering cloudflared service ..."
  sudo cloudflared service install "${TUNNEL_TOKEN}"
  sudo systemctl enable --now cloudflared
else
  echo "[setup-vm] cloudflared service already running"
fi

echo "[setup-vm] cloudflared status:"
sudo systemctl status cloudflared --no-pager
