#!/usr/bin/env bash
# dev/scripts/test.sh — Run the test suite.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

echo "[dev] Running tests..."

# Lint / syntax checks
"${SCRIPT_DIR}/lint.sh"

# Python unit tests for remote services
if command -v pytest >/dev/null 2>&1; then
    cd "${PROJECT_ROOT}/remote-services"
    pytest -q ssfx_trader/tests ssfx_server/tests agent_harness/tests market_data_service/gold_quant_engine/tests
    cd "${PROJECT_ROOT}"
else
    echo "[dev] pytest not found; skipping Python tests"
fi

# Node.js unit tests for functions
if command -v node >/dev/null 2>&1; then
    echo "[dev] Running Node.js tests..."
    cd "${PROJECT_ROOT}"
    
    # Run Node tests for shared functions
    if [ -f "functions/_shared/tests/test_getServiceConfig.mjs" ]; then
        node functions/_shared/tests/test_getServiceConfig.mjs
    fi
    
    cd "${PROJECT_ROOT}"
else
    echo "[dev] Node.js not found; skipping Node tests"
fi

echo "[dev] Tests completed."
