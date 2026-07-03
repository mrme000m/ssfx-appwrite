#!/usr/bin/env bash
# dev/scripts/init.sh — Run all third-party service init scripts.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INIT_DIR="$(cd "${SCRIPT_DIR}/../../init-scripts" && pwd)"

echo "[dev] Running init scripts from ${INIT_DIR}..."

run_py() {
  python3 "$1"
}

# Run shell wrappers first; they often delegate to Python scripts.
for script in "${INIT_DIR}"/*.sh; do
  [[ -f "${script}" ]] || continue
  name="$(basename "${script}")"
  if [[ "${name}" == _* || "${name}" == TEMPLATE* ]]; then
    continue
  fi
  echo "[dev] Running ${name}..."
  "${script}"
done

# Run standalone Python scripts only if no shell wrapper exists.
for script in "${INIT_DIR}"/*.py; do
  [[ -f "${script}" ]] || continue
  name="$(basename "${script}")"
  base="${name%.*}"
  if [[ "${name}" == _* || "${name}" == TEMPLATE* ]]; then
    continue
  fi
  if [[ -f "${INIT_DIR}/${base}.sh" ]]; then
    continue
  fi
  echo "[dev] Running ${name}..."
  run_py "${script}"
done

echo "[dev] All init scripts completed."
