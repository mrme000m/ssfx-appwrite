#!/usr/bin/env bash
# dev.sh — Dispatcher for slwp development operations.
# Usage: ./dev.sh <command> [args...]
#
# Commands are implemented as repeatable scripts in dev/scripts/.
# Complex or JSON-heavy scripts are written in Python; simple wrappers may be shell.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
SCRIPTS_DIR="${SCRIPT_DIR}/dev/scripts"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${ENV_FILE}"
  set +a
fi

COMMAND="${1:-}"
shift || true

if [[ -z "${COMMAND}" ]]; then
  echo "Usage: $0 <command> [args...]" >&2
  echo "" >&2
  echo "Available commands:" >&2
  for script in "${SCRIPTS_DIR}"/*.sh "${SCRIPTS_DIR}"/*.py; do
    [[ -f "${script}" ]] || continue
    name="$(basename "${script}")"
    # Strip extension (.sh or .py)
    name="${name%.*}"
    # Skip private helpers (files starting with _)
    if [[ "${name}" == _* ]]; then
      continue
    fi
    # Display user-friendly hyphenated command names.
    echo "  ${name//_/-}" >&2
  done
  exit 1
fi

# Commands are hyphenated in usage (cf-tunnel-status) but Python modules use underscores.
# Try the literal name first, then the underscore variant, for both .py and .sh.
SCRIPT=""
for base in "${COMMAND}" "${COMMAND//-/_}"; do
  for ext in py sh; do
    candidate="${SCRIPTS_DIR}/${base}.${ext}"
    if [[ -x "${candidate}" ]]; then
      SCRIPT="${candidate}"
      break 2
    fi
  done
done

if [[ -z "${SCRIPT}" ]]; then
  echo "Error: no script for command '${COMMAND}' in ${SCRIPTS_DIR}" >&2
  echo "Add dev/scripts/${COMMAND//-/_}.py (preferred for complex logic) or dev/scripts/${COMMAND}.sh and make it executable." >&2
  exit 1
fi

exec "${SCRIPT}" "$@"
