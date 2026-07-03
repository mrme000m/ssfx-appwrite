#!/usr/bin/env bash
# Wrapper for mcp-server-appwrite that loads .env before launching
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${ENV_FILE}"
  set +a
fi

exec /Users/m/.local/bin/mcp-server-appwrite "$@"
