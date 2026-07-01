#!/usr/bin/env bash
# dev/scripts/deploy-auth.sh — Deploy cTrader auth functions + site
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

echo "[deploy-auth] Done."
echo ""
echo "Next steps:"
echo "  1. Get the ctrader-auth Function domain:"
echo "     appwrite functions get --function-id ctrader-auth"
echo "  2. Register the /callback domain in openapi.ctrader.com"
echo "  3. Update sites/ctrader-auth-site/config.js with deployed Function URLs"
echo "  4. Run ./dev.sh init to set up master PIN and cTrader config"
