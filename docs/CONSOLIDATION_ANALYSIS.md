# Workspace Organization & Redundancy Analysis

> **Status: HISTORICAL —Actions Completed (2026-07-03)**
>
> This document records the analysis that drove the consolidation passes.
> All items marked with ✅ have been implemented. For the current architecture,
> see `docs/ARCHITECTURE.md`. For auth details, see `docs/AUTHENTICATION_ARCHITECTURE.md`.
>
> **Date of analysis:** 2026-07-03
> **Scope:** Comprehensive audit of `remote-services/` and workspace-wide naming/organization.

---

## 1. Executive Summary

The workspace has **six major categories of problems**:

| Category | Severity | Count |
|----------|----------|-------|
| **Redundant packages** — `ctrader/market/` duplicates `market_data_service/` | HIGH | 5 files |
| **Root-level script pollution** in `remote-services/` | HIGH | 12 files |
| **Duplicate Appwrite client patterns** across packages | MEDIUM | 3 different patterns |
| **Dual config readers** for `ssfx_accounts` | MEDIUM | 2 files |
| **`scripts/` vs `dev/scripts/` overlap** | MEDIUM | 2 dirs |
| **Package naming inconsistency** | LOW | 5 packages |

---

## 2. Redundant Packages

### 2.1. `ctrader/market/` — Complete shadow of `market_data_service/`

**The problem:** The `ctrader` unified service (port 9300) has a `market/` subpackage that consumes `market_data_service` (port 9002) via HTTP polling and re-exposes it via WebSocket. The `market_data_service` ALREADY has SSE streaming (`server_sse.py`) and a comprehensive REST API (`api_server.py`). This proxy layer adds latency, operational surface, and maintenance burden with zero unique value.

| File in `ctrader/market/` | What it does | Already exists in `market_data_service/` |
|---|---|---|
| `feed_client.py` | HTTP poll dataservice for price/context | `api_server.py` `/api/v1/market/*` endpoints |
| `hub.py` | WebSocket fan-out with backpressure | `server_sse.py` SSE streaming |
| `router.py` | REST + WS routes for market data | `api_server.py` REST routes |
| `context_engine.py` | Build agent context prompts | `gold_quant_engine/context_builder.py` |
| `models.py` | Tick/bar/dataclasses | `api_models.py`, `models.py` |

**Action:** Delete `ctrader/market/` entirely. Any WebSocket needs should be added to `market_data_service` directly. The `ctrader` service should focus on trading execution only.

### 2.2. `ContextEngine.build_prompt()` — AI prompt generation scattered

`ctrader/market/context_engine.py` generates prompts for AI agents. But the `agent_harness/` package has dedicated agents with their own prompt engineering (`signal_intent.py`, `entry_decision.py`, `lifecycle_planner.py`).

**Action:** Delete `ctrader/market/context_engine.py`. Prompt generation belongs in the Intelligence Plane (`agent_harness/`), not the Operations Plane (`ctrader/`).

### 2.3. `BadTradeDetector` — Duplicates risk monitoring

`ctrader/trading/bad_trade_detector.py` tracks "unannounced losses" and pauses symbols. But `ssfx_trader/risk_monitor.py` already has comprehensive per-account risk limits (`max_daily_loss_pct`, `max_drawdown_pct`, `max_open_risk_pct`, `panic_stop`). The `BadTradeDetector` heuristic (consecutive NEW signals without CLOSES) is fragile and untested.

**Action:** Remove `BadTradeDetector` from `ctrader/web_app.py` wiring. If the heuristic is valuable, port it to `ssfx_trader/risk_monitor.py` where risk logic is centralized.

### 2.4. `market_data_service/util/ctrader_import.py` — No-op legacy

`setup_ctrader_import_path()` is called by `feed_manager.py` and `data_ingestion.py` but does nothing in the unified container layout (it just checks that `ctrader_client` is importable, which it always is).

**Action:** Delete `util/ctrader_import.py` and remove imports. Inline the simple check if needed.

---

## 3. Root-Level Script Pollution in `remote-services/`

**Principle:** `remote-services/` should contain ONLY runtime service code and Docker config. Tools, provisioning, testing, and ad-hoc scripts should live elsewhere.

### 3.1. Files that should move to `dev/scripts/`

| File | What it does | Target location |
|---|---|---|
| `setup_vm.py` | VM provisioning (Docker, cloudflared) | `dev/scripts/setup_vm.py` (already exists?) |
| `setup-vm.sh` | Shell wrapper for setup_vm.py | `dev/scripts/setup-vm.sh` |
| `deploy-azure.sh` | Azure VM deployment (deprecated per AGENTS.md) | **Delete** (replaced by `setup_vm.py`) |
| `deploy-master.sh` | Deploy master branch to Azure | `dev/scripts/deploy-master.sh` |
| `sync-and-restart.sh` | rsync + docker restart on VM | `dev/scripts/sync-and-restart.sh` |
| `cleanup-vm.sh` | Clean up Azure VM resources | `dev/scripts/cleanup-vm.sh` |
| `remote-verify.sh` | Health check script for VM | `dev/scripts/remote-verify.sh` |
| `init-tunnel.py` | Cloudflare tunnel ingress init | `dev/scripts/init-tunnel.py` (already exists as `remote-services-init-tunnel.py`) |
| `setup-cf-tunnel.sh` | Shell wrapper for tunnel setup | `dev/scripts/setup-cf-tunnel.sh` |
| `integration_test.py` | Integration test for Docker stack | `dev/scripts/integration_test.py` (already exists) |
| `xauusd_lifecycle.py` | Ad-hoc XAUUSD trading CLI | `ctrader_cli/commands/xauusd.py` or `scripts/tools/` |

### 3.2. Files that are already duplicated

- `remote-services/integration_test.py` (327 lines) vs `dev/scripts/integration_test.py` (291 lines) — **different implementations of the same concept.** Keep `dev/scripts/` version, delete `remote-services/` version.
- `remote-services/init-tunnel.py` vs `dev/scripts/remote-services-init-tunnel.py` — the dev script is a thin SSH wrapper around the remote-services version. Consolidate: keep the implementation in `dev/scripts/init-tunnel.py` and delete both `remote-services/init-tunnel.py` and `dev/scripts/remote-services-init-tunnel.py`.
- `dev/scripts/deploy_ctrader.py` + `dev/scripts/deploy_ctrader_remote.py` — two variants of the same deploy script. Consolidate into one with `--remote` flag.
- `dev/scripts/remote_test.py` + `dev/scripts/remote_test.sh` — Python and shell versions of the same test. Keep one.
- `dev/scripts/deploy.sh` + `dev/scripts/deploy-remote.sh` — shell wrappers. Consolidate.

---

## 4. Duplicate Appwrite Client Patterns

**Three different patterns** for connecting to Appwrite exist across the codebase:

| Pattern | Location | Approach |
|---|---|---|
| **Singleton** | `ssfx_server/appwrite_client.py` | `AppwriteClient` class with `_instance` singleton |
| **Inline construction** | `ssfx_trader/stores/appwrite_account_store.py` | Creates `Client()` and `TablesDB()` directly in `__init__` |
| **Constructor injection** | `ctrader/trading/user_config_store.py` | Takes `Client` as constructor arg |

**Problem:** Different packages can't share Appwrite connection pools, configuration, or retry logic. Each package reinvents the auth client.

**Action:** Create a single shared Appwrite client factory in `remote-services/shared/appwrite_client.py` (or similar). All packages import from it. The factory reads env vars once and returns configured `Client` + `TablesDB` instances.

---

## 5. Dual Config Readers for `ssfx_accounts`

Both `ctrader/trading/user_config_store.py` and `ssfx_trader/stores/appwrite_account_store.py` read from the `ssfx_accounts` Appwrite table. They have:

1. **Overlapping mapping logic** — both convert `config_json` → `AccountConfig`
2. **Different capabilities** — `ctrader` version is read-only; `ssfx_trader` version also writes executions and risk state
3. **Different error handling** — `ctrader` logs warnings; `ssfx_trader` logs errors and raises

**Action:** Make `ssfx_trader/stores/appwrite_account_store.py` the canonical account config reader/writer. The `ctrader` service should import from `ssfx_trader.stores.appwrite_account_store` instead of having its own `user_config_store.py`.

---

## 6. `scripts/` vs `dev/scripts/` Overlap

```
scripts/
├── dev/
│   └── reset_user_db.py      ← should be in dev/scripts/
└── mcp-appwrite.sh           ← could be in dev/scripts/

dev/scripts/                   ← 40+ files, canonical location
```

**Action:**
1. Move `scripts/dev/reset_user_db.py` → `dev/scripts/reset_user_db.py`
2. Move `scripts/mcp-appwrite.sh` → `dev/scripts/mcp-appwrite.sh`
3. Delete empty `scripts/dev/` directory
4. If `scripts/` becomes empty, delete it entirely

---

## 7. Package Naming Inconsistency

The `remote-services/` packages use inconsistent naming conventions:

| Package | Convention | Issue |
|---|---|---|
| `ssfx_parser/` | `ssfx_` prefix | Consistent with `ssfx_server`, `ssfx_trader` |
| `ssfx_server/` | `ssfx_` prefix | ✅ |
| `ssfx_trader/` | `ssfx_` prefix | ✅ |
| `market_data_service/` | descriptive noun | Inconsistent — should be `ssfx_dataservice` or `slwp_dataservice` |
| `agent_harness/` | descriptive noun | Inconsistent |
| `ctrader/` | brand name | Inconsistent — but `ctrader` is a bounded context so acceptable |
| `ctrader_client/` | brand name | Library, acceptable |
| `ctrader_cli/` | brand name | CLI tool, acceptable |

**Note:** Per `NAMING_AND_CONSOLIDATION_OVERHAUL.md`, the target project name is `slwp` (not `ssfx`). The `ssfx_` prefix is legacy from a prior project name. Renaming all packages to `slwp_` is a massive effort that should be part of a future phase.

**Short-term action:** Document the naming inconsistency. Do NOT rename packages now — the commit churn would be enormous. Defer to a dedicated repo-wide rename project.

---

## 8. Additional Issues Found

### 8.1. `ssfx_server/signal_generator.py` vs `market_data_service/gold_quant_engine/`

`ssfx_server/signal_generator.py` generates autonomous XAUUSD signals using the gold quant engine. It runs inside `ssfx_server` (port 8000). But `market_data_service/gold_quant_engine/` (port 9000-9002) is ALSO responsible for XAUUSD analytics. 

**The signal generator should probably live in `market_data_service/` or `agent_harness/`**, not in the webhook server. The webhook server's job is to receive Telegram signals, not generate them.

**Action:** Move `signal_generator.py` to `market_data_service/gold_quant_engine/generator.py` or `agent_harness/signal_generator.py`.

### 8.2. `ssfx_server/cli.py` — Tiny orphaned utility

Only has `set_webhook()` — one function. Not a real CLI.

**Action:** Move to `dev/scripts/set_telegram_webhook.py`.

### 8.3. `ssfx_server/appwrite_client.py` — Only used by `ssfx_server/`

It's a singleton but only `ssfx_server/` imports it. If it's truly shared, it should be in a shared module.

**Action:** Merge into the shared Appwrite client factory (see §4).

### 8.4. `remote-services/README.md` — Outdated?

Need to verify if this README still reflects the current architecture after the consolidation.

### 8.5. `dev/scripts/` bloat — 40+ files

Many files are one-off scripts or thin wrappers. Consider organizing into subdirectories:

```
dev/scripts/
├── deploy/          # deploy_auth.py, deploy_site.py, deploy.sh, deploy-remote.sh
├── tunnel/          # cf_tunnel_*.py, setup_cf_tunnel.py, setup_function_domains.py
├── init/            # init_ctrader_tables.py, init.sh, admin-pin.py
├── monitoring/      # status.py, logs.sh, start.sh, stop.sh
├── testing/         # integration_test.py, remote_test.py, remote_test.sh, lint.sh, test.sh
├── cleanup/         # cleanup_appwrite_resources.py, cleanup_cache.py, reset.py, reset_admin.py
└── _shared/         # _config.py, _azure_vm.py, sync_shared.py
```

---

## 9. Fix Plan (Prioritized)

### Phase A — Safe Deletions (no behavior change)

1. **Delete `ctrader/market/`** — 5 files (`feed_client.py`, `hub.py`, `router.py`, `context_engine.py`, `models.py`). Update `ctrader/web_app.py` to remove market router import. ✅ Redundant with `market_data_service`.
2. **Delete `market_data_service/util/ctrader_import.py`** — no-op. Remove imports from `feed_manager.py` and `data_ingestion.py`.
3. **Delete `remote-services/deploy-azure.sh`** — deprecated per AGENTS.md.
4. **Delete `remote-services/integration_test.py`** — `dev/scripts/integration_test.py` is the canonical version.
5. **Delete `dev/scripts/remote-services-init-tunnel.py`** — thin wrapper around `remote-services/init-tunnel.py`.
6. **Move `remote-services/init-tunnel.py`** → `dev/scripts/init-tunnel.py`, then update `dev.sh` dispatcher.
7. **Move `scripts/dev/reset_user_db.py`** → `dev/scripts/reset_user_db.py`.
8. **Move `scripts/mcp-appwrite.sh`** → `dev/scripts/mcp-appwrite.sh`.
9. **Delete empty `scripts/` and `scripts/dev/`**.

### Phase B — Move scripts out of `remote-services/`

10. **Move VM/deployment scripts** from `remote-services/` root to `dev/scripts/`:
    - `setup_vm.py`, `setup-vm.sh`, `deploy-master.sh`, `sync-and-restart.sh`, `cleanup-vm.sh`, `remote-verify.sh`, `setup-cf-tunnel.sh`
    - Update any hardcoded paths in these scripts.
11. **Move `remote-services/xauusd_lifecycle.py`** → `ctrader_cli/commands/xauusd.py` (add as CLI subcommand).

### Phase C — Consolidate Appwrite clients

12. **Create `remote-services/shared/` package** with `appwrite_client.py`:
    - Factory function `get_appwrite_client()` that reads env and returns `(Client, TablesDB, database_id)`
    - Reuse in `ssfx_server/appwrite_client.py`, `ssfx_trader/stores/appwrite_account_store.py`, `ctrader/trading/user_config_store.py`
13. **Deprecate `ssfx_server/appwrite_client.py`** — import from shared instead.

### Phase D — Consolidate config readers

14. **Delete `ctrader/trading/user_config_store.py`** — import from `ssfx_trader.stores.appwrite_account_store` instead.
15. **Move `ssfx_trader/stores/appwrite_account_store.py` account config reading** to the shared module if it makes sense.

### Phase E — Consolidate dev/scripts

16. **Merge `deploy_ctrader.py` + `deploy_ctrader_remote.py`** into one script with `--remote` flag.
17. **Merge `remote_test.py` + `remote_test.sh`** — keep Python version, delete shell version.
18. **Merge `deploy.sh` + `deploy-remote.sh`** into one script.
19. **Organize `dev/scripts/` into subdirectories** (see §8.5).

### Phase F — Re-evaluate placement

20. **Evaluate moving `ssfx_server/signal_generator.py`** to `market_data_service/` or `agent_harness/` — the webhook server should not generate autonomous signals.
21. **Evaluate `ssfx_server/cli.py`** → `dev/scripts/set_telegram_webhook.py`.

---

## 10. What NOT to Change (Yet)

| Item | Why Deferred |
|---|---|
| Database rename `ctrader_auth` → `slwp_platform` | Requires migration script, dual-write, backfill (Phase 3 per NAMING_AND_CONSOLIDATION_OVERHAUL) |
| Table renames (`slave_accounts` → `users`, etc.) | Requires database migration, code changes across Functions + Python + SPA |
| Package name `ssfx_` → `slwp_` | 100+ import statements across the repo. Too much churn. |
| Hostname renames (`ssfx-api` → `api`, etc.) | Requires Cloudflare DNS changes, tunnel ingress, env vars |
| Merge `auth-pin` into `auth-oauth` | Functional change to routing; test carefully |
| Merge `trade_configs` + `ssfx_accounts` | Requires schema design and data migration |
