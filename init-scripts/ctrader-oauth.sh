#!/usr/bin/env bash
# init-scripts/ctrader-oauth.sh — Thin wrapper for ctrader-oauth.py
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/ctrader-oauth.py"
