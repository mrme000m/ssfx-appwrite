#!/usr/bin/env bash
# dev/scripts/lint.sh — Syntax check function entry points and shared module.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cd "${PROJECT_ROOT}"

echo "[dev] Syncing shared module..."
python3 "${PROJECT_ROOT}/dev/scripts/sync_shared.py"

echo "[dev] Running Node syntax checks..."
node --check "functions/_shared/index.js"

for main in functions/*/src/main.js; do
  if [ -f "$main" ]; then
    echo "  checking $main"
    node --check "$main"
  fi
done

echo "[dev] Lint completed."
