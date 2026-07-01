#!/usr/bin/env bash
# sa.sh - Start an AI CLI agent for this project with .env loaded.
# Usage: ./sa.sh {oc|opencode|claude-kimi|cki|claude-zai|cza|qwen} [args...]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${ENV_FILE}"
  set +a
fi

AGENT="${1:-}"
shift || true

case "${AGENT}" in
  oc|opencode)
    exec opencode "$@"
    ;;
  claude-kimi|cki)
    CKI="$(command -v cki.sh 2>/dev/null || echo '/Volumes/ExMac/code/cki.sh')"
    if [[ ! -x "${CKI}" ]]; then
      echo "Error: cki.sh not found or not executable at ${CKI}" >&2
      exit 1
    fi
    exec "${CKI}" "$@"
    ;;
  claude-zai|cza)
    CZA="$(command -v cza.sh 2>/dev/null || echo '/Volumes/ExMac/code/cza.sh')"
    if [[ ! -x "${CZA}" ]]; then
      echo "Error: cza.sh not found or not executable at ${CZA}" >&2
      exit 1
    fi
    exec "${CZA}" "$@"
    ;;
  qwen)
    exec qwen "$@"
    ;;
  "")
    echo "Usage: $0 {oc|opencode|claude-kimi|cki|claude-zai|cza|qwen} [args...]" >&2
    exit 1
    ;;
  *)
    echo "Unknown agent: ${AGENT}" >&2
    echo "Usage: $0 {oc|opencode|claude-kimi|cki|claude-zai|cza|qwen} [args...]" >&2
    exit 1
    ;;
esac
