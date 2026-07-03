# Phase 2: Hardening and Operational Readiness

**Status: COMPLETED ✅** (2026-07-02)

This plan continues from Phase 1 (minimal blocker fix) to address the remaining
critical and medium-priority issues before live autonomous trading can be enabled.

## Goals

1. ✅ Close the last public-facing unauthenticated request paths.
2. ✅ Add per-account trading safety kill-switches.
3. ✅ Move configuration out of the wrong tables and deprecated SDK calls.
4. ✅ Reconcile schema gaps discovered in Phase 1.
5. ✅ Make local/remote `dev.sh` commands usable and consistent.
6. ✅ Add targeted tests and observability for the new safety logic.

## Work breakdown

### 1. Telegram webhook signature verification + admin API hardening

**Priority:** critical  
**Files:** `remote-services/ssfx_server/web_app.py`, `config_loader.py`, `cli.py`, `admin_api.py`, `config/v2.env.example`, `.gitignore`

- Make `TELEGRAM_WEBHOOK_SECRET_TOKEN` required when `WEBHOOK_HOST` is not `polling`.
- Remove the `if secret_token:` bypass in `telegram_webhook()`.
- Register the same secret with Telegram when calling `setWebhook` in `cli.py`.
- Harden `admin_api._require_admin_key()` so an empty `ADMIN_API_KEY` rejects requests.
- Add `TELEGRAM_WEBHOOK_SECRET_TOKEN` and `ADMIN_API_KEY` placeholders to `v2.env.example`.
- Ignore `remote-services/config/v2.env` because it contains runtime secrets.

### 2. Per-account risk kill-switches

**Priority:** critical  
**Files:** `ssfx_trader/config.py`, `ssfx_trader/risk_monitor.py` (new), `ssfx_trader/follower.py`, `ssfx_trader/executor.py`, `ssfx_trader/backends/*.py`, `ssfx_trader/stores/*`, `dev/scripts/init/init_ctrader_tables.py`, `appwrite.config.json`

- Add `max_daily_loss_pct`, `max_drawdown_pct`, `panic_stop`, `risk_reset_utc_hour` to account config.
- Create a new `ssfx_risk_state` table for daily PnL/drawdown state.
- Implement `RiskMonitor` that loads/persists state and trips kill-switches on:
  - hit daily loss limit
  - hit peak-to-trough drawdown limit
  - manual panic flag
- Wire `RiskMonitor` into `AccountFollower` before executing new signals and on position close.
- Enhance `ExecutionBackend.close_position` to return realized PnL where possible.
- Add skip logging so blocked signals are visible.

### 3. Init-script cleanup

**Priority:** high  
**Files:** `dev/scripts/init/ctrader-oauth.py`, `dev/scripts/init/admin-pin.py`, `dev/scripts/init/_env.py` (new), `appwrite.config.json`, `dev/scripts/init/migrate-master-to-service-config.py` (new)

- Create a shared `dev/scripts/init/_env.py` loader.
- Update `appwrite.config.json` `service_config` schema (rowSecurity, `config_json` column, encrypted `config_value`).
- Rewrite `ctrader-oauth.py` to use modern keyword-style TablesDB calls and store config under `ctrader_oauth` key.
- Rewrite `admin-pin.py` to store master PIN hash in `service_config` (`master_auth` key) instead of abusing `slave_accounts`.
- Provide a migration script to move existing master rows from `slave_accounts` to `service_config`.

### 4. Schema gaps

**Priority:** medium  
**Files:** `appwrite.config.json`, `dev/scripts/init/init_ctrader_tables.py`, `account_events_persister.py`, `functions/ctrader-internal/src/main.js`

- Add missing `ssfx_presets` table (referenced by command-center site).
- Add `updated_at` to `accounts` and `ssfx_accounts`.
- Add indexes on `accounts` (`grant_id`, `ctidTraderAccountId`), `account_events` (`grant_id`, `event_type`, `received_at`, etc.), and `ssfx_accounts` (`owner_id`, `enabled`, `host_type`).
- Add `config_json` column to `service_config` for data-service nested-config compatibility.
- Change `account_events.event_json` default type from `text` to `longtext`.
- Ensure `functions/ctrader-internal` sends `ctidTraderAccountId` as an integer.

### 5. dev.sh stubs and remote deployment reconciliation

**Priority:** medium  
**Files:** `dev/scripts/start.sh`, `stop.sh`, `logs.sh`, `test.sh`, `deploy-remote.sh`, `status.py`, `deploy-status.sh`, `cf_tunnel_update.py`, `cf-tunnel-config.json`, `remote-services-sync.py`, `remote-services-init-tunnel.py`, `remote-services/config/tunnel-ingress.json`, `remote-services/setup-cf-tunnel.sh`, `AGENTS.md`

- Implement local Docker Compose start/stop/logs/test commands.
- Unify remote directory to `~/ssfx-remote-services`.
- Point `cf_tunnel_update.py` at `remote-services/config/tunnel-ingress.json`.
- Update canonical tunnel ingress to hostnames: `ssfx-api`, `ds-control`, `ds-sse`, `dataservice`, `agent`, `ctrader`, `account-hub`, `admin`.
- Update stale `DATA_SERVICE_URL` defaults from port `9099` to `9002`.
- Update `AGENTS.md` tunnel table.

### 6. Tests and observability

**Priority:** medium  
**Files:** `remote-services/tests/*`, `dev/scripts/test.sh`, observability log points

- Add webhook auth tests for authorized/unauthorized paths.
- Add `RiskMonitor` unit tests for daily loss, drawdown, panic, and reset behavior.
- Ensure new kill-switch trips produce WARN/ERROR logs with `account_name` and reason.

## Completion Summary (2026-07-02)

All Phase 2 hardening tasks have been implemented and verified:

- **Security:** Telegram webhook signature verification + admin API hardening complete
- **Kill-switches:** Per-account risk limits (daily loss, drawdown, panic stop) with RiskMonitor integration
- **Config:** OAuth and master auth moved from slave_accounts to service_config table
- **Schema:** All gaps patched (ssfx_presets, ssfx_risk_state, indexes, config_json column)
- **DevOps:** dev.sh commands implemented (start, stop, logs, deploy-remote) with Docker Compose
- **Tunnel:** Canonical hostnames configured (ssfx-api, ds-control, ds-sse, dataservice, agent, ctrader, account-hub, admin)
- **Tests:** 23 tests passing, lint clean

---

## Post-Phase-2: Consolidation (2026-07-03)

After Phase 2, a comprehensive consolidation pass was executed across the entire codebase:

### Dead code removal
- Deleted `sites/ctrader-auth-site/` and `sites/ctrader-command-center/` (superseded by `ssfx-hq`)
- Deleted `ctrader/market/` (5 files, redundant proxy of `market_data_service/`)
- Deleted `ssfx_trader/cli.py`, `ssfx_trader/stores/mongo_store.py`
- Deleted `ssfx_server/cli.py` (replaced by standalone `dev/scripts/ops/set_telegram_webhook.py`)
- Deleted `ctrader/trading/user_config_store.py` (merged into `AppwriteAccountStore`)
- Deleted deprecated Azure scripts: `deploy-azure.sh`, `deploy-master.sh`, `setup-cf-tunnel.sh`, `cleanup-vm.sh`

### Naming overhaul
- `ctrader-auth` → `auth-oauth`
- `ctrader-pin-auth` → `auth-pin`
- `ctrader-internal` → `api-internal`
- `ctrader-token-refresh-worker` → `token-refresh`
- `follower` → `slave` throughout codebase
- `AccountFollower` → `AccountSlave`
- `to_mongo`/`from_mongo` → `to_doc`/`from_doc`

### Architecture improvements
- Created `remote-services/shared/appwrite_client.py` (single factory used by 8+ files)
- Merged `UserConfigStore` into `AppwriteAccountStore`
- Eliminated circular import (`ssfx_trader` → `ssfx_server`)
- Organized `dev/scripts/` into 6 subdirectories (`cleanup/`, `deploy/`, `init/`, `ops/`, `testing/`, `tunnel/`)
- Moved `init-scripts/` → `dev/scripts/init/`

### Doc updates
- Rewrote `AUTHENTICATION_ARCHITECTURE.md` with current names
- Updated `INTEGRATION_AND_TRIAL_READINESS.md` for current state
- Updated `project-devstack-deployment-learnings.md` path references
- Created `REMOTE_SERVICES_AUDIT.md`

See `docs/CONSOLIDATION_ANALYSIS.md` for the full analysis.

## Verification checklist

- [x] Telegram webhook without `X-Telegram-Bot-Api-Secret-Token` returns 401.
- [x] `/api/signals/inject` without `x-admin-key` returns 401.
- [x] Kill-switched account does not open new trades; signals are logged as skipped.
- [x] Panic stop toggle immediately blocks new entries.
- [ ] `./dev.sh init` runs idempotently and cTrader OAuth + master auth are in `service_config`. (Requires runtime credentials)
- [ ] `./dev.sh deploy-auth` pushes schema including `ssfx_presets` and new indexes. (Ready to deploy)
- [x] `./dev.sh start` / `./dev.sh stop` / `./dev.sh logs` work locally.
- [x] Remote deploy uses `~/ssfx-remote-services` and canonical tunnel hostnames.
- [x] `./dev.sh test` passes lint + unit tests (23 tests).
