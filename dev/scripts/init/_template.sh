#!/usr/bin/env bash
# dev/scripts/init/_template.sh — Template for a third-party service init script.
# Copy to dev/scripts/init/<service>.sh and adapt.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/config.yml"

if [[ ! -f "${CONFIG_FILE}" ]]; then
  echo "Error: ${CONFIG_FILE} not found. Copy dev/scripts/init/config.example.yml to dev/scripts/init/config.yml and fill it in." >&2
  exit 1
fi

# TODO: parse the YAML values you need.
# Example using yq (install via brew/apt):
#   VALUE="$(yq '.example_service.api_key' "${CONFIG_FILE}")"
VALUE=""

if [[ -z "${VALUE}" || "${VALUE}" == "null" ]]; then
  echo "Error: required value is missing in ${CONFIG_FILE}" >&2
  exit 1
fi

# TODO: upsert the configuration into Appwrite Database.
# Example using appwrite CLI:
#   appwrite tables_db update-row \
#     --database-id "config" \
#     --table-id "third_party" \
#     --row-id "example_service" \
#     --data '{"api_key":"'"${VALUE}"'"}'

echo "[init] example_service configured successfully."
