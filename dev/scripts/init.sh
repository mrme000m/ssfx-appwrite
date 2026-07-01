#!/usr/bin/env bash
# dev/scripts/init.sh — Run all third-party service init scripts.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INIT_DIR="$(cd "${SCRIPT_DIR}/../../init-scripts" && pwd)"

echo "[dev] Running init scripts from ${INIT_DIR}..."

for script in "${INIT_DIR}"/*.sh; do
  [[ -f "${script}" ]] || continue
  name="$(basename "${script}")"
  # Skip templates and helpers (files starting with _ or TEMPLATE).
  if [[ "${name}" == _* || "${name}" == TEMPLATE* ]]; then
    continue
  fi
  echo "[dev] Running ${name}..."
  "${script}"
done

echo "[dev] All init scripts completed."
