#!/usr/bin/env bash
# init-scripts/admin-pin.sh — Thin wrapper for admin-pin.py
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/admin-pin.py"
