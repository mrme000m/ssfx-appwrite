#!/usr/bin/env bash
# dev/scripts/deploy-auth.sh — Deploy cTrader auth functions + site + callback proxy
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

echo "[deploy-auth] Pushing tables, functions, and sites..."

# 1. Push config (tables + functions + sites)
#    Tables must exist before functions can write to them.
cd "${PROJECT_ROOT}"

echo "[deploy-auth] Pushing TablesDB config..."
appwrite push tables --all --force

echo "[deploy-auth] Pushing Functions (with variables)..."
appwrite push functions --all --force --with-variables

echo "[deploy-auth] Pushing Site (with variables)..."
appwrite push sites --all --force --with-variables

echo "[deploy-auth] Appwrite deployment complete."

# 2. Update callback proxy if available
SETUP_SCRIPT="${PROJECT_ROOT}/dev/scripts/setup-callback-domain.py"
if [[ -x "${SETUP_SCRIPT}" ]]; then
  echo "[deploy-auth] Updating callback proxy environment variables..."
  if python3 "${SETUP_SCRIPT}" --update-only; then
    echo "[deploy-auth] Callback proxy updated."
  else
    echo "[deploy-auth] Warning: callback proxy update failed (check CF_API_TOKEN)."
  fi
else
  echo "[deploy-auth] Note: setup-callback-domain.py not found, skipping proxy update."
fi

echo ""
echo "[deploy-auth] Done."
echo ""
echo "Stable callback URL: https://auth.mrme.tech/callback"
echo ""
echo "Next steps:"
echo "  1. Register the stable callback in cTrader Open API portal:"
echo "     https://openapi.ctrader.com"
echo "     Callback URL: https://auth.mrme.tech/callback"
echo "  2. Ensure Cloudflare Worker Custom Domain 'auth.mrme.tech' is active"
echo "  3. Run ./dev.sh init to set up master PIN and cTrader config"
