#!/usr/bin/env bash
# Thin wrapper around setup_vm.py for users who prefer shell entrypoints.
# See setup_vm.py for full documentation and environment variables.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/setup_vm.py" "$@"
