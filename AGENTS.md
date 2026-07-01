# Project: slwp

## Appwrite Project

- **Endpoint**: `https://sgp.cloud.appwrite.io/v1`
- **Region**: `sgp`
- **Project ID**: `6a22a362002b9ae880bb`
- **Organization**: `685456084475475206c2`

## Tools & Setup

### Appwrite CLI

The CLI is installed at `/opt/homebrew/bin/appwrite` (v20.1.0). The project is linked via `appwrite.json`.

Key commands:
- `appwrite projects get --project-id "6a22a362002b9ae880bb"` – verify project
- `appwrite init project` – re-initialize project config
- `appwrite login` – authenticate (user: HR / mrme000.m0@gmail.com)
- `appwrite client --endpoint https://sgp.cloud.appwrite.io/v1` – switch endpoint

### MCP Servers

Two MCP servers are configured at the project level:

1. **appwrite-api** (`uvx mcp-server-appwrite`) – direct API access to the project
2. **appwrite-docs** (`https://mcp-for-docs.appwrite.io`) – Appwrite documentation search

Environment variables required (see `.env`):
- `APPWRITE_PROJECT_ID`
- `APPWRITE_API_KEY`
- `APPWRITE_ENDPOINT`

### Agent Skills

Appwrite agent skills are installed in `.agents/skills/` for universal access and `.qwen/skills/` for Qwen Code. Available skills:

- `appwrite-typescript` – TypeScript / node-appwrite SDK patterns
- `appwrite-python` – Python SDK patterns
- `appwrite-cli` – CLI commands, deployment, CI mode
- `appwrite-go`, `appwrite-dart`, `appwrite-dotnet`, `appwrite-kotlin`, `appwrite-php`, `appwrite-ruby`, `appwrite-rust`, `appwrite-swift`

Install or update skills:
```bash
npx skills add appwrite/agent-skills --yes --scope project --method symlink
```

## Agent Configuration

All agents read shared instructions from this file and from `.rules`:

- **OpenCode**: loads `AGENTS.md` and `.rules` via the `instructions` array in `opencode.jsonc`.
- **Claude Code**: loads `.claude/CLAUDE.md`, which imports `AGENTS.md` and `.rules`.
- **Qwen Code**: loads `QWEN.md`, which imports `AGENTS.md` and `.rules`. Qwen Code also reads `AGENTS.md` automatically as a fallback.

Keep tool-agnostic rules in `.rules` and project-specific context in `AGENTS.md`.

## Coding Conventions

- Use **TablesDB** (not deprecated `Databases`) for all new database code.
- Prefer object-param calling style for SDK methods (`{ databaseId: '...' }`).
- Use explicit string column types (`varchar`, `text`, `mediumtext`, `longtext`) instead of deprecated `string`.
- Set `Permissions` and `Roles` explicitly on rows/files or at the table/bucket level. Default is no access.
- For SSR auth, use two clients: an **admin client** (API key, reusable) and a **session client** (per-request with cookie).
- Cookie name for sessions: `a_session_<PROJECT_ID>`.
- Handle errors with `AppwriteException` catch blocks.

## Architecture: Appwrite Database as the Source of Truth

- All **user-level** and **system-level** configuration lives in Appwrite Database tables.
- Runtime code, local services, CI/CD, and remote deployments read configuration from Appwrite Database.
- `.env` is reserved only for bootstrap secrets required to connect to Appwrite (`APPWRITE_PROJECT_ID`, `APPWRITE_API_KEY`, `APPWRITE_ENDPOINT`).
- Do **not** store application config, feature flags, or third-party service credentials in `.env`.
- When adding a new configurable value, create or update an Appwrite Database table and provide a script or SDK helper to read it.

## Development Operations: `dev.sh`

All development operations are invoked through `dev.sh <command>`, which delegates to repeatable scripts in `dev/scripts/`. Complex or JSON-heavy scripts are written in Python (`<command>.py`); simple wrappers may be shell (`<command>.sh`).

Common commands:

| Command | Purpose |
|---------|---------|
| `dev.sh start` | Start local development services |
| `dev.sh stop` | Stop local development services |
| `dev.sh status` | Show service status |
| `dev.sh logs` | Tail relevant logs |
| `dev.sh test` | Run tests |
| `dev.sh lint` | Run linting / type checking |
| `dev.sh deploy` | Deploy to the primary remote environment |
| `dev.sh deploy-remote <target>` | Deploy to a specific remote target |
| `dev.sh deploy-status` | Show deployment status |
| `dev.sh deploy-auth` | Deploy cTrader auth functions + site + tables |
| `dev.sh init` | Run all third-party service init scripts |

Do not run ad-hoc commands for these operations; add new commands to `dev/scripts/` and expose them through `dev.sh`.

## Third-Party Service Initialization

- Configure third-party services using repeatable init scripts in `init-scripts/`.
- Each init script reads required setup values from a YAML file (e.g., `init-scripts/config.yml`).
- After collecting values, the init script writes them to Appwrite Database for subsequent use by local and remote runtimes.
- Init scripts must be idempotent and safe to rerun.
- Example flow:
  1. Parse `init-scripts/config.yml`.
  2. Validate required fields.
  3. Upsert configuration rows into Appwrite Database.
  4. Print confirmation with table/row identifiers.

## Remote Deployment Target: Azure VM

Remote deployments run against the currently running Azure VM in the active Azure CLI account.

Current VM (as of last check):

| Property | Value |
|----------|-------|
| Name | `ubuntu-server` |
| Resource Group | `RG-UBUNTU-VM` |
| Location | `westus2` |
| Size | `Standard_D2s_v3` |
| OS Type | Linux |
| Admin Username | `m` |
| Public IP | `172.171.109.137` |
| Power State | `VM running` |

Commands to verify the VM:

```bash
# List running VMs
az vm list --show-details --output table

# Show details for the deployment target
az vm show --name ubuntu-server --resource-group RG-UBUNTU-VM --output table

# Get current public IP
az vm list-ip-addresses --name ubuntu-server --resource-group RG-UBUNTU-VM --output table
```

Deployment scripts in `dev/scripts/` use the Azure CLI to discover the target VM and deploy via SSH. Do not hardcode the VM IP in scripts; resolve it dynamically with `az vm list-ip-addresses` or read it from Appwrite Database after it has been persisted by an init script.

## Public Access: Cloudflare Tunnel on `mrme.tech`

All public access to services running on the Azure VM must go through the Cloudflare Tunnel for the `mrme.tech` domain on account `misterme00@icloud.com`.

### Cloudflare Identifiers

| Item | Value |
|------|-------|
| Account | `misterme00@icloud.com` |
| Account ID | `4f6d43db5dbe773f750a2c8f941d0cdc` |
| Domain / Zone | `mrme.tech` |
| Zone ID | `5290d99f626b08c46c1eca6cc7cfa090` |
| Tunnel Name | `ssfx_azurue` |
| Tunnel ID | `d1e96e86-a44a-457a-a60c-e7d5d5d675bd` |
| Remote Host | `172.171.109.137` (user `m`) |
| CF Dashboard | `https://dash.cloudflare.com/4f6d43db5dbe773f750a2c8f941d0cdc/one/networks/connectors/cloudflare-tunnels/cloudflared/d1e96e86-a44a-457a-a60c-e7d5d5d675bd/edit/overview` |

### Credentials

Cloudflare API tokens are stored in the Bitwarden vault item **"Cloudflare — mrme.tech"**. Do not commit tokens to git. Scripts should retrieve the token from Bitwarden or from an environment variable set by `.env` / `init-scripts`.

```bash
# Retrieve CF API token from Bitwarden
export BW_PASSWORD=$(security find-generic-password -a "bw-master-password" -w)
export BW_SESSION=$(bw unlock --passwordenv BW_PASSWORD --raw)
unset BW_PASSWORD
CF_API_TOKEN=$(bw get item "Cloudflare — mrme.tech" | jq -r '.fields[] | select(.name=="api_token") | .value')
```

### Current Tunnel Ingress (Public Hostnames)

| Hostname | Local Service | Purpose |
|----------|---------------|---------|
| `dataservice.mrme.tech` | `http://localhost:9099` | Market data REST API |
| `ds-sse.mrme.tech` | `http://localhost:9001` | MCP SSE live price/tools |
| `admin.mrme.tech` | `http://ubuntu-server:8100` | Admin panel |
| catch-all | `http_status:404` | — |

New public services on the Azure VM must be added as ingress rules on this tunnel and as proxied CNAME records in the `mrme.tech` zone.

### Commands

```bash
# Check tunnel status
./dev.sh cf-tunnel-status

# Update tunnel ingress rules
./dev.sh cf-tunnel-update

# Manual: list tunnels
curl -s "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/cfd_tunnel" \
  -H "Authorization: Bearer $CF_API_TOKEN" | jq '.result[] | {id, name, status, connections: (.connections | length)}'

# Manual: show tunnel ingress
curl -s "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/cfd_tunnel/$TUNNEL_ID/configurations" \
  -H "Authorization: Bearer $CF_API_TOKEN" | jq '.result.config.ingress'

# Manual: list DNS records for mrme.tech
curl -s "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records?per_page=100" \
  -H "Authorization: Bearer $CF_API_TOKEN" | jq '.result[] | "\(.type) \(.name) -> \(.content) (proxied: \(.proxied))"'
```

### Adding a New Public Service

1. Decide the subdomain (e.g., `api.mrme.tech`).
2. Ensure the service is running on the Azure VM and listening on a local port.
3. Add a tunnel ingress rule mapping the subdomain to the local service.
4. Add a proxied CNAME record: `api.mrme.tech` → `<tunnel-id>.cfargotunnel.com`.
5. Verify with `curl https://api.mrme.tech/health`.

Use `dev.sh cf-tunnel-update` (implemented in `dev/scripts/cf_tunnel_update.py`) to apply ingress changes idempotently.

## Email: Resend

The project uses Resend for transactional and agent-triggered email. The Resend CLI is installed and configured on this machine.

### Configuration

- **API Key:** stored in `.env` as `RESEND_API_KEY` (provided key has sending access).
- **Sending domain:** `email.mrme.tech`
- **Default from address:** `noreply@email.mrme.tech`
- **Env vars:** `RESEND_API_KEY`, `RESEND_FROM_DOMAIN`, `RESEND_FROM_EMAIL` are loaded by `sa.sh` before starting any agent.

### Resend CLI Status

```bash
# Check CLI / API key status
resend doctor

# Show current authenticated account
resend whoami
```

### Sending Email

Agents can send email via the Resend CLI:

```bash
# Plain text email
resend emails send \
  --from "App <noreply@email.mrme.tech>" \
  --to user@example.com \
  --subject "Subject" \
  --text "Body"

# HTML email
resend emails send \
  --from "App <noreply@email.mrme.tech>" \
  --to user@example.com \
  --subject "Subject" \
  --html "<p>Body</p>"

# Pipe content from stdin
echo "Body" | resend emails send \
  --from "App <noreply@email.mrme.tech>" \
  --to user@example.com \
  --subject "Subject" \
  --text-file -
```

Use the helper script for project-standard sending:

```bash
./dev.sh resend-send user@example.com "Subject" "Body"
```

### Domain Setup Note

The provided API key has **sending access only**. Domain creation and DNS verification in Resend require a **full access** API key. To verify `email.mrme.tech`:

1. Create a full-access API key at https://resend.com/api-keys.
2. Run `resend domains create --name email.mrme.tech`.
3. Add the DNS records Resend provides to the `mrme.tech` zone (Cloudflare).
4. Run `resend domains verify <domain-id>`.

Until the domain is verified, emails can still be sent from Resend's shared domains for testing, but production sends should use `email.mrme.tech`.

## cTrader + Appwrite Auth Layer

Replaces `cf-auth-broker` (Cloudflare Worker) with Appwrite Functions + TablesDB-backed static site. The Python backends in `/Volumes/ExMac/code/ssfx/v2` and `/Volumes/ExMac/code/ssfx/dataservice` consume grant handles via `CTRADER_AUTH_BROKER_URL` exactly as before.

### Architecture

- **grant_id**: opaque handle for a cTrader token pair. Refresh tokens are AES-GCM-256 encrypted at rest and never leave the Function. Python receives only short-lived access tokens.
- **Connection model**: one grant_id ≡ one cTID ≡ one accessToken ≡ one persistent TCP connection in the Python runtime's live connection pool. The auth layer mints and rotates grant_ids; the Python side handles account enumeration, auth, and trading.

### TablesDB Schema (database: `ctrader_auth`)

| Table | Purpose |
|-------|---------|
| `slave_accounts` | Identity + encrypted tokens (merges grants + master/slave identity) |
| `trade_configs` | Per-slave trade settings (lot size, multiplier, drawdown, symbols, copy_enabled) |
| `master_signals` | Signal broadcast table (optional) |
| `ephemeral_tokens` | Short-lived tokens: `oauth_state` and `pin_reset` |
| `grant_locks` | Row-level distributed locks for token refresh (row $id = grant_id) |

### Appwrite Functions

| Function | Purpose | Endpoints |
|----------|---------|-----------|
| `ctrader-auth` | OAuth start/callback, session check, logout | `GET /auth/ctrader/start`, `GET /callback`, `GET /session`, `POST /logout` |
| `ctrader-pin-auth` | PIN login, set username+PIN, PIN reset | `POST /pin-login`, `POST /set-credentials`, `POST /pin-reset/request`, `POST /pin-reset/confirm` |
| `ctrader-internal` | Server-to-server for Python backends (gated by `x-internal-key`) | `POST /internal/ctrader/refresh`, `GET /internal/grant/latest`, `POST /internal/grant/:grant_id/accounts` |
| `ctrader-token-refresh-worker` | Scheduled cron (daily 03:00) + on-demand HTTP for rotating near-expiry tokens and sweeping stale ephemeral_tokens | `GET /` (HTTP trigger) |

### Appwrite Site

| Site | Purpose |
|------|---------|
| `ctrader-auth-site` | Static SPA (plain HTML/JS) with hash routing: landing, onboarding, login, slave dashboard, master dashboard. Uses Appwrite Web SDK + Realtime. |

### Auth Flow

1. **New slave**: clicks Connect → `ctrader-auth` redirects to cTrader consent → callback exchanges code → creates Appwrite user + `slave_accounts` row with encrypted tokens → sets `a_session_<PROJECT_ID>` cookie → redirects to site onboarding.
2. **Onboarding**: slave sets username + PIN via `ctrader-pin-auth` /set-credentials.
3. **Login**: slave/master enter username + PIN → `ctrader-pin-auth` verifies, creates Appwrite session cookie → redirected to dashboard.
4. **Python backend**: calls `POST /internal/ctrader/refresh` with `x-internal-key`, gets access_token, then uses `ctrader-open-api` locally for account list and trading.
5. **Master**: username `admin` + PIN. Created by `init-scripts/admin-pin.sh`. Can view all slaves via master dashboard.

### Deployment

```bash
# 1. Push tables + functions + site
./dev.sh deploy-auth

# 2. Set up master admin PIN
./init-scripts/admin-pin.sh

# 3. Configure cTrader OAuth (reads init-scripts/config.yml)
./init-scripts/ctrader-oauth.sh

# 4. Register the ctrader-auth /callback domain in openapi.ctrader.com
# 5. Update sites/ctrader-auth-site/config.js with deployed Function domains
# 6. E2E test: SPA → start → consent → callback → grant stored + session
```

### Function Variables (secrets)

Set via `.env` files in each function directory (see `.env.example` files). Push with `--with-variables`.

- `CTRADER_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY`, `SESSION_HMAC_KEY` → `ctrader-auth`
- `INTERNAL_API_KEY` → `ctrader-internal`
- `APPWRITE_PROJECT_ID`, `APPWRITE_API_KEY`, `APPWRITE_ENDPOINT`, `CTRADER_AUTH_DATABASE_ID` → all functions

### Python Backend Migration

- `CTRADER_AUTH_BROKER_URL` → `ctrader-internal` Function domain
- Add `CTRADER_AUTH_INTERNAL_KEY` env var
- Remove calls to old `/internal/ctrader/accounts` and `/internal/ctrader/account-balance`; use `ctrader-open-api` locally with the returned `access_token`
- Call `POST /internal/grant/:grant_id/accounts` once after first refresh to persist account IDs

## Project Files

| File | Purpose |
|------|---------|
| `appwrite.json` | Appwrite CLI project config |
| `opencode.jsonc` | OpenCode MCP + project settings |
| `.mcp.json` | Claude Code project-level MCP |
| `.qwen/settings.json` | Qwen Code project-level MCP |
| `.claude/CLAUDE.md` | Claude Code project instructions (imports AGENTS.md + .rules) |
| `QWEN.md` | Qwen Code project instructions (imports AGENTS.md + .rules) |
| `.rules` | Cross-agent rules (Appwrite-as-config-source, dev.sh, init scripts) |
| `dev.sh` | Dispatcher for development operations |
| `dev/scripts/` | Repeatable scripts for dev operations |
| `init-scripts/` | Repeatable init scripts for third-party services |
| `resend` | Resend CLI (global) — email sending, configured with `.env` |
| `.env` | Environment variables (credentials) – **not committed** |
| `.gitignore` | Excludes `.env`, `.DS_Store`, logs |
| `AGENTS.md` | This file – project context for all AI agents |
