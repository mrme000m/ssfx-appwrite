# SSFX / slwp Architecture

> **Scope:** single source of truth for the logical and physical architecture of the cTrader copy-trading platform. This document consolidates content previously spread across `RESOURCE_CONFIGURATION.md` and `AUTHENTICATION_ARCHITECTURE.md`; authentication is treated as an integral plane rather than a separate document.
>
> **Target state:** Appwrite Cloud as the control plane. All user, system, and runtime configuration lives in TablesDB. Long-running execution happens in Python services behind a Cloudflare Tunnel. SPAs are served by Appwrite Sites.

---

## 1. Architecture Principles

| # | Principle | How we apply it |
|---|---|---|
| 1 | **Appwrite is the source of truth** | Users, config, permissions, session state, and audit data are authoritative in TablesDB. Runtime services hydrate their local caches from Appwrite on startup and on change. |
| 2 | **Use Appwrite primitives by default** | Auth, TablesDB, Realtime, Functions, Sites, Storage, Messaging, and Webhooks are preferred over self-hosted alternatives. |
| 3 | **Least privilege at every boundary** | Row-level security, role/label-based permissions, HMAC-signed state, encrypted tokens, and scoped internal keys. |
| 4 | **Stateless, horizontally-scalable execution** | Functions are stateless; Python services hold only transient TCP connections and refresh them from Appwrite. |
| 5 | **Graceful degradation** | AI agents and market-data enrichments can fail back to deterministic rules. |
| 6 | **Typed, versioned contracts** | API contracts, table schemas, and environment contracts are checked in; drift is detected by CI. |

---

## 2. Logical Planes

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Experience Plane                                                          │
│  Appwrite Sites: ssfx-hq (app.mrme.tech)                                  │
│  Vanilla JS SPA → Appwrite Web SDK + REST clients                         │
├──────────────────────────────────────────────────────────────────────────┤
│ Identity & Access Plane                                                   │
│  Appwrite Functions + Appwrite Auth + TablesDB                            │
│  auth-oauth / auth-pin / api-internal / token-refresh                     │
├──────────────────────────────────────────────────────────────────────────┤
│ Intelligence Plane                                                        │
│  agent_harness (Python) + pplx-agent (Python)                             │
│  Signal intent, entry decisions, lifecycle planning, market research      │
├──────────────────────────────────────────────────────────────────────────┤
│ Operations Plane                                                          │
│  ssfx_server, market_data_service, account_hub, ctrader CLI/runtime       │
│  Containerized on AWS VM, exposed via Cloudflare Tunnel                   │
├──────────────────────────────────────────────────────────────────────────┤
│ Data Plane                                                                │
│  TablesDB (authority)  |  InfluxDB (time-series)  |  Storage (artifacts)  │
└──────────────────────────────────────────────────────────────────────────┘
```

### 2.1. Experience Plane

**Primary site:** `ssfx-hq` served by Appwrite Sites at `https://app.mrme.tech`.

**Key files:**

- `sites/ssfx-hq/index.html` — shell and hash-router mount point.
- `sites/ssfx-hq/config.js` — runtime endpoints, project ID, database IDs.
- `sites/ssfx-hq/js/api.js` — API clients (`AuthAPI`, `V2API`, `AgentAPI`, `DataAPI`).
- `sites/ssfx-hq/js/auth.js` — session manager.
- `sites/ssfx-hq/js/router.js` — hash-based client routing.
- `sites/ssfx-hq/js/components/*` — landing, login, onboarding, dashboard.

**Appwrite Cloud leverage:**

- Serve the SPA from **Appwrite Sites** with custom domain and automatic TLS.
- Use the **Appwrite Web SDK** for session cookies, account status, and Realtime subscriptions.
- Subscribe to **Realtime** for account events, execution updates, and signal lifecycle changes instead of polling.

**Cleanup note:** `sites/ctrader-auth-site` and `sites/ctrader-command-center` are deprecated; `ssfx-hq` is the only deployed site. Remove them from the working tree once references in `AGENTS.md` and scripts are migrated.

### 2.2. Identity & Access Plane

This plane is implemented entirely with Appwrite primitives: Functions, Auth, and TablesDB.

#### 2.2.1. Functions

| Function | Current ID | Responsibility | Public domain |
|---|---|---|---|
| cTrader OAuth | `auth-oauth` | OAuth2 initiation & callback, session creation, master admin operations. | `auth.mrme.tech` |
| PIN Auth | `auth-pin` | Username+PIN login, PIN setup, PIN reset via email. | `pin.mrme.tech` |
| Internal API | `api-internal` | Server-to-server token refresh and grant account persistence. | internal only |
| Refresh worker | `token-refresh` | Proactive refresh of near-expiry tokens and stale ephemeral-token sweep. | scheduled + on-demand HTTP |

#### 2.2.2. Authentication paths

**Primary path: cTrader OAuth2**

```
User → SPA → GET /auth/ctrader/start → cTrader consent
→ GET /callback → verify state → exchange code → create Appwrite user
→ create/upsert slave_accounts row → set session cookie → redirect to dashboard
```

- State token: HMAC-signed, single-use, 10-minute TTL, stored in `ephemeral_tokens`.
- Tokens at rest: AES-GCM-256 encrypted in `slave_accounts`.
- Session cookie: `a_session_<PROJECT_ID>`, HTTP-only, Secure, SameSite=Lax.

**Secondary path: Username + PIN**

```
User → SPA → POST /pin-login → verify username + BCrypt pin_hash
→ create Appwrite session → set cookie → dashboard
```

- PIN setup happens after first OAuth login via `POST /set-credentials`.
- Master login uses username `admin`; role is looked up in `service_config` (`master_auth`).
- PIN reset uses Resend (migrate to Appwrite Messaging when native email templates mature).

**Background path: Token refresh**

```
Python service → POST /internal/ctrader/refresh (x-internal-key)
→ acquire grant_locks row → decrypt refresh token → refresh with cTrader
→ encrypt + store new tokens → release lock → return access_token
```

#### 2.2.3. Identity and permission tables

| Table | Purpose | Permission model |
|---|---|---|
| `slave_accounts` | Link between Appwrite user, cTrader grant, username, PIN hash, encrypted tokens. | Row-level security per Appwrite user. |
| `trade_configs` | Per-user trading settings (lot size, multipliers, risk kill-switches). | Row-level security per Appwrite user. |
| `ephemeral_tokens` | Short-lived OAuth state and PIN-reset tokens. | Server-only writes; TTL cleanup. |
| `grant_locks` | Distributed locks for token refresh. Row `$id` is the grant ID. | Server-only. |
| `service_config` | System config: `ctrader_oauth`, `master_auth`, `pplx_agent`. | Server-only writes; controlled read. |

**Appwrite Cloud leverage:**

- Create a native **Appwrite user** for every cTrader grant. Store the Appwrite `userId` in `slave_accounts.appwrite_user_id`; this gives you labels, teams, sessions, and audit for free.
- Replace the custom `master_auth` `service_config` entry with an **Appwrite Team/Label** (`label:master`) and use table-level label permissions where appropriate.
- Replace `pin.mrme.tech` with route prefixes under `auth.mrme.tech` (`/auth/pin/*`, `/oauth/*`) once the `auth-oauth` and `auth-pin` functions are merged. Until then, keep the separate domain documented in config.
- Use **Appwrite Realtime** to push session invalidation, role changes, and account selection updates to the SPA.

### 2.3. Intelligence Plane

Python FastAPI services that make trading decisions.

| Service | Port | Role | Model |
|---|---|---|---|
| `agent_harness` | 9003 | Signal intent, entry validation, lifecycle planning. | Mistral Small 3.2 / Hermes 3 / Kimi K2.7 |
| `pplx_agent` | 9004 | Long-term gold market research and Perplexity Space maintenance. | Perplexity + TradingView scans |

**Integration:**

- `ssfx_server` calls `/agent/v1/signal/intent` before parsing.
- `ssfx_trader/executor.py` calls `/agent/v1/entry/decision` for new XAUUSD signals and `/agent/v1/lifecycle/plan` for follow-ups.
- Kill-switches (`AGENT_*_ENABLED`) allow deterministic fallback.

### 2.4. Operations Plane

Containerized Python services on the AWS VM. They read current configuration from Appwrite and use short-lived access tokens from `ctrader-internal`.

| Service | Port | Responsibility |
|---|---|---|
| `ssfx_server` | 8000 | Telegram webhook receiver, signal parsing, slave routing, admin REST API. |
| `market_data_service` | 9000–9002 | cTrader tick ingestion, gold quant engine, control/SSE/OpenPI APIs. |
| `account_hub` | 9301 | Live cTrader transports, account discovery, WebSocket fan-out. |
| `ctrader` / `ctrader_cli` | 9300 / CLI | Direct cTrader Open API tooling and unified cTrader service. |

**Connection model:**

- One `grant_id` ≡ one cTID ≡ one access token ≡ one persistent TCP connection in the live pool.
- `account_hub` v2 discovers active grants from TablesDB and maintains exactly two transport endpoints (live + demo).
- Trading services request `/internal/ctrader/refresh` when a token is missing or near expiry.

### 2.5. Data Plane

#### 2.5.1. TablesDB

**Database: `ctrader_auth`**

| Table | Responsibility |
|---|---|
| `slave_accounts` | User identity, encrypted cTrader tokens, grant handle. |
| `trade_configs` | Per-grant trading configuration and kill-switches. |
| `accounts` | Discovered cTrader trading accounts (populated by account hub). |
| `account_events` | Real-time account state snapshots (positions, orders, balance, equity, margin). |
| `ctrader_trading_events` | Order fills, position changes, errors. |
| `master_signals` | Master-to-slave signal broadcast. |
| `ssfx_accounts` | Telegram signal slave configuration. |
| `ssfx_executions` | Signal execution history. |
| `ephemeral_tokens` | OAuth state, PIN reset tokens. |
| `grant_locks` | Distributed locks for token refresh. |
| `service_config` | Third-party and system configuration. |
| `ssfx_presets` / `ssfx_risk_state` | Preset and runtime risk state. |

**Database: `market_data`**

- Signal experience tables (`signal_experience_authors`, `sessions`, `patterns`, `overall`, `signal_quality_log`).
- Symbol-quality and derived analytics.

#### 2.5.2. Time-series and object storage

- **InfluxDB Cloud Serverless** for tick and OHLC time-series; SQLite as explicit fallback.
- **Appwrite Storage** (not currently used) for exported reports, trade journals, and snapshot archives.

---

## 3. Data Flows

### 3.1. Authentication

```
User → ssfx-hq → /auth/ctrader/start  → state token in ephemeral_tokens
→ cTrader OAuth consent → /callback
→ exchange code → Appwrite Users.create → slave_accounts.upsert
→ Appwrite Account.createSession → cookie → dashboard
```

### 3.2. Signal ingestion → execution

```
Telegram channel → Telegram Bot Webhook → ssfx_server /webhook
→ SignalIntentAgent → parser → SignalExperienceScorer
→ per-slave AccountSlave → executor
→ ctrader-internal /refresh (if needed) → ctrader-open-api
→ position → account_events / ctrader_trading_events
```

### 3.3. Market data → decision support

```
cTrader tick stream → market_data_service → GoldQuantEngine
→ /api/v1/gold/* endpoints → agent_harness / agent entry/lifecycle endpoints
→ ssfx_trader executor
```

### 3.4. Account state fan-out

```
account_hub live transport → account_events write
→ Appwrite Realtime subscription → ssfx-hq dashboard update
```

---

## 4. Naming & Consolidation Overhaul

This section records the current names, the proposed names, and the rationale. The detailed migration plan is in `docs/NAMING_AND_CONSOLIDATION_OVERHAUL.md`.

### 4.1. What has already been consolidated

- `docs/RESOURCE_CONFIGURATION.md` was folded into this document; resource configuration is now a first-class part of each plane.
- `docs/AUTHENTICATION_ARCHITECTURE.md` is condensed to a focused identity reference; the full auth flow lives in §2.2.

### 4.2. Proposed name changes

#### Hostnames / public surface

| Current | Proposed | Rationale |
|---|---|---|
| `auth.mrme.tech` | `auth.mrme.tech` | Keep; primary auth domain. |
| `pin.mrme.tech` | merge to `auth.mrme.tech` | Single auth domain with paths `/auth/pin/*`. |
| `ssfx-api.mrme.tech` | `api.mrme.tech` | Generic, shorter, service-agnostic. |
| `dataservice.mrme.tech` | `market.mrme.tech` | Matches market-data purpose. |
| `agent.mrme.tech` | `ai.mrme.tech` | Shorter, describes function. |
| `pplx-agent.mrme.tech` | `research.mrme.tech` | Describes long-term research role. |
| `account-hub.mrme.tech` | keep | Clear purpose. |

#### Functions

| Current | Proposed | Rationale |
|---|---|---|
| `ctrader-auth` → `auth-oauth` | ✅ Applied | Clear responsibility. |
| `ctrader-pin-auth` → `auth-pin` | ✅ Applied | Sibling to `auth-oauth`; mergeable under one auth domain. |
| `ctrader-internal` → `api-internal` | ✅ Applied | Server-to-server API, not cTrader-specific. |
| `ctrader-token-refresh-worker` → `token-refresh` | ✅ Applied | Remove redundant prefix. |

#### Databases & tables

| Current | Proposed | Rationale |
|---|---|---|
| `ctrader_auth` | `slwp_platform` | Platform-wide database, not only cTrader auth. |
| `slave_accounts` | `users` | Generic, non-pejorative; Appwrite user is already the real identity. |
| `accounts` | `ctrader_accounts` | Clarifies relationship to cTrader. |
| `trade_configs` | `trade_settings` | Simpler; per-user via RLS. |
| `ssfx_accounts` | `signal_slaves` | Explains Telegram slave role. |
| `account_events` | `account_state_history` | Time-series of account snapshots. |
| `master_signals` | `signal_broadcasts` | Neutral naming. |

#### Code paths / endpoints

| Current | Proposed |
|---|---|
| `/auth/ctrader/start` | `/oauth/start` |
| `/callback` | `/oauth/callback` |
| `/pin-login` | `/auth/pin/login` |
| `/set-credentials` | `/auth/pin/credentials` |
| `/pin-reset/*` | `/auth/pin/reset/*` |
| `/internal/ctrader/refresh` | `/api/internal/token/refresh` |

---

## 5. Leveraging Appwrite Cloud Optimally

### 5.1. Do more with Appwrite primitives

| Instead of | Use Appwrite Cloud primitive | Benefit |
|---|---|---|
| Custom session cookie wrangling in Functions | Appwrite Account sessions + SDK cookie handling | Correct SameSite/Secure/HttpOnly defaults, expiry, revocation. |
| Manual master-auth flag in `service_config` | Appwrite **Teams/Labels** (`label:master`) + table permissions | Built-in RBAC, audit, membership UI. |
| Resend-only email PIN reset | Appwrite **Messaging** for email/SMS/Push (when templates are available) | Unified messaging, retries, delivery status. |
| SPA polling for account updates | Appwrite **Realtime** subscriptions on `account_events` | Lower latency, less compute. |
| Manual function invocation for table-change side effects | Appwrite **Webhooks** + Functions events | Event-driven refresh, audit, notifications. |
| Self-hosted file/artifact handling | Appwrite **Storage** | Signed URLs, compression, CDN, permissions. |
| One custom refresh worker per grant | Appwrite **Scheduled Functions** + a single sweep function | Native scheduling, no cron infra. |
| Ad-hoc deployments | Appwrite **CLI + GitHub Actions** from `develop` | Repeatable, audited, secret-free repo. |

### 5.2. Connection model recommendation

Keep the Appwrite-native mode that is already emerging:

- Appwrite `users` table owns identity; `slave_accounts` (renamed `users` or `ctrader_grants`) owns only the cTrader grant handle and encrypted tokens.
- Python services never receive refresh tokens; they call `api-internal` `/api/internal/token/refresh` with `x-internal-key`.
- Use **grant-level distributed locks** via `grant_locks` row IDs to avoid thundering-herd refresh.

### 5.3. Configuration as data

- All runtime configuration belongs in TablesDB (`service_config`, `trade_settings`, `signal_slaves`).
- `.env` is reserved for bootstrap secrets: `APPWRITE_ENDPOINT`, `APPWRITE_PROJECT_ID`, `APPWRITE_API_KEY`.
- Third-party credentials (cTrader OAuth, Resend, Telegram, Perplexity cookies) are written by idempotent `init-scripts/*` into `service_config`, not into `.env` files on the VM.

---

## 6. Deployment Topology

### 6.1. Public access via Cloudflare Tunnel

Current ingress (source of truth: `remote-services/config/tunnel-ingress.json`):

| Hostname | Local service | Purpose |
|---|---|---|
| `ssfx-api.mrme.tech` | `localhost:8000` | Telegram webhook + admin API |
| `ds-control.mrme.tech` | `localhost:9000` | Data service control API |
| `ds-sse.mrme.tech` | `localhost:9001` | MCP SSE live price/tools |
| `dataservice.mrme.tech` | `localhost:9002` | Market data REST API |
| `agent.mrme.tech` | `localhost:9003` | AI agent harness |
| `pplx-agent.mrme.tech` | `localhost:9004` | Perplexity research agent |
| `ctrader.mrme.tech` | `localhost:9300` | cTrader unified service |
| `account-hub.mrme.tech` | `localhost:9301` | Account hub WebSocket |
| `app.mrme.tech` | Appwrite Site | SSFX HQ SPA |

Appwrite Functions (`auth.mrme.tech`, `pin.mrme.tech`) are reached through Appwrite custom domains, not the tunnel.

### 6.2. CI/CD

- Push to `develop` triggers `.github/workflows/deploy.yml`.
- Jobs: `deploy-tables` → parallel `deploy-functions` + `deploy-site` → `verify-domains` → `smoke-test` → `cleanup`.
- Functions are deployed with `appwrite functions create-deployment/activate`, not VCS git auto-deploy.
- Secrets are set via `dev.sh setup-gh-secrets`.

### 6.3. Local development

- Use `dev.sh <command>` (maps to `dev/scripts/<command>.py`).
- Docker Compose stack in `remote-services/docker-compose.yml`.

---

## 7. Security Model

| Layer | Control |
|---|---|
| External ingress | Cloudflare Tunnel; no direct VM public IP exposure. |
| Function authentication | Appwrite session cookies; `x-internal-key` for server-to-server; `x-admin-key` for admin API. |
| OAuth CSRF | HMAC-signed state tokens, single-use, TTL 10 min. |
| Tokens at rest | AES-GCM-256 encrypted in TablesDB; encryption key in function variables, never in code. |
| PINs | BCrypt hashed in `slave_accounts.pin_hash`; plaintext never logged. |
| Permissions | Row-level security on user tables; label-based permissions on `master_signals`; server-only writes on config. |
| Webhooks | Telegram secret-token verification; reject unknown senders. |
| Secrets | Bitwarden + GitHub Secrets + Appwrite function variables; nothing in git. |

---

## 8. Monitoring & Observability

- Health endpoints: `/health` or `/api/v1/health` on every service.
- Appwrite Function logs via Console and CLI.
- Trading metrics: execution latency, signal processing time, agent response time, token refresh success/failure.
- Realtime subscriptions in `ssfx-hq` for live account state and execution updates.

---

## 9. Related Documentation

- `docs/NAMING_AND_CONSOLIDATION_OVERHAUL.md` — detailed rename matrix and migration roadmap.
- `docs/AUTHENTICATION_ARCHITECTURE.md` — condensed identity & access reference.
- `docs/account-hub-and-dataservice.md` — account hub, data service, and InfluxDB details.
- `AGENTS.md` — project operations, dev commands, and conventions.
- `docs/SESSION_FIXES_SUMMARY.md` — record of production security fixes.

---

## 10. Glossary

| Term | Meaning |
|---|---|
| **grant_id** | Opaque handle for a cTrader token pair. |
| **ctidTraderAccountId** | cTrader trading account identifier. |
| **AccountSlave** | Runtime component that routes a signal to one configured cTrader account. |
| **GoldQuantEngine** | Real-time XAUUSD multi-timeframe analytics engine. |
| **SignalExperience** | Scoring system that adjusts execution based on signal-author history. |
