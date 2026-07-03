# SSFX / slwp Naming & Consolidation Overhaul

> This document records the consolidation decisions for architecture docs and the proposed naming changes across hostnames, Functions, databases, tables, and endpoints. It is a decision-aid and migration backlog, not an executed refactor.

---

## 1. Consolidation Decisions

### 1.1. Problem

The project had architecture information split across multiple documents and inline notes:

- `docs/RESOURCE_CONFIGURATION.md` was referenced in `ARCHITECTURE.md` but did not exist on disk; its content existed only as a section inside `ARCHITECTURE.md`.
- `docs/AUTHENTICATION_ARCHITECTURE.md` duplicated auth flows, component descriptions, and endpoint tables already present in `ARCHITECTURE.md`.
- The result was drift: authentication changes were documented in one file but missing from another, and resource configuration details were hard to find.

### 1.2. Resolution

| Before | After | Rationale |
|---|---|---|
| `RESOURCE_CONFIGURATION.md` (missing) + resource-config section in `ARCHITECTURE.md` | Resource configuration folded into each logical plane in `ARCHITECTURE.md` | Hostnames, endpoints, schemas, env vars, and infra config are described where they are used, not in a separate doc. |
| Full `AUTHENTICATION_ARCHITECTURE.md` | Condensed to `docs/AUTHENTICATION_ARCHITECTURE.md` as an identity & access reference | Detailed auth flow, component architecture, and Appwrite-Cloud patterns now live in `ARCHITECTURE.md` §2.2 and §5. The condensed doc keeps endpoint/token tables for operators. |
| Three overlapping docs | Two focused docs + this decision record | `ARCHITECTURE.md` = master architecture; `AUTHENTICATION_ARCHITECTURE.md` = operator reference; `NAMING_AND_CONSOLIDATION_OVERHAUL.md` = backlog and rename matrix. |

---

## 2. Naming Overhaul Matrix

### 2.1. Public hostnames

| Current | Proposed | Scope of change | Effort | Dependencies |
|---|---|---|---|---|
| `auth.mrme.tech` | **keep** | Function custom domain | None | — |
| `pin.mrme.tech` | merge into `auth.mrme.tech` | Function custom domain + SPA config | Medium | `ctrader-pin-auth` routes must be added to `ctrader-auth` or a unified auth function; update `sites/ssfx-hq/config.js`, `sites/ctrader-auth-site/config.js`, `dev/scripts/_config.py`. |
| `ssfx-api.mrme.tech` | `api.mrme.tech` | DNS + tunnel ingress + SPA config + env vars | Medium | Update `remote-services/config/tunnel-ingress.json`, Cloudflare DNS, `sites/ssfx-hq/config.js`, `remote-services/config/v2.env`, GitHub Secrets if any. |
| `dataservice.mrme.tech` | `market.mrme.tech` | DNS + tunnel ingress + SPA config + env vars | Medium | Same as above, plus `DATA_SERVICE_URL` consumers. |
| `agent.mrme.tech` | `ai.mrme.tech` | DNS + tunnel ingress + env vars | Low/Medium | Update ingress, DNS, configs. |
| `pplx-agent.mrme.tech` | `research.mrme.tech` | DNS + tunnel ingress + env vars | Low | Update ingress, DNS, configs. |
| `account-hub.mrme.tech` | **keep** | — | None | — |

### 2.2. Appwrite Functions

| Current | Proposed | Responsibility | Scope of change | Notes |
|---|---|---|---|---|
| `ctrader-auth` | `auth-oauth` | OAuth2 + session + admin | Medium | ✅ Applied. Rename function ID, update `appwrite/functions.json`, CI, custom domain, references. |
| `ctrader-pin-auth` | `auth-pin` | Username/PIN auth | Medium | ✅ Applied. Could later merge into a single `auth` function with route prefixes. |
| `ctrader-internal` | `api-internal` | Server-to-server refresh + grant accounts | Medium | ✅ Applied. Name is misleading — it is not cTrader-specific, it is the internal platform API. |
| `ctrader-token-refresh-worker` | `token-refresh` | Scheduled token refresh + ephemeral sweep | Low | ✅ Applied. Cron schedule stays the same. |

**Long-term option:** merge `auth-oauth` and `auth-pin` into one `auth` Function with custom-domain path routing:

- `auth.mrme.tech/oauth/*` → OAuth flow
- `auth.mrme.tech/auth/pin/*` → PIN flow
- `auth.mrme.tech/session`, `/logout` → shared session endpoints

### 2.3. Databases

| Current | Proposed | Rationale | Scope |
|---|---|---|---|
| `ctrader_auth` | `slwp_platform` | The database holds users, trading config, signals, executions, and system config — not only cTrader auth. | High. All table references, SDK calls, env vars, and migrations must be updated. Plan as a discrete migration. |
| `market_data` | **keep** | Already descriptive. | None. |

### 2.4. Tables

| Current | Proposed | Rationale | Scope | Migration approach |
|---|---|---|---|---|
| `slave_accounts` | `users` | Neutral, generic; Appwrite `users` is already the real identity. This table becomes the grant/profile extension. | High. Referenced in every auth function, init scripts, Python services, SPA. | Dual-write → cutover → drop old table. |
| `accounts` | `ctrader_accounts` | Clarifies relationship to cTrader trading accounts. | Medium. Account hub and data service use this. | Rename via migration or create new table and backfill. |
| `trade_configs` | `trade_settings` | Simpler; row-level security already makes it per-user. | Medium. `ssfx_trader`, `ssfx_server`, functions. | Rename or migrate. |
| `ssfx_accounts` | `signal_slaves` | Explains Telegram slave role. | Medium. `ssfx_server`, `ssfx_trader`. | Rename + update code. |
| `account_events` | `account_state_history` | Time-series of account snapshots. | Low/Medium. Account hub writes; dashboard reads. | Rename. |
| `master_signals` | `signal_broadcasts` | Neutral, clearer. | Low. `ssfx_server` and dashboard. | Rename. |
| `ssfx_executions` | **keep** or `signal_executions` | Already clear; rename optional. | Low | Optional. |
| `ctrader_trading_events` | **keep** or `trading_events` | `ctrader` prefix is redundant inside the platform DB. | Low | Optional. |
| `ephemeral_tokens` | **keep** | Accurate. | None | — |
| `grant_locks` | **keep** | Accurate. | None | — |
| `service_config` | **keep** | Accurate. | None | — |

### 2.5. Endpoints

| Current function | Current path | Proposed path | Function |
|---|---|---|---|
| `ctrader-auth` | `/auth/ctrader/start` | `/oauth/start` | `auth-oauth` / unified `auth` |
| `ctrader-auth` | `/callback` | `/oauth/callback` | `auth-oauth` / unified `auth` |
| `ctrader-auth` | `/session` | `/session` | `auth-oauth` / unified `auth` |
| `ctrader-auth` | `/logout` | `/logout` | `auth-oauth` / unified `auth` |
| `ctrader-auth` | `/admin/slaves` | `/admin/slaves` | `auth-oauth` / unified `auth` |
| `ctrader-pin-auth` | `/pin-login` | `/auth/pin/login` | `auth-pin` / unified `auth` |
| `ctrader-pin-auth` | `/set-credentials` | `/auth/pin/credentials` | `auth-pin` / unified `auth` |
| `ctrader-pin-auth` | `/pin-reset/request` | `/auth/pin/reset/request` | `auth-pin` / unified `auth` |
| `ctrader-pin-auth` | `/pin-reset/confirm` | `/auth/pin/reset/confirm` | `auth-pin` / unified `auth` |
| `ctrader-internal` | `/internal/ctrader/refresh` | `/api/internal/token/refresh` | `api-internal` |
| `ctrader-internal` | `/internal/grant/latest` | `/api/internal/grant/latest` | `api-internal` |
| `ctrader-internal` | `/internal/grant/:id/accounts` | `/api/internal/grant/:id/accounts` | `api-internal` |

### 2.6. SPA / config keys

| Current | Proposed | Location |
|---|---|---|
| `pinDomain` | remove / merge into `authDomain` | `sites/ssfx-hq/config.js`, `js/api.js` |
| `authDomain` pointing to `auth.mrme.tech` | keep, but route all auth calls through it | SPA API clients |
| `v2ApiBase` = `https://ssfx-api.mrme.tech` | `apiBase` = `https://api.mrme.tech` | `sites/ssfx-hq/config.js` |
| `dataserviceBase` = `https://dataservice.mrme.tech` | `marketBase` = `https://market.mrme.tech` | `sites/ssfx-hq/config.js` |
| `agentHarnessBase` = `https://agent.mrme.tech` | `aiBase` = `https://ai.mrme.tech` | `sites/ssfx-hq/config.js` |

---

## 3. Appwrite Cloud Optimization Patterns

The proposed renames are not cosmetic; they are prerequisites for using Appwrite Cloud primitives more naturally.

| Pattern | Current | Proposed Appwrite-native approach | Blocker removed by rename |
|---|---|---|---|
| **Identity extension** | `slave_accounts` is the de-facto user table. | Appwrite `users` is the identity; a `users`/ `ctrader_grants` table only holds cTrader-specific data. | Renaming `slave_accounts` → `users` makes this relationship obvious. |
| **RBAC** | Master role stored in `service_config.master_auth`. | Appwrite **Teams/Labels** (`label:master`) with table-level label permissions. | Removing custom master-auth config reduces bespoke logic. |
| **Realtime state** | SPA polls for account updates. | Subscribe to `account_events` / `account_state_history` changes. | Renaming events → state_history clarifies subscription intent. |
| **Webhooks** | Functions are invoked directly by Python services. | Table changes trigger Appwrite Webhooks to Functions for audit, cache invalidation, notifications. | Having a single `slwp_platform` DB makes webhook routing simpler. |
| **Messaging** | PIN reset emails via Resend directly. | Appwrite **Messaging** templates (when available) for email/SMS/Push. | Consolidating auth under one domain makes messaging integration easier. |
| **Scheduled Functions** | Dedicated refresh worker. | Single scheduled Function sweeps near-expiry tokens and stale ephemerals. | Rename `ctrader-token-refresh-worker` → `token-refresh` reflects generic sweep role. |
| **Storage** | Attachments/reports not handled. | Appwrite **Storage** for trade journals, snapshots, exported reports. | Not blocked by naming; adoption recommended. |

---

## 4. Migration Roadmap

### Phase 0 — Documentation alignment (safe, no code change)

- [x] Consolidate `ARCHITECTURE.md` and `AUTHENTICATION_ARCHITECTURE.md`.
- [x] Publish `NAMING_AND_CONSOLIDATION_OVERHAUL.md` as decision backlog.
- [ ] Update any internal wiki/Notion links to point to `ARCHITECTURE.md`.
- [x] Delete deprecated `sites/ctrader-auth-site` and `sites/ctrader-command-center`.

### Phase 1 — Function and domain consolidation (✅ COMPLETED)

- [x] Renamed `ctrader-auth` → `auth-oauth`
- [x] Renamed `ctrader-pin-auth` → `auth-pin`
- [x] Renamed `ctrader-internal` → `api-internal`
- [x] Renamed `ctrader-token-refresh-worker` → `token-refresh`
- [x] Updated `appwrite/functions.json`, directory names, `dev/scripts/_config.py`, `dev/scripts/deploy/deploy_auth.py`, CI workflow, and all doc references.

**Remaining:** Merge `auth-pin` routes into `auth-oauth` behind `auth.mrme.tech` (single auth domain) — deferred until post-trial.

### Phase 2 — Hostname migration (medium risk)

1. Add new hostnames (`api.mrme.tech`, `market.mrme.tech`, `ai.mrme.tech`, `research.mrme.tech`) to Cloudflare DNS and tunnel ingress.
2. Update SPA configs and `remote-services/config/v2.env`.
3. Run parallel old+new hostnames for a cutover window.
4. Remove old hostnames from tunnel ingress and DNS.

### Phase 2 — Hostname migration (medium risk)

1. Add new hostnames (`api.mrme.tech`, `market.mrme.tech`, `ai.mrme.tech`, `research.mrme.tech`) to Cloudflare DNS and tunnel ingress.
2. Update SPA configs and `remote-services/config/v2.env`.
3. Run parallel old+new hostnames for a cutover window.
4. Remove old hostnames from tunnel ingress and DNS.

### Phase 3 — Database rename (high risk, needs migration script)

1. Create `slwp_platform` database.
2. Recreate tables under new names (`users`, `ctrader_accounts`, `trade_settings`, `signal_slaves`, `account_state_history`, `signal_broadcasts`).
3. Backfill data, including permissions.
4. Update all Functions and Python services to dual-write.
5. Cut reads to new tables.
6. Drop old database after validation.

### Phase 4 — Appwrite Cloud primitive adoption (medium risk)

1. Replace `service_config.master_auth` with Appwrite label/team permissions.
2. Adopt Appwrite Realtime for SPA dashboards.
3. Adopt Appwrite Messaging for transactional emails.
4. Introduce Appwrite Storage for reports/snapshots.

---

## 5. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Hardcoded `slave_accounts` across Functions and Python services | Create a single shared constant/module first, then rename once. |
| Cloudflare DNS/tunnel cutover causes downtime | Run old + new hostnames in parallel; verify with smoke tests before deleting old records. |
| Database rename loses permissions | Script schema+permissions export/import and validate row counts and role assignments. |
| SPA cache references old config.js | Bust cache on site deployment; `config.js` should include a version query parameter. |
| External consumers (Telegram, cTrader OAuth console) reference old hostnames | Keep redirect rules or old hostnames during cutover for webhook and OAuth callback. |

---

## 6. Decision Log

| Date | Decision | Rationale |
|---|---|---|
| 2026-07-03 | Fold `RESOURCE_CONFIGURATION.md` into `ARCHITECTURE.md` | File was missing; resource config is better described in context. |
| 2026-07-03 | Condense `AUTHENTICATION_ARCHITECTURE.md` to operator reference | Prevents duplication with `ARCHITECTURE.md`; full flow belongs in master architecture. |
| 2026-07-03 | Keep `auth.mrme.tech`; merge `pin.mrme.tech` as future state | Single auth domain is simpler, but merging functions is a non-trivial change. |
| 2026-07-03 | Propose `slwp_platform` database rename | Current `ctrader_auth` name is too narrow for a platform-wide database. |
| 2026-07-03 | Propose `slave_accounts` → `users` | Aligns identity model with Appwrite `users`; removes pejorative term. |

---

## 7. References

- `docs/ARCHITECTURE.md` — master architecture.
- `docs/AUTHENTICATION_ARCHITECTURE.md` — identity & access reference.
- `AGENTS.md` — project conventions and operations.
