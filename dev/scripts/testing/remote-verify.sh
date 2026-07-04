#!/usr/bin/env bash
# remote-services/remote-verify.sh — Remote verification test for VM deployment.
# Tests both localhost ports (VM internal) and public Cloudflare tunnel hostnames.
#
# Usage from local machine:
#   ssh m@<vm-ip> 'bash -s' < remote-services/remote-verify.sh
#
# Usage on VM:
#   cd ~/ssfx-remote-services && bash remote-verify.sh

set -euo pipefail

VM_IP="${VM_IP:-localhost}"
PASS_COUNT=0
FAIL_COUNT=0

check_http() {
    local name="$1"
    local url="$2"
    local expect_ok="${3:-true}"
    local t0
    t0=$(date +%s%N)
    
    local http_code
    local body
    http_code=$(curl -s -o /tmp/remote_test_body.json -w "%{http_code}" --max-time 10 "$url" 2>/dev/null || echo "000")
    body=$(cat /tmp/remote_test_body.json 2>/dev/null || echo "")
    local t1
    t1=$(date +%s%N)
    local dur_ms=$(( (t1 - t0) / 1000000 ))
    
    local ok=true
    if [[ "$http_code" == "000" ]]; then
        ok=false
    elif [[ "$expect_ok" == "true" && "$http_code" -ge 400 ]]; then
        ok=false
    fi
    
    if [[ "$ok" == "true" ]]; then
        echo "  ✓ $name -> status=$http_code (${dur_ms}ms) | ${body:0:80}"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        echo "  ✗ $name -> status=$http_code (${dur_ms}ms) | ${body:0:80}"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
}

echo "[remote-verify] ============================================"
echo "[remote-verify] PRIVATE PORT TESTS (VM localhost)"
echo "[remote-verify] ============================================"

# 1. Health checks on local ports
echo "[1/6] HTTP health checks on localhost..."
check_http "health/ctrader" "http://${VM_IP}:9300/health"
check_http "health/dataservice-api" "http://${VM_IP}:9002/health"
check_http "health/ssfx-server" "http://${VM_IP}:8000/health"
check_http "health/ctrader-ws" "http://${VM_IP}:9301/health"
echo ""

# 2. Data-service API tests
echo "[2/6] Data-service API tests..."
check_http "ds/symbols" "http://${VM_IP}:9002/symbols"
check_http "ds/stats" "http://${VM_IP}:9002/stats"
check_http "ds/feed/status" "http://${VM_IP}:9002/feed/status"
check_http "ds/version" "http://${VM_IP}:9002/version"
check_http "ds/config" "http://${VM_IP}:9002/config" "false"  # 401 without auth
echo ""

# 3. cTrader API tests
echo "[3/6] cTrader unified service tests..."
check_http "ct/market/health" "http://${VM_IP}:9300/market/health"
check_http "ct/trade/health" "http://${VM_IP}:9300/trade/health"
echo ""

# 4. Control API tests
echo "[4/6] Data-service control API tests..."
check_http "ctrl/feed/status" "http://${VM_IP}:9000/feed/status"
echo ""

# 5. WebSocket / SSE smoke tests
echo "[5/6] WebSocket / SSE smoke tests..."
check_http "sse/dataservice" "http://${VM_IP}:9001/" "false"
echo ""

echo "[remote-verify] ============================================"
echo "[remote-verify] PUBLIC TUNNEL TESTS (mrme.tech)"
echo "[remote-verify] ============================================"

# 6. Public hostnames via Cloudflare Tunnel
echo "[6/6] Public hostname health checks via Cloudflare Tunnel..."
check_http "pub/api" "https://api.mrme.tech/health"
check_http "pub/market" "https://market.mrme.tech/health"
check_http "pub/ds-sse" "https://ds-sse.mrme.tech/" "false"
check_http "pub/ds-control" "https://ds-control.mrme.tech/feed/status"
check_http "pub/ctrader" "https://ctrader.mrme.tech/health"
check_http "pub/account-hub" "https://account-hub.mrme.tech/health"
echo ""

echo "============================================================"
echo "REMOTE VERIFICATION TEST REPORT"
echo "============================================================"
echo "  TOTAL: $((PASS_COUNT + FAIL_COUNT))  |  PASS: ${PASS_COUNT}  |  FAIL: ${FAIL_COUNT}"
echo "============================================================"

if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    echo "WARNING: ${FAIL_COUNT} test(s) failed."
    exit 1
else
    echo "All tests passed."
    exit 0
fi
