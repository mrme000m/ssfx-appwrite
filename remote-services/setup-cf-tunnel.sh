#!/usr/bin/env bash
# Cloudflare tunnel ingress update for remote-services on Azure VM.
# Fetches the current tunnel config, replaces with clean ingress rules for
# the Docker-based deployment, and pushes the updated config back to Cloudflare.
#
# Usage:
#   cd /Volumes/ExMac/code/ssfx/appwrite-auth/remote-services
#   ./setup-cf-tunnel.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../.env" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Configuration (from AGENTS.md / .env)
# ---------------------------------------------------------------------------
CF_ACCOUNT_ID="${CF_ACCOUNT_ID:-4f6d43db5dbe773f750a2c8f941d0cdc}"
CF_ZONE_ID="${CF_ZONE_ID:-5290d99f626b08c46c1eca6cc7cfa090}"
TUNNEL_ID="${TUNNEL_ID:-d1e96e86-a44a-457a-a60c-e7d5d5d675bd}"
TUNNEL_NAME="${TUNNEL_NAME:-ssfx_azurue}"

if [[ -z "${CF_API_TOKEN:-}" ]]; then
  echo "ERROR: CF_API_TOKEN not set. Source .env or export it."
  exit 1
fi

API_BASE="https://api.cloudflare.com/client/v4"
AUTH_HDR="Authorization: Bearer ${CF_API_TOKEN}"

# ---------------------------------------------------------------------------
# Helper: CF API call with jq parsing
# ---------------------------------------------------------------------------
cf_api() {
  local method="$1"
  local path="$2"
  local data="${3:-}"
  local url="${API_BASE}${path}"

  if [[ -n "${data}" ]]; then
    curl -s -X "${method}" "${url}" \
      -H "${AUTH_HDR}" \
      -H "Content-Type: application/json" \
      -d "${data}"
  else
    curl -s -X "${method}" "${url}" \
      -H "${AUTH_HDR}"
  fi
}

# ---------------------------------------------------------------------------
# Build clean ingress list for Docker-based deployment
# ---------------------------------------------------------------------------
read -r -d '' NEW_INGRESS_JSON <<'EOF' || true
[
  { "hostname": "ssfx-api.mrme.tech",   "service": "http://localhost:8000" },
  { "hostname": "ds-control.mrme.tech", "service": "http://localhost:9000" },
  { "hostname": "ds-sse.mrme.tech",     "service": "http://localhost:9001" },
  { "hostname": "dataservice.mrme.tech","service": "http://localhost:9002" },
  { "hostname": "agent.mrme.tech",      "service": "http://localhost:9003" },
  { "hostname": "pplx-agent.mrme.tech", "service": "http://localhost:9004" },
  { "hostname": "ctrader.mrme.tech",    "service": "http://localhost:9300" },
  { "hostname": "account-hub.mrme.tech","service": "http://localhost:9301" },
  { "hostname": "admin.mrme.tech",      "service": "http://ubuntu-server:8100" },
  { "service": "http_status:404" }
]
EOF

echo "[cf-tunnel] Proposed ingress rules:"
echo "${NEW_INGRESS_JSON}" | jq .

# ---------------------------------------------------------------------------
# Push updated config
# ---------------------------------------------------------------------------
echo "[cf-tunnel] Updating tunnel config ..."
PATCH_PAYLOAD=$(jq -n --argjson ingress "${NEW_INGRESS_JSON}" '{config: {ingress: $ingress}}')
RESPONSE=$(cf_api PUT "/accounts/${CF_ACCOUNT_ID}/cfd_tunnel/${TUNNEL_ID}/configurations" "${PATCH_PAYLOAD}")

if [[ $(echo "${RESPONSE}" | jq -r '.success') != "true" ]]; then
  echo "ERROR: Failed to update tunnel config:"
  echo "${RESPONSE}" | jq .
  exit 1
fi

echo "[cf-tunnel] Tunnel config updated successfully."

# ---------------------------------------------------------------------------
# Ensure DNS records exist (CNAME -> tunnel)
# ---------------------------------------------------------------------------
echo "[cf-tunnel] Ensuring DNS CNAME records ..."
TUNNEL_CNAME="${TUNNEL_ID}.cfargotunnel.com"

for host in ssfx-api ds-control ds-sse dataservice agent pplx-agent ctrader account-hub admin; do
  RECORD="${host}.mrme.tech"
  
  # Check if record exists
  EXISTING=$(curl -s "${API_BASE}/zones/${CF_ZONE_ID}/dns_records?name=${RECORD}" \
    -H "${AUTH_HDR}")
  
  if [[ $(echo "${EXISTING}" | jq '.result | length') -gt 0 ]]; then
    echo "  ✓ ${RECORD} already exists"
    continue
  fi

  echo "  → Creating ${RECORD} -> ${TUNNEL_CNAME} ..."
  CREATE_RESP=$(curl -s -X POST "${API_BASE}/zones/${CF_ZONE_ID}/dns_records" \
    -H "${AUTH_HDR}" \
    -H "Content-Type: application/json" \
    -d "{
      \"type\": \"CNAME\",
      \"name\": \"${RECORD}\",
      \"content\": \"${TUNNEL_CNAME}\",
      \"ttl\": 1,
      \"proxied\": true
    }")

  if [[ $(echo "${CREATE_RESP}" | jq -r '.success') == "true" ]]; then
    echo "    ✓ Created"
  else
    echo "    ✗ Failed: $(echo "${CREATE_RESP}" | jq -r '.errors[0].message // "unknown error"')"
  fi
done

echo "[cf-tunnel] Done. It may take 30-60s for changes to propagate."
echo ""
echo "Current tunnel ingress:"
echo "  ssfx-api.mrme.tech     -> http://localhost:8000   (Telegram webhook / admin)"
echo "  ds-control.mrme.tech   -> http://localhost:9000   (Market data control API)"
echo "  ds-sse.mrme.tech       -> http://localhost:9001   (MCP SSE live prices)"
echo "  dataservice.mrme.tech  -> http://localhost:9002   (Market Data REST API)"
echo "  agent.mrme.tech        -> http://localhost:9003   (AI agent harness)"
echo "  pplx-agent.mrme.tech   -> http://localhost:9004   (PPLX research agent)"
echo "  ctrader.mrme.tech      -> http://localhost:9300   (cTrader unified service)"
echo "  account-hub.mrme.tech  -> http://localhost:9301   (Account hub WebSocket)"
echo "  admin.mrme.tech        -> http://ubuntu-server:8100 (Admin panel)"
echo "  catch-all              -> http_status:404"
