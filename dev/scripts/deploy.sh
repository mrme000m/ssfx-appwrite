#!/usr/bin/env bash
# dev/scripts/deploy.sh — Deploy remote-services to the primary AWS VM.
# Thin wrapper around deploy-remote.sh for backward compatibility.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/deploy-remote.sh" aws "$@"
