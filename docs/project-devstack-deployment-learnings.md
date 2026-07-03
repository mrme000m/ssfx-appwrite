# Project Learnings: slwp Dev Stack → Deployment

This document captures the architecture, conventions, and operational details discovered while reviewing the slwp project (Appwrite + cTrader auth layer + Python runtime services). It is intended as a reference for implementing a sound, complete, production-ready system.

---

## 1. Project identity

| Item | Value |
|------|-------|
| Project name | slwp |
| Appwrite endpoint | `https://sgp.cloud.appwrite.io/v1` |
| Appwrite project ID | `6a22a362002b9ae880bb` |
| Appwrite organization | `685456084475475206c2` |
| Primary domain | `mrme.tech` |
| Cloudflare tunnel | `ssfx_azurue` (`d1e96e86-a44a-457a-a60c-e7d5d5d675bd`) |
| AWS VM | `aws-ssfx` (current IP `18.207.246.5`, user `ec2-user`) — canonical deployer: `remote-services/setup_vm.py` |

---

## 2. Source-of-truth philosophy

- **Appwrite Database is the single source of truth** for all user-level and system-level configuration.
- `.env` is reserved only for **bootstrap secrets** required to connect to Appwrite (`APPWRITE_ENDPOINT`, `APPWRITE_PROJECT_ID`, `APPWRITE_API_KEY`).
- Third-party service configuration (cTrader OAuth, Telegram bot, LLM keys, InfluxDB, etc.) is read from `dev/scripts/init/config.yml` at setup time and then persisted to Appwrite Database.
- Runtime code reads configuration from Appwrite, not from files or environment variables.

---

## 3. Local development command dispatcher: `dev.sh`

`dev.sh` is the single entry point for all development operations. It sources `.env` and dispatches to repeatable scripts in `dev/scripts/<command>.py` (preferred) or `<command>.sh` (simple wrappers).

Commands are discovered automatically; hyphens in command names map to underscores in filenames (`cf-tunnel-status` → `cf_tunnel_status.py`).

### Required commands (from `.rules`)

| Command | Script | Status at review |
|---------|--------|------------------|
| `start` | `dev/scripts/ops/start.sh` | Stub |
| `stop` | `dev/scripts/ops/stop.sh` | Stub |
| `status` | `dev/scripts/ops/status.py` | Partial (only Azure VM) |
| `logs` | `dev/scripts/ops/logs.sh` | Stub |
| `test` | `dev/scripts/test.sh` | Stub |
| `lint` | `dev/scripts/testing/lint.sh` | Implemented |
| `integration-test` | `dev/scripts/integration_test.py` | Implemented |
| `deploy` | `dev/scripts/deploy/deploy.sh` | Implemented (delegates to `remote-services/deploy-azure.sh`) |
| `deploy-remote <target>` | `dev/scripts/deploy/deploy-remote.sh` | Stub for Azure |
| `deploy-status` | `dev/scripts/deploy/deploy-status.sh` | Implemented |
| `init` | `dev/scripts/init.sh` | Implemented |

### Cloudflare tunnel commands

| Command | Script | Purpose |
|---------|--------|---------|
| `cf-tunnel-status` | `cf_tunnel_status.py` | Show tunnel status |
| `cf-tunnel-update` | `cf_tunnel_update.py` | Apply `dev/scripts/cf-tunnel-config.json` |

---

## 4. Appwrite auth layer

### Functions

| Function | Runtime | Entry | Purpose |
|----------|---------|-------|---------|
| `ctrader-auth` | node-22 | `src/main.js` | OAuth start/callback, session, logout, admin slaves |
| `ctrader-pin-auth` | node-22 | `src/main.js` | PIN login, set credentials, PIN reset |
| `ctrader-internal` | node-22 | `src/main.js` | Server-to-server token refresh for Python backends |
| `ctrader-token-refresh-worker` | node-22 | `src/main.js` | Scheduled cron + HTTP trigger for token rotation |

### Sites

| Site | Public hostname | Purpose |
|------|-----------------|---------|
| `ctrader-auth-site` | `app.mrme.tech` | Slave onboarding, login, dashboards |
| `ssfx-hq` | `app.mrme.tech` | Primary HQ/dashboard SPA (replaces `hq.mrme.tech` / `command.mrme.tech`) |
| `ctrader-command-center` | `command.mrme.tech` | **Deprecated** — functionality merged into `ssfx-hq` at `app.mrme.tech` |

### Shared module

`functions/_shared/index.js` is copied into every function package at deploy time by `dev/scripts/ops/sync_shared.py`. It provides:

- AES-GCM-256 token encryption/decryption (`encrypt`/`decrypt`).
- HMAC state signing/verification (`signState`/`verifyState`).
- Appwrite admin client/DB/users factories.
- Distributed grant locks via TablesDB (`acquireGrantLock`/`releaseGrantLock`).
- cTrader token exchange and refresh helpers.
- CORS and cookie helpers.

**Important bug discovered:** the shared module declares `CORS_ORIGINS` but exports `CORS_ORIGIN` (undefined), which will break any consumer that imports it.

### Auth flow

1. New slave visits `https://app.mrme.tech` → clicks Connect.
2. `ctrader-auth` `/auth/ctrader/start` stores OAuth state in `ephemeral_tokens`, redirects to cTrader consent.
3. cTrader redirects to `https://auth.mrme.tech/callback`.
4. Function exchanges code, creates/updates Appwrite user, creates `slave_accounts` row with encrypted tokens, issues session cookie, redirects to SPA.
5. Slave sets username + PIN via `ctrader-pin-auth` `/set-credentials`.
6. Future login via `ctrader-pin-auth` `/pin-login`.
7. Python backends call `ctrader-internal` `/internal/ctrader/refresh` with `x-internal-key` to get short-lived access tokens.

---

## 5. TablesDB schema (`appwrite.config.json`)

### Databases

- `ctrader_auth` — identity, auth, trading config, events.
- `market_data` — signal experience, market data metadata.

### Key tables in `ctrader_auth`

| Table | Purpose | Row security |
|-------|---------|--------------|
| `slave_accounts` | Identity + encrypted tokens | Yes |
| `trade_configs` | Per-slave copy-trading settings | Yes (but table-level permissions are too broad) |
| `accounts` | Discovered cTrader trading accounts | No |
| `account_events` | Real-time account state/events | No |
| `ctrader_trading_events` | Trading operation log | No |
| `ephemeral_tokens` | OAuth state, PIN reset tokens | No |
| `grant_locks` | Distributed token-refresh locks | No |
| `ssfx_accounts` | Telegram signal follower config | No |
| `ssfx_executions` | Signal execution history | No |
| `master_signals` | Master-to-slave signal broadcast | No |

### Notable schema gaps

- `ssfx_presets` is referenced by the command-center site but not defined.
- `service_config` is created at runtime by the data service and not in `appwrite.config.json`.
- `accounts` and `account_events` lack indexes for their main query patterns.
- `ssfx_accounts` schema does not include `updated_at`, but the runtime writes it.

---

## 6. Deployment pipelines

### Appwrite auth layer (`dev/scripts/deploy/deploy_auth.py`)

Called via `./dev.sh deploy-auth`.

Steps:
1. Sync shared module (`sync_shared.py`).
2. Lint (`node --check` on shared + every `src/main.js`).
3. Push TablesDB (`appwrite push tables --all --force`).
4. For each function:
   - Upsert variables by stable variable ID.
   - Build filtered `.tar.gz` of function code.
   - `appwrite functions create-deployment --code <tarball> --activate`.
   - Poll until ready.
5. Set refresh-worker cron (`0 3 * * *`).
6. For each site:
   - Build filtered `.tar.gz`.
   - `appwrite sites create-deployment --code <tarball> --activate`.
   - Poll until ready.
7. Check custom domain status (`setup_function_domains.py --status`).
8. Optional smoke test (`--smoke`).

**Issue discovered:** the tarball is created in `/tmp`, but the Appwrite CLI help says `--code` must use a path within the current directory.

### GitHub Actions

`.github/workflows/deploy.yml` triggers on pushes to `develop`:

1. `deploy-tables` → `appwrite push tables --all --force`.
2. `deploy-functions` → `deploy_auth.py --functions --no-tables --no-site --no-domains --no-smoke --no-sync --no-lint`.
3. `deploy-site` → `deploy_auth.py --site --no-tables --no-functions --no-domains --no-smoke`.
4. `verify-domains` → domain status check.
5. `smoke-test` → curl `/session` and site root.
6. `cleanup` → remove orphaned Appwrite resources.

**Important:** Functions are **not** connected to VCS/git auto-deploy. GitHub Actions controls all deployments via the Appwrite CLI with API key auth.

### Remote-services deployment

Two competing paths exist:

| Script | Remote directory | Notes |
|--------|------------------|-------|
| `remote-services/setup_vm.py` | `~/ssfx-remote-services` | **Canonical** AWS VM provision + sync + build + deploy |
| `remote-services/deploy-azure.sh` | `~/ssfx-remote-services` | **Deprecated** — kept for Azure compatibility only |
| `remote-services/deploy-master.sh` | `~/ssfx-remote-services` | **Deprecated** — Azure-specific orchestrator |
| `dev/scripts/ops/remote-services-sync.py` | `~/ctrader-services` | **Deprecated** — stale target directory |

The VM runs a single Docker container via `docker-compose.yml` exposing ports 8000, 9000, 9001, 9002, 9003, 9300, 9301.

Health check is `nc -z` on ports 9001, 9002, 9003, 9301 only.

### Cloudflare tunnel

Current intended public hostnames (from `setup-cf-tunnel.sh` / `deploy-master.sh`):

| Hostname | VM service | Port |
|----------|------------|------|
| `dataservice.mrme.tech` | localhost | 9002 |
| `ds-sse.mrme.tech` | localhost | 9001 |
| `ssfx-api.mrme.tech` | localhost | 8000 |
| `ds-control.mrme.tech` | localhost | 9000 |
| `ds-sse.mrme.tech` | localhost | 9001 |
| `dataservice.mrme.tech` | localhost | 9002 |
| `agent.mrme.tech` | localhost | 9003 |
| `pplx-agent.mrme.tech` | localhost | 9004 |
| `ctrader.mrme.tech` | localhost | 9300 |
| `account-hub.mrme.tech` | localhost | 9301 |

The canonical tunnel ingress source of truth is `remote-services/config/tunnel-ingress.json`, applied by `setup_vm.py` or `./dev.sh cf-tunnel-update`. Older `dev/scripts/cf-tunnel-config.json` and `remote-services/setup-cf-tunnel.sh` are deprecated.

---

## 7. Runtime services (`remote-services/`)

All services run in a single Docker container under `supervisord`.

| Service | Port | Purpose |
|---------|------|---------|
| ssfx-server | 8000 | Telegram webhook, admin API, signal ingestion |
| Data service control | 9000 | Market data control API |
| Data service SSE | 9001 | MCP SSE live price/tools |
| Data service REST | 9002 | Market data REST API |
| Agent harness | 9003 | Multi-model AI decision service |
| cTrader unified | 9300 | Legacy/account hub REST |
| Account hub WS | 9301 | Persistent cTrader WebSocket fan-out |

### Configuration

- Primary config file: `/app/config/v2.env` (mounted from `config/v2.env`).
- Data service also reads `/app/config/dataservice.env` and `/app/config/dataservice-config.yml`.
- Market data defaults to **InfluxDB Cloud Serverless**; SQLite is explicit fallback.
- Account hub defaults to **v2** (`account_hub_environment_mode=true` in `ctrader/config.py`).

### cTrader connection model

- **One transport per environment** (live + demo).
- Application auth once per transport; account auth for each managed account.
- Python calls `ctrader-internal` to refresh short-lived access tokens; refresh tokens never leave the Appwrite Function.
- Token refresh uses per-grant `asyncio.Lock` to prevent thundering herds.

### Signal → trade pipeline

1. Telegram channel post arrives at `ssfx-server` webhook.
2. `SignalIntentAgent` classifies intent (new signal / update / orphan / noise).
3. `ssfx_parser` extracts symbol, side, entry, SL/TP.
4. `SignalExperienceStore` scores historical signal quality.
5. `EntryDecisionAgent` approves/rejects/modifies XAUUSD entries.
6. `Follower` executes per-account copy trades via cTrader Open API.
7. `LifecyclePlannerAgent` suggests in-trade actions for follow-up messages.
8. Events are persisted to `ctrader_trading_events` and `account_events`.

---

## 8. Secrets management

### Local

- Root `.env` holds Appwrite bootstrap credentials and third-party API keys.
- `functions/*/.env` files hold function secrets (gitignored but present in working tree).
- `remote-services/config/*.env` files hold runtime secrets.

### GitHub Actions

Set via `./dev.sh setup-gh-secrets`:

| Secret | Source |
|--------|--------|
| `APPWRITE_ENDPOINT` | `.env` |
| `APPWRITE_PROJECT_ID` | `.env` |
| `APPWRITE_API_KEY` | `.env` |
| `CF_API_TOKEN` | Bitwarden or `.env` |
| `CF_ACCOUNT_ID` | `.env` |
| `CF_ZONE_ID` | `.env` |
| `CTRADER_CLIENT_ID` | `.env` |
| `CTRADER_CLIENT_SECRET` | `.env` / function `.env` |
| `TOKEN_ENCRYPTION_KEY` | function `.env` |
| `SESSION_HMAC_KEY` | function `.env` |
| `INTERNAL_API_KEY` | function `.env` |

---

## 9. Key conventions to preserve

- Use **TablesDB** (not deprecated `Databases`) for all new database code.
- Use explicit string column types (`varchar`, `text`, `mediumtext`, `longtext`).
- Prefer object-param calling style for SDK methods.
- Set `Permissions` and `Roles` explicitly; default is no access.
- For SSR auth, use admin client (API key) + session client (cookie) separately.
- Cookie name: `a_session_<PROJECT_ID>`.
- All dev operations go through `dev.sh`.
- CI/CD triggers on `develop`; `main` is stable only.

---

## 10. Critical issues discovered during review

1. `SITES_URL` now points to `app.mrme.tech` (historically pointed to `hq.mrme.tech`).
2. Plaintext PIN reset token logged in `ctrader-pin-auth`.
3. PIN reset flow does not send email.
4. `trade_configs` table permissions allow any user to read/modify any row.
5. Function `.env` files with secrets exist in working tree.
6. `dev/scripts/init/ctrader-oauth.py` abuses `slave_accounts` to store cTrader OAuth config.
7. Init scripts use deprecated Python SDK positional API.
8. `functions/_shared/index.js` exports undefined `CORS_ORIGIN` instead of `CORS_ORIGINS`.
9. Telegram webhook has no signature verification.
10. No per-account risk kill-switches in trading path.
11. `remote-services` deployment uses conflicting directories and stale tunnel config.
12. Several `dev.sh` commands are stubs.

---

## 11. Reference commands

```bash
# Appwrite CLI status
appwrite projects get --project-id 6a22a362002b9ae880bb

# Deploy auth layer
./dev.sh deploy-auth

# Deploy remote services
./dev.sh deploy

# Check tunnel
curl https://auth.mrme.tech/session
curl https://app.mrme.tech/

# AWS VM (canonical deployer: remote-services/setup_vm.py)
ssh aws-ssfx 'cd ~/ssfx-remote-services && docker compose ps'
```
