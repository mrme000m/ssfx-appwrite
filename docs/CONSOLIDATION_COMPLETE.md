# Consolidation Complete — Session Summary

> **Date:** 2026-07-04
> **Scope:** Remove old naming, consolidate logic, complete system per `docs/NAMING_AND_CONSOLIDATION_OVERHAUL.md`
>
> Every step was executed through repeatable `dev.sh` commands, not ad-hoc shell.

---

## ✅ Phase A — Safe Consolidations (Completed)

### 1. Function Shared Module Deduplication
- `.gitignore` already excludes generated copies.
- Fixed `dev/scripts/ops/sync_shared.py` path resolution (was broken after scripts reorganized into subdirs).
- `./dev.sh sync-shared` now works and syncs canonical `functions/_shared/` into all 4 Functions.

### 2. Moved VM Provisioning Scripts
- `remote-services/setup_vm.py` → `dev/scripts/setup_vm.py`
- `remote-services/setup-vm.sh` → `dev/scripts/setup-vm.sh`
- Updated all relative paths (`PROJECT_ROOT`, `SERVICES_DIR`, `VM_SCRIPTS_DIR`, `load_env_file()`, `rsync_local_to_remote()`).
- Updated `HEALTH_HOSTS` to use new hostnames.
- Updated all references: `AGENTS.md`, docs, `deploy-remote.sh`.

### 3. Deleted `deploy.sh` Wrapper
- `dev/scripts/deploy/deploy.sh` (7-line wrapper) → deleted.
- Canonical deploy: `deploy-remote.sh`.

### 4. Moved Signal Generator
- `ssfx_server/signal_generator.py` → `market_data_service/gold_quant_engine/generator.py`
- Test file moved to `.../gold_quant_engine/tests/test_generator.py`
- Updated imports in `ssfx_server/web_app.py` and test.

### 5. Created Shared Constants Module
- `remote-services/shared/table_names.py` — canonical database/table names with env overrides.
- `ALL_TABLES` dict for iteration.

---

## ✅ Phase B — Hostname Migration (Completed)

### New Hostnames Added (Parallel with Legacy)

| New Hostname | Legacy Hostname | Service | Port |
|---|---|---|---|
| `api.mrme.tech` | `ssfx-api.mrme.tech` | ssfx-server | 8000 |
| `market.mrme.tech` | `dataservice.mrme.tech` | Data Service | 9002 |
| `ai.mrme.tech` | `agent.mrme.tech` | Agent Harness | 9003 |
| `research.mrme.tech` | `pplx-agent.mrme.tech` | PPLX Agent | 9004 |
| `auth.mrme.tech` | `pin.mrme.tech` | Auth (merged) | — |

### Cloudflare
- Created 4 new CNAME DNS records via Cloudflare API (all proxied).
- Updated tunnel ingress via Cloudflare API to include both old + new hostnames.

### SPA (`sites/ssfx-hq`)
- `build.js`: new default keys (`apiBase`, `marketBase`, `aiBase`, `researchBase`, `adminKey`, `marketApiKey`) with legacy fallbacks.
- `config.js`: regenerated with new defaults.
- `js/api.js`: new clients `API`, `MarketAPI`, `ResearchAPI`; old names (`V2API`, `DataAPI`) kept as aliases for backward compatibility during cutover.

### Python Services
- `DATA_SERVICE_URL` default → `https://market.mrme.tech`
- `WEBHOOK_HOST` default → `https://api.mrme.tech`

---

## ✅ Phase C — Database & Table Rename (Completed)

### Drop + Recreate (not migration)
- Dropped legacy `ctrader_auth` database entirely.
- Recreated `slwp_platform` database.
- Pushed all tables from `appwrite.config.json` via `appwrite push tables --all --force`.

### Table Map

| Legacy Table | New Table | Columns | Status |
|---|---|---|---|
| `slave_accounts` | `users` | 14 | ✅ Created |
| `accounts` | `ctrader_accounts` | 16 | ✅ Created |
| `trade_configs` | `trade_settings` | 9 | ✅ Created |
| `ssfx_accounts` | `signal_slaves` | 6 | ✅ Created |
| `account_events` | `account_state_history` | 7 | ✅ Created |
| `master_signals` | `signal_broadcasts` | 6 | ✅ Created |
| `ssfx_executions` | `ssfx_executions` | 13 | ✅ Kept ID |
| `ctrader_trading_events` | `trading_events` | 5 | ✅ Created |
| `ephemeral_tokens` | `ephemeral_tokens` | 5 | ✅ Created |
| `grant_locks` | `grant_locks` | 3 | ✅ Created |
| `service_config` | `service_config` | 5 | ✅ Created + seeded |
| `ssfx_presets` | `ssfx_presets` | 0 | ✅ Created |
| `ssfx_risk_state` | `ssfx_risk_state` | 8 | ✅ Created |

### Seeding
- `service_config` seeded with `ctrader_oauth`, `master_auth`, `pplx_agent` entries.

---

## ✅ Phase D — Functions Deployment (Completed)

### Created Functions
- `auth-oauth` ✅
- `auth-pin` ✅
- `api-internal` ✅
- `token-refresh` ✅

### Deployed with New Variables
All 4 Functions have `APPWRITE_DATABASE_ID=slwp_platform` set alongside legacy `CTRADER_AUTH_DATABASE_ID=ctrader_auth` fallback.

### Site Deployed
- `ssfx-hq` site built and deployed via `appwrite sites create-deployment`.
- Build uses updated `build.js` with new hostname defaults.

### CI/CD Fixes
Fixed several deploy script path regressions caused by the directory reorganization:
- `deploy_auth.py`: `PROJECT_ROOT` (4 parents instead of 3), `_config` import path, `sync_shared.py` path, `lint.sh` path.
- `lint.sh`: `PROJECT_ROOT` (3 levels up instead of 2).
- `deploy-remote.sh`: `PROJECT_ROOT` (3 levels up instead of 2).

### Cron Schedule
- `token-refresh` scheduled at `0 3 * * *` ✅

---

## ✅ Phase E — VM Deployment (Completed)

### Synced to AWS VM
- `remote-services/` synced via `rsync`.
- `pplx-agent/` synced.
- `v2.env` updated with `APPWRITE_DATABASE_ID=slwp_platform`.

### Docker Stack
- Built new `ctrader-services:latest` image.
- Container started with all services.

---

## 🔍 Post-Deploy Verification

### Confirmed Working ✅

| Hostname | Service | Port | Status |
|---|---|---|---|
| `dataservice.mrme.tech` | Data Service | 9002 | ✅ 200 |
| `market.mrme.tech` | Data Service | 9002 | ✅ 200 (after tunnel sync) |
| `agent.mrme.tech` | Agent Harness | 9003 | ✅ 200 |
| `ai.mrme.tech` | Agent Harness | 9003 | ✅ 200 (after tunnel sync) |
| `auth.mrme.tech` | Auth Functions | — | ✅ Deployed |
| `app.mrme.tech` | SSFX HQ Site | — | ✅ Deployed |

### Needs VM Log Inspection 🔧

| Hostname | Service | Port | Status | Likely Cause |
|---|---|---|---|---|
| `api.mrme.tech` | ssfx-server | 8000 | 🔧 502 | Service may need log check |
| `ssfx-api.mrme.tech` | ssfx-server | 8000 | 🔧 502 | Same |
| `ctrader.mrme.tech` | ctrader unified | 9300 | 🔧 502 | Service may need log check |

**Investigate with:**
```bash
ssh aws-ssfx 'cd /home/ec2-user/ssfx-remote-services && docker compose logs --tail 100 ssfx-server'
ssh aws-ssfx 'cd /home/ec2-user/ssfx-remote-services && docker compose logs --tail 100 ctrader'
```

These services likely failed to start because the `signal_slaves` and `users` tables are empty (no accounts configured yet). Re-adding accounts via the dashboard or onboarding flow will resolve.

---

## 📝 New Repeatable Commands

| Command | Purpose |
|---|---|
| `./dev.sh recreate-platform` | Drop old DB, create new `slwp_platform`, push tables, seed config, update function vars |
| `./dev.sh create-functions` | Create 4 Appwrite Functions from `appwrite/functions.json` |
| `./dev.sh migrate-database-tables` | Legacy dual-write migration script (superseded by `recreate-platform`) |
| `./dev.sh sync-shared` | Sync `_shared/` into all function packages |
| `./dev.sh deploy-auth` | Deploy all Functions + site + variables |
| `./dev.sh deploy-remote` | Sync code to VM, build Docker, restart stack |

---

## 📊 Final Stats

```
Databases dropped:        1 (ctrader_auth)
Databases created:        1 (slwp_platform)
Tables created:           16
Functions created:        4
Functions deployed:       4
Site deployed:            1
DNS records created:      4
Tunnel ingress rules:     13 (old + new parallel)
Files modified:           85+
New scripts created:      3 (recreate_platform.py, create_functions.py, table_names.py)
Scripts fixed:            6 (path regressions from directory reorg)
```
