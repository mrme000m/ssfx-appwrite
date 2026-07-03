# Integration & Demo Trial Readiness Assessment

> Date: 2026-07-03 | Based on analysis of ARCHITECTURE.md, AUTHENTICATION_ARCHITECTURE.md, NAMING_AND_CONSOLIDATION_OVERHAUL.md, and actual codebase inspection.

---

## 1. Executive Summary

The system is **architecturally sound and substantially implemented**. All critical components — auth functions, cTrader Open API client, trading executor, account hub, and CI/CD — exist as working code. The documentation accurately reflects the implementation. The system is **ready for a demo-account trial** after addressing a small number of pre-trial gaps (§5). No fundamental rewrites or architectural changes are needed.

---

## 2. Documentation Coherence

### 2.1. Cross-document consistency: GOOD

| Aspect | Status |
|--------|--------|
| Auth flow (3 paths: OAuth, PIN, server-to-server) | Consistent across all 3 docs |
| Function names and responsibilities | Match actual code (`functions.json` ↔ docs) |
| TablesDB tables (12 tables in `ctrader_auth`) | Match `_shared/index.js` references |
| Token lifecycle (access 1h, refresh 30d, state 10min, PIN reset 15min) | Consistent |
| Security model (AES-GCM-256, BCrypt, HMAC state, grant locks) | Matches `_shared/index.js` implementation |
| Endpoint paths | Match actual route handlers in function `main.js` files |

### 2.2. Documentation gaps

| Gap | Impact | Recommendation |
|-----|--------|----------------|
| `AUTHENTICATION_ARCHITECTURE.md` security checklist uses `[ ]` (unchecked) | Cosmetic, but implies unverified | Verify each item and mark `[x]` |
| `ARCHITECTURE.md` §2.5.1 lists `ssfx_presets` / `ssfx_risk_state` tables but no schema file exists in `appwrite/` | Schema drift risk | Add table definitions to `appwrite/functions.json` or a schema file |
| `appwrite.config.json` reports `functions: 0, sites: 0, databases: 0` | CLI may not deploy correctly | Reconcile with `appwrite/functions.json` (which has 4 functions) |
| Deprecated sites (`ctrader-auth-site`, `ctrader-command-center`) still in working tree | Confusion, deploy noise | Delete per NAMING_AND_CONSOLIDATION_OVERHAUL.md Phase 0 |

---

## 3. Integration Assessment

### 3.1. Auth Layer → Python Services: WELL INTEGRATED ✅

```
Functions (_shared/index.js)          Python (ctrader_client/)
  encrypt/decrypt (AES-GCM-256)  ←──→  appwrite_auth.py (210 lines)
  refreshCtraderToken            ←──→  Token refresh via /internal/ctrader/refresh
  grant_locks (row-level)        ←──→  x-internal-key header validation
  TablesDB (CTRADER_AUTH_DATABASE_ID) ←→ ctrader/config.py (same env var)
```

- `ctrader/config.py` references `CTRADER_AUTH_BROKER_URL`, `INTERNAL_API_KEY`, `CTRADER_AUTH_DATABASE_ID` — all match the function-side variables.
- `ctrader_client/appwrite_auth.py` correctly calls the internal refresh endpoint and never receives refresh tokens.
- `v2.env.example` documents all required env vars for the Python side.

### 3.2. cTrader Open API Client: FULLY IMPLEMENTED ✅

| Component | File | Lines | ProtoOA Messages |
|-----------|------|-------|-----------------|
| Protocol client | `ctrader_client/protocol.py` | 294 | ProtoOA* |
| Trading execution | `ctrader_client/execution.py` | 400 | ProtoOANewOrderReq, ProtoOAClosePositionReq, ProtoOAAmendPositionSLTPReq, ProtoOACancelOrderReq, ProtoOAOrderType, ProtoOATradeSide, ProtoOATimeInForce |
| Session management | `ctrader_client/session.py` | 575 | ProtoOA* |
| Market data | `ctrader_client/market_data.py` | 447 | ProtoOA* |
| Transport (TCP/WS) | `ctrader_client/transport.py` | 241 | — |
| Live connection | `ctrader/env_connection.py` | 553 | Connects to `demo.ctraderapi.com` and `live.ctraderapi.com` |

**Dependency**: `ctrader-open-api>=0.9` is declared in `pyproject.toml`.

### 3.3. Trading Executor: FULLY IMPLEMENTED ✅

`ssfx_trader/executor.py` (1260 lines, 45 functions):
- Signal execution pipeline: `execute_signal` → `_execute_new_signal` / `_execute_follow_up_signal`
- Position management: `_close_position`, `_amend_position_sltp`, `_handle_tp_hit`, `_handle_sl_hit`
- Risk controls: `_check_market_context`, `_estimate_open_risk_pct`, `_can_open_new_position_locked`, `RiskMonitor`
- Agent integration: `AgentHarnessClient` for entry decisions and lifecycle planning
- Reconciliation: `reconcile_positions`, `check_stale_positions`, `rebuild_state`

**Backend abstraction** (`ssfx_trader/backends/`):
- `base.py`: Clean interface — `connect`, `open_position`, `close_position`, `amend_position_sltp`, `cancel_order`, `get_open_position_ids`
- `ctrader.py` (412 lines): Real cTrader backend using `CTraderSession` from `ctrader_client`
- `simulated.py` (112 lines): In-memory simulated backend for safe testing

### 3.4. Account Hub: IMPLEMENTED ✅

- `ctrader/account_hub_v2.py` (375 lines): Two-transport model (live + demo)
- `ctrader/account_hub.py` (509 lines): Full account hub with WebSocket fan-out
- `ctrader/env_connection.py` (553 lines): Per-environment TCP connections, token refresh loop, execution event handlers, reconciliation
- `ctrader/account_events_persister.py` (175 lines): Writes account state to TablesDB
- `ctrader/account_discovery.py` (199 lines): Discovers trading accounts from cTrader

### 3.5. Intelligence Plane: IMPLEMENTED ✅

- `agent_harness/` (17 .py files): Signal intent, entry decisions, lifecycle planning
- `pplx-agent/` (separate package): Gold market research
- Kill-switches via `AGENT_*_ENABLED` env vars
- Degrades to deterministic rules on LLM failure

### 3.6. CI/CD: IMPLEMENTED ✅

GitHub Actions workflow (`.github/workflows/deploy.yml`, 127 lines):
- Jobs: `deploy-tables` → `deploy-functions` + `deploy-site` (parallel) → `verify-domains` → `smoke-test` → `cleanup`
- Functions deployed via `appwrite functions create-deployment` + `activate`
- Secrets via `dev.sh setup-gh-secrets`

---

## 4. Better Integration Opportunities

### 4.1. Add health endpoints to Functions (HIGH PRIORITY)

None of the 4 Appwrite Functions have a `/health` endpoint. The CI smoke-test job cannot verify function health without them.

**Action**: Add `GET /health` → `200 {"status":"ok"}` to each function's route handler.

### 4.2. Reconcile `appwrite.config.json`

The config reports 0 functions/sites/databases, but `appwrite/functions.json` defines 4 functions and `appwrite/sites.json` defines sites. The CLI may use `functions.json` as source of truth, but this inconsistency risks deployment failures.

**Action**: Run `appwrite init project` to regenerate, or manually sync `appwrite.config.json`.

### 4.3. End-to-end integration test

The existing `integration_test.py` (328 lines) tests individual APIs (dataservice, ctrader, websocket) but not the full auth → grant → token refresh → trade execution flow.

**Action**: Add a test that:
1. Calls `/auth/ctrader/start` → verifies state token in `ephemeral_tokens`
2. Simulates callback → verifies `slave_accounts` row creation
3. Calls `/internal/ctrader/refresh` → verifies access token returned
4. Uses simulated backend to place/close a position
5. Verifies `account_events` and `ctrader_trading_events` rows

### 4.4. Naming overhaul: defer until after demo trial

The `NAMING_AND_CONSOLIDATION_OVERHAUL.md` proposes significant renames (database, tables, functions, hostnames). These are well-reasoned but high-risk. **Do not attempt before demo trial.** Run the trial with current names, then execute the migration as a structured project.

### 4.5. Adopt Appwrite Realtime for SPA dashboards

Currently the SPA polls for account updates. `ARCHITECTURE.md` §5.1 recommends Appwrite Realtime subscriptions on `account_events`. This would reduce latency and compute.

**Action**: Post-trial, add Realtime subscriptions in `ssfx-hq/js/` for `account_events` and `ctrader_trading_events`.

### 4.6. Replace `service_config.master_auth` with Appwrite Labels

The master role is stored as a config value. Appwrite Teams/Labels (`label:master`) would give built-in RBAC, audit, and membership UI.

**Action**: Post-trial, Phase 4 of the naming overhaul.

---

## 5. Demo Trial Readiness

### 5.1. Verdict: READY (with pre-trial checklist)

All code paths needed for a demo-account trial are implemented:
- OAuth consent → grant creation → encrypted token storage ✅
- PIN login → session cookie ✅
- Token refresh via internal API with grant locks ✅
- cTrader Open API connection to `demo.ctraderapi.com` ✅
- Position open/close/amend via `ctrader_client/execution.py` ✅
- Signal ingestion → intent classification → execution ✅
- Account state fan-out via WebSocket ✅
- Simulated backend for safe pre-live testing ✅

### 5.2. Pre-trial checklist (must do)

| # | Task | Command / Action | Effort |
|---|------|-----------------|--------|
| 1 | Commit uncommitted `admin_api.py` | `git add remote-services/ssfx_server/admin_api.py && git commit` | 1 min |
| 2 | Add `/health` to all 4 functions | Add route handler in each `main.js` | 15 min |
| 3 | Reconcile `appwrite.config.json` | `appwrite init project` or manual edit | 5 min |
| 4 | Run existing test suite | `cd remote-services && python -m pytest ssfx_trader/tests/ ssfx_server/tests/` | 5 min |
| 5 | Run function tests | `cd functions/_shared && node tests/test_getServiceConfig.mjs` | 1 min |
| 6 | Deploy to Appwrite | `./dev.sh deploy-auth` | 10 min |
| 7 | Verify smoke test passes | Check GitHub Actions tab | 2 min |
| 8 | Test simulated backend locally | Configure `ssfx_trader` to use `simulated` backend, send test signal | 30 min |
| 9 | Test OAuth flow with demo cTrader account | Click Connect in SPA → complete consent → verify dashboard | 15 min |
| 10 | Test token refresh | Call `/internal/ctrader/refresh` with internal key | 5 min |

### 5.3. Demo trial sequence

```
Phase A: Simulated (no real cTrader connection)
  1. Deploy auth layer + site
  2. Set up admin PIN (init-scripts/admin-pin.sh)
  3. Configure cTrader OAuth (init-scripts/ctrader-oauth.sh)
  4. Complete OAuth flow with a demo cTrader account
  5. Set username + PIN
  6. Run ssfx_trader with simulated backend
  7. Send test Telegram signal → verify simulated execution
  8. Verify account_events and executions in TablesDB

Phase B: Demo cTrader (real demo.ctraderapi.com connection)
  1. Switch ssfx_trader backend from `simulated` to `ctrader`
  2. Start account_hub → verify connection to demo.ctraderapi.com
  3. Verify account discovery populates `accounts` table
  4. Send test signal → verify real position on demo account
  5. Verify position appears in cTrader demo UI
  6. Test close/modify operations
  7. Monitor token refresh cycle

Phase C: Copy trading trial
  1. Configure master signal source (Telegram channel)
  2. Configure follower (ssfx_accounts table)
  3. Enable agent harness (optional, with kill-switches)
  4. Monitor signal → intent → execution → position → close cycle
  5. Verify execution history in ssfx_executions
```

### 5.4. Risk mitigations for trial

| Risk | Mitigation |
|------|------------|
| Token encryption key lost | Back up `TOKEN_ENCRYPTION_KEY` to Bitwarden before trial |
| Accidental live account trade | Use only demo cTrader accounts; verify `isLive: false` in `accounts` table |
| Signal spam | Start with `copy_enabled: false` on all followers; enable one at a time |
| Agent LLM costs | Keep `AGENT_*_ENABLED=false` initially; enable only `AGENT_INTENT_ENABLED` first |
| Token refresh failure | Monitor `ctrader-token-refresh-worker` logs; set up alerting |

---

## 6. Architecture vs Implementation Matrix

| Documented Component | Code Location | Lines | Status |
|---------------------|--------------|-------|--------|
| ctrader-auth function | `functions/ctrader-auth/src/main.js` | 791 | ✅ Implemented |
| ctrader-pin-auth function | `functions/ctrader-pin-auth/src/main.js` | 572 | ✅ Implemented |
| ctrader-internal function | `functions/ctrader-internal/src/main.js` | 428 | ✅ Implemented |
| token-refresh-worker | `functions/ctrader-token-refresh-worker/src/main.js` | 204 | ✅ Implemented |
| Shared crypto/auth utils | `functions/_shared/index.js` | 363 | ✅ Implemented |
| ssfx-hq SPA | `sites/ssfx-hq/` | — | ✅ Implemented |
| cTrader Open API client | `remote-services/ctrader_client/` | ~3400 | ✅ Implemented |
| Trading executor | `remote-services/ssfx_trader/executor.py` | 1260 | ✅ Implemented |
| cTrader backend | `remote-services/ssfx_trader/backends/ctrader.py` | 412 | ✅ Implemented |
| Simulated backend | `remote-services/ssfx_trader/backends/simulated.py` | 112 | ✅ Implemented |
| Account hub v2 | `remote-services/ctrader/account_hub_v2.py` | 375 | ✅ Implemented |
| Env connection | `remote-services/ctrader/env_connection.py` | 553 | ✅ Implemented |
| Agent harness | `remote-services/agent_harness/` | 17 files | ✅ Implemented |
| Market data service | `remote-services/market_data_service/` | 45 files | ✅ Implemented |
| CI/CD pipeline | `.github/workflows/deploy.yml` | 127 | ✅ Implemented |
| Health endpoints | — | — | ❌ Missing |
| E2E integration test | `remote-services/integration_test.py` | 328 | ⚠️ Partial |
| TablesDB schema file | `appwrite/tables.json` | — | ❌ Missing |
| Function unit tests | `functions/_shared/tests/` | 1 file | ⚠️ Minimal |

---

## 7. Conclusion

The three architecture documents form a coherent, well-structured design that accurately reflects the implemented codebase. The system is **ready for a demo-account trial** after completing the 10-item pre-trial checklist (§5.2). The naming overhaul (§4.4) and Appwrite Cloud primitive adoption (§4.5, §4.6) are post-trial improvements that should not block the initial validation.

**Recommended next step**: Execute the pre-trial checklist, then run Phase A (simulated) to validate the full pipeline without financial risk.
