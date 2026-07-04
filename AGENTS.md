# Project: slwp

## Appwrite Project

- **Endpoint**: `https://sgp.cloud.appwrite.io/v1`
- **Region**: `sgp`
- **Project ID**: `6a22a362002b9ae880bb`
- **Organization**: `685456084475475206c2`

## Tools & Setup

### Appwrite CLI

The CLI is installed at `/opt/homebrew/bin/appwrite` (v22.3.0). The project is linked via `appwrite.config.json`.

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
- **Qwen Code models**: configured in `.qwen/settings.json` to use `agentrouter.org` with `glm-5.2` (default) and `gpt-5.5`. The `OPENAI_API_KEY` is loaded from `.env` via `sa.sh`.

### Mise / Tooling Environment

- This machine uses **mise** to manage Node, Python, Go, `uv`, and other tools.
- `uv` / `uvx` are invoked via mise shims at `/Users/m/.local/share/mise/shims/`.
- The shim requires `MISE_DATA_DIR=/Volumes/Spare/mise` to resolve actual tool binaries. This variable is set in `.env` and exported by `sa.sh` before starting any agent.
- If an MCP server or script fails to find `uvx`, ensure `MISE_DATA_DIR` is exported in the agent's environment.

Keep tool-agnostic rules in `.rules` and project-specific context in `AGENTS.md`.

## Coding Conventions

- Use **TablesDB** (not deprecated `Databases`) for all new database code.
- Prefer object-param calling style for SDK methods (`{ databaseId: '...' }`).
- Use explicit string column types (`varchar`, `text`, `mediumtext`, `longtext`) instead of deprecated `string`.
- Set `Permissions` and `Roles` explicitly on rows/files or at the table/bucket level. Default is no access.
- For SSR auth, use two clients: an **admin client** (API key, reusable) and a **session client** (per-request with cookie).
- Cookie name for sessions: `a_session_<PROJECT_ID>`.
- Handle errors with `AppwriteException` catch blocks.

## Security Requirements

### Critical Security Rules (Production Readiness)

1. **No plaintext logging of sensitive data** - Tokens, passwords, PINs, API keys, or credentials must never be logged. Use redaction or omit from logs entirely.
2. **Row-level permissions required** - When `rowSecurity: true` on a table, always set explicit row-level permissions when creating rows: `Permission.read(Role.user(userId))`, `Permission.update(Role.user(userId))`, `Permission.delete(Role.user(userId))`.
3. **Avoid broad table permissions** - Do not use `create("any")` or `create("users")` at the table level. Use row-level permissions or admin-only creation via functions.
4. **Webhook signature verification mandatory** - All external webhooks (Telegram, etc.) must verify the signature/secret token before processing payloads. Use `X-Telegram-Bot-Api-Secret-Token` header for Telegram.
5. **Session validation via HTTP** - To validate user sessions, make HTTP requests to Appwrite's `/account` endpoint with the session cookie. Do not call `setSession()` on an admin client.
6. **Proper config storage** - Store third-party service credentials and configuration in dedicated config tables (e.g., `service_config`), not in user/data tables like `slave_accounts`.

### Fixed Issues (2026-07-02)

All 8 critical production blockers have been resolved:
- ✅ OAuth redirect target corrected to `https://app.mrme.tech`
- ✅ PIN reset tokens no longer logged in plaintext
- ✅ PIN reset emails now send via Resend API (when `RESEND_API_KEY` configured)
- ✅ `trade_configs` table permissions fixed with row-level access
- ✅ No function `.env` files with secrets in working tree
- ✅ cTrader OAuth config moved from `slave_accounts` to `service_config` table
- ✅ Telegram webhook signature verification implemented
- ✅ Per-account risk kill-switches added to schema (`max_open_risk_pct`, `max_positions`, `trading_enabled`)

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
| `dev.sh deploy-auth` | Deploy cTrader auth functions + site + tables (local, manual) |
| `dev.sh setup-gh-secrets` | Fetch CF token from Bitwarden, set all GitHub Actions secrets, populate .env |
| `dev.sh init` | Run all third-party service init scripts |

Do not run ad-hoc commands for these operations; add new commands to `dev/scripts/` and expose them through `dev.sh`.

## Third-Party Service Initialization

- Configure third-party services using repeatable init scripts in `dev/scripts/init/.
- Each init script reads required setup values from a YAML file (e.g., `dev/scripts/init/config.yml`).
- After collecting values, the init script writes them to Appwrite Database for subsequent use by local and remote runtimes.
- Init scripts must be idempotent and safe to rerun.
- Example flow:
  1. Parse `dev/scripts/init/config.yml`.
  2. Validate required fields.
  3. Upsert configuration rows into Appwrite Database.
  4. Print confirmation with table/row identifiers.

## Remote Deployment

The SSFX backend runs as a single Docker Compose stack on a Linux VM. A single helper script provisions a fresh VM end-to-end and keeps the tunnel ingress in sync.

> **TODO:** Replace the ad-hoc SSH + shell helpers with an Ansible playbook for post-VM configuration. The playbook should cover Docker, cloudflared, and stack deployment so that switching clouds requires only an inventory change. Until then, use `setup_vm.py`.

### One-shot fresh-VM setup

```bash
# Copy the example and point at your VM (usually only SSH_HOST changes)
cp remote-services/config/vm.env.example remote-services/config/vm.env
# edit remote-services/config/vm.env

# Provision Docker, cloudflared, and deploy the stack
python3 dev/scripts/setup_vm.py
# or the thin shell wrapper:
# ./dev/scripts/setup-vm.sh
```

`dev/scripts/setup_vm.py` is the canonical reference for agents. It:
1. Installs Docker, Docker Compose plugin, and Docker Buildx.
2. Installs and registers `cloudflared` for the existing Cloudflare tunnel.
3. Updates the tunnel ingress from `remote-services/config/tunnel-ingress.json`.
4. Syncs `remote-services/` and `pplx-agent/` to the VM.
5. Builds and starts the Docker stack.
6. Verifies public health endpoints.

### Switching to a new VM (2–3 changes)

Edit `remote-services/config/vm.env`:

```bash
# 1. SSH target: alias from ~/.ssh/config or user@ip
SSH_HOST=aws-ssfx

# 2. (only if user is not in the SSH alias) VM_USER=ec2-user

# 3. (only if OS is not auto-detected) VM_OS_FAMILY=amazonlinux
```

Supported `VM_OS_FAMILY` values: `amazonlinux`, `rhel`, `ubuntu`.

### Historical / deprecated target

The original Azure VM (`ubuntu-server` in `RG-UBUNTU-VM`, `172.171.109.137`, user `m`) has been deallocated. Use `setup_vm.py` (or `setup-vm.sh`) against the current target instead. The older `deploy-azure.sh` and Azure-specific deployment scripts were removed during consolidation.

## Public Access: Cloudflare Tunnel on `mrme.tech`

All public access to remote services goes through the Cloudflare Tunnel for the `mrme.tech` domain on account `misterme00@icloud.com`.

### Cloudflare Identifiers

| Item | Value |
|------|-------|
| Account | `misterme00@icloud.com` |
| Account ID | `4f6d43db5dbe773f750a2c8f941d0cdc` |
| Domain / Zone | `mrme.tech` |
| Zone ID | `5290d99f626b08c46c1eca6cc7cfa090` |
| Tunnel Name | `ssfx_azurue` |
| Tunnel ID | `d1e96e86-a44a-457a-a60c-e7d5d5d675bd` |
| CF Dashboard | `https://dash.cloudflare.com/4f6d43db5dbe773f750a2c8f941d0cdc/one/networks/connectors/cloudflare-tunnels/cloudflared/d1e96e86-a44a-457a-a60c-e7d5d5d675bd/edit/overview` |

### Credentials

Cloudflare API tokens are stored in the Bitwarden vault item **"Cloudflare — mrme.tech"**. Do not commit tokens to git. Scripts retrieve the token from `.env` or Bitwarden.

```bash
# Retrieve CF API token from Bitwarden
export BW_PASSWORD=$(security find-generic-password -a "bw-master-password" -w)
export BW_SESSION=$(bw unlock --passwordenv BW_PASSWORD --raw)
unset BW_PASSWORD
CF_API_TOKEN=$(bw get item "Cloudflare — mrme.tech" | jq -r '.fields[] | select(.name=="api_token") | .value')
```

### Current Tunnel Ingress (Public Hostnames)

Source of truth: `remote-services/config/tunnel-ingress.json`.

| Hostname | Local Service | Purpose |
|----------|---------------|---------|
| `api.mrme.tech` (legacy `ssfx-api.mrme.tech`) | `http://localhost:8000` | Telegram webhook + cTrader slave admin |
| `ds-control.mrme.tech` | `http://localhost:9000` | DataService control API |
| `ds-sse.mrme.tech` | `http://localhost:9001` | MCP SSE live price/tools |
| `market.mrme.tech` (legacy `dataservice.mrme.tech`) | `http://localhost:9002` | Market data OpenPI REST API |
| `ai.mrme.tech` (legacy `agent.mrme.tech`) | `http://localhost:9003` | AI agent harness (XAUUSD decision layer) |
| `research.mrme.tech` (legacy `pplx-agent.mrme.tech`) | `http://localhost:9004` | Perplexity gold-market research agent |
| `ctrader.mrme.tech` | `http://localhost:9300` | cTrader unified service |
| `account-hub.mrme.tech` | `http://localhost:9301` | Account hub WebSocket server |
| catch-all | `http_status:404` | — |

### Commands

```bash
# Check tunnel status
./dev.sh cf-tunnel-status

# Update tunnel ingress rules from remote-services/config/tunnel-ingress.json
./dev.sh cf-tunnel-update

# One-shot fresh-VM provision + deploy + tunnel sync
python3 dev/scripts/setup_vm.py

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
2. Ensure the service is running on the VM and listening on a local port.
3. Add a tunnel ingress rule to `remote-services/config/tunnel-ingress.json` mapping the subdomain to the local service.
4. Ensure a proxied CNAME record exists: `api.mrme.tech` → `<tunnel-id>.cfargotunnel.com` (use the Cloudflare dashboard or API).
5. Run `python3 dev/scripts/setup_vm.py` or `./dev.sh cf-tunnel-update` to apply the ingress change.
6. Verify with `curl https://api.mrme.tech/health`.

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

## Web Scraping: Firecrawl

The project uses Firecrawl CLI for web scraping, searching, crawling, and browser interaction. The CLI is installed globally and skills are installed at user level for OpenCode, Claude Code, and Qwen Code.

### Configuration

- **API Key:** stored in `.env` as `FIRECRAWL_API_KEY` (loaded by `sa.sh`).
- **Telemetry:** disabled via `FIRECRAWL_NO_TELEMETRY=1` in `.env`.
- **CLI config:** persisted at `~/.firecrawl/` via `firecrawl login --api-key`.
- **Skills:** installed in `~/.agents/skills/`, `~/.claude/skills/`, and `~/.qwen/skills/` for all three agents.

### CLI Status

```bash
# Check installation, auth, and rate limits
firecrawl --status

# View current configuration
firecrawl view-config

# Check credit usage
firecrawl credit-usage
```

### Common Commands

```bash
# Scrape a single page to markdown
firecrawl scrape https://example.com

# Search the web
firecrawl search "Appwrite TablesDB documentation"

# Crawl an entire site section
firecrawl crawl https://docs.example.com/api --limit 50

# Map all URLs on a site
firecrawl map https://example.com

# Interact with a page (clicks, form fills, login flows)
firecrawl interact https://example.com/login --prompt "Fill login form and submit"
```

### Agent Skills

Firecrawl skills are available at user level for all agents. Key skills:

| Skill | Purpose |
|-------|---------|
| `firecrawl-scrape` | Extract markdown/HTML from a single URL |
| `firecrawl-search` | Web search with full page content |
| `firecrawl-crawl` | Bulk extract from entire site sections |
| `firecrawl-map` | Discover and list all URLs on a site |
| `firecrawl-interact` | Browser interaction (clicks, forms, login) |
| `firecrawl-parse` | Parse local files (PDF, DOCX, XLSX) to markdown |
| `firecrawl-deep-research` | Cited analytical reports from web research |
| `firecrawl-workflows` | Outcome-focused deliverables (SEO audits, lead lists, etc.) |

### Reinstalling Skills

```bash
# Install all firecrawl skills to all detected AI agents
npx -y firecrawl-cli@latest init --all
```

## Plugin: appwrite-ctrader

A user-level plugin at `~/.claude/plugins/appwrite-ctrader/` provides skills, commands, agents, and hooks for this project. All three agents (Claude Code, OpenCode, Qwen Code) load it when working in this repository.

### How Each Agent Loads the Plugin

| Agent | Mechanism |
|-------|-----------|
| **Claude Code** | `.claude/settings.json` enables the plugin via `plugins.user` + hooks from `hooks/hooks.json` |
| **OpenCode** | 7 skills symlinked from plugin to `.agents/skills/` (auto-discovered) |
| **Qwen Code** | 7 skills symlinked from plugin to `.qwen/skills/` and `~/.qwen/skills/` (auto-discovered) |

### Skills (7) — available to all agents

| Skill | Purpose |
|-------|---------|
| `appwrite-functions` | Create, deploy, and manage Appwrite Functions |
| `appwrite-tablesdb` | Create TablesDB tables with correct column types and permissions |
| `appwrite-sites` | Deploy and extend the auth/admin SPA |
| `appwrite-cicd` | Hybrid GitHub Actions + Appwrite git deployment pipeline |
| `auth-oauth` | OAuth flow, PIN login, grant_id token management, encrypted storage |
| `ctrader-trading` | TG signal ingestion, copy trading, position monitoring, master-slave execution |
| `pplx-agent` | Perplexity + TradingView gold market research and Space management |

> **Downstream signal integration:** see `cpr00.md` for how the `alwaydata` Telegram forwarder (upstream) delivers signals to this runtime (downstream) — via Telegram Bot API webhook, direct HTTP push (`SIGNAL_WEBHOOK_URL`), or long-polling fallback. Includes the `SignalParser` classification contract, promo-filtering guarantees, and HMAC-signed payload schema.

### Commands (7) — Claude Code slash commands

| Command | Purpose |
|---------|---------|
| `/deploy-auth` | Deploy auth layer (functions + site + tables) |
| `/deploy-functions` | Deploy one or all Appwrite Functions |
| `/deploy-site` | Deploy the static auth/admin site |
| `/deploy-all` | Full deployment + health checks |
| `/init-service` | Initialize third-party services (TG, OAuth, admin) |
| `/status` | System status across all components |
| `/create-table` | Create TablesDB table with permissions |

OpenCode and Qwen Code agents should use the equivalent `dev.sh` commands (e.g., `./dev.sh deploy-auth`) for the same functionality.

### Sub-Agents (3) — Claude Code only

| Agent | Purpose |
|-------|---------|
| `appwrite-deployer` | Deployment specialist for Functions, Sites, TablesDB |
| `appwrite-reviewer` | Code review for Appwrite SDK patterns, security, TablesDB |
| `trade-system-architect` | Architecture design for trading system components |

### Hooks (2) — Claude Code only

| Hook | Event | Action |
|------|-------|--------|
| `validate_appwrite_code` | PreToolUse (Write/Edit in `functions/`) | Check TablesDB patterns, SDK style, security |
| `post_deploy_check` | PostToolUse (Bash) | Health check deployed functions |

### Re-symlink Plugin Skills

```bash
# Run from project root to re-link skills after plugin updates
PLUGIN_SKILLS="$HOME/.claude/plugins/appwrite-ctrader/skills"
for skill in appwrite-cicd appwrite-functions appwrite-sites appwrite-tablesdb ctrader-auth ctrader-trading pplx-agent; do
# Note: ctrader-auth skill name kept for backwards compatibility with plugin.
  ln -sfn "${PLUGIN_SKILLS}/${skill}" ".agents/skills/${skill}"
  ln -sfn "${PLUGIN_SKILLS}/${skill}" ".qwen/skills/${skill}"
  ln -sfn "${PLUGIN_SKILLS}/${skill}" "$HOME/.qwen/skills/${skill}"
  ln -sfn "${PLUGIN_SKILLS}/${skill}" "$HOME/.agents/skills/${skill}"
done
```

## cTrader + Appwrite Auth Layer

Replaces `cf-auth-broker` (Cloudflare Worker) with Appwrite Functions + TablesDB-backed static site. The containerized Python services in `remote-services/` are self-contained and consume grant handles via `CTRADER_AUTH_BROKER_URL` using the bundled `ctrader_client/` library — no external dependency on `/Volumes/ExMac/code/ssfx/v2` or `/Volumes/ExMac/code/ssfx/dataservice`.

### Architecture

- **grant_id**: opaque handle for a cTrader token pair. Refresh tokens are AES-GCM-256 encrypted at rest and never leave the Function. Python receives only short-lived access tokens.
- **Connection model**: one grant_id ≡ one cTID ≡ one accessToken ≡ one persistent TCP connection in the Python runtime's live connection pool. The auth layer mints and rotates grant_ids; the Python side handles account enumeration, auth, and trading.

### TablesDB Schema (database: `ctrader_auth`)

| Table | Purpose | Layer |
|-------|---------|-------|
| `slave_accounts` | Identity + encrypted tokens (merges grants + master/slave identity) | Auth |
| `trade_configs` | Per-slave trade settings (lot size, multiplier, drawdown, symbols, copy_enabled) | Auth |
| `accounts` | Discovered cTrader trading accounts (ctidTraderAccountId, isLive, brokerTitleShort, selected) — populated by Python account hub after first connect | Runtime |
| `account_events` | Real-time account state: positions, orders, balance, equity, margin — written by account hub, read by dashboards | Runtime |
| `ctrader_trading_events` | Trading operation event log (order filled, position closed, errors) — written by ctrader service | Runtime |
| `master_signals` | Signal broadcast table for master-to-slave copy trading (optional) | Trading |
| `ssfx_accounts` | Telegram signal FOLLOWER configuration (name, enabled, host_type, config_json with symbol filters, SL/TP, lot settings) | Trading |
| `ssfx_executions` | Signal execution history per slave (account_name, chat_id, message_id, status, order_id, position_id, price, volume) | Trading |
| `ephemeral_tokens` | Short-lived tokens: `oauth_state` and `pin_reset` | Auth |
| `grant_locks` | Row-level distributed locks for token refresh (row $id = grant_id) | Auth |

**Note on MongoDB**: MongoDB is **legacy** and no longer required. The system uses Appwrite TablesDB for all configuration and account state, and SQLite/InfluxDB for time-series market data. The `ssfx-server` will fall back to a no-op store if MongoDB is unavailable, but full functionality requires the `ssfx_accounts` table in Appwrite.

### Appwrite Functions

| Function | Purpose | Endpoints |
|----------|---------|-----------|
| `auth-oauth` | OAuth start/callback, session check, logout | `GET /auth/ctrader/start`, `GET /callback`, `GET /session`, `POST /logout` |
| `auth-pin` | PIN login, set username+PIN, PIN reset | `POST /pin-login`, `POST /set-credentials`, `POST /pin-reset/request`, `POST /pin-reset/confirm` |
| `api-internal` | Server-to-server for Python backends (gated by `x-internal-key`) | `POST /internal/ctrader/refresh`, `GET /internal/grant/latest`, `POST /internal/grant/:grant_id/accounts` |
| `token-refresh` | Scheduled cron (daily 03:00) + on-demand HTTP for rotating near-expiry tokens and sweeping stale ephemeral_tokens | `GET /` (HTTP trigger) |

### Appwrite Sites

| Site | Purpose |
|------|---------|
| Static auth/admin SPA (superseded by `ssfx-hq`) | Static SPA (plain HTML/JS) with hash routing: landing, onboarding, login, slave dashboard, master dashboard. Uses Appwrite Web SDK + Realtime. |
| `ssfx-hq` | Consolidated command/dashboard SPA served at `https://app.mrme.tech`. Calls the SSFX v2 admin API, agent harness, and market data service. |

### Auth Flow

1. **New slave**: clicks Connect → `auth-oauth` redirects to cTrader consent → callback exchanges code → creates Appwrite user + `slave_accounts` row with encrypted tokens → sets `a_session_<PROJECT_ID>` cookie → redirects to site onboarding.
2. **Onboarding**: slave sets username + PIN via `auth-pin` /set-credentials.
3. **Login**: slave/master enter username + PIN → `auth-pin` verifies, creates Appwrite session cookie → redirected to dashboard.
4. **Python backend**: calls `POST /internal/ctrader/refresh` with `x-internal-key`, gets access_token, then uses `ctrader-open-api` locally for account list and trading.
5. **Master**: username `admin` + PIN. Created by `dev/scripts/init/admin-pin.sh`. Can view all slaves via master dashboard.

### Account Discovery & Sync Process

The AccountHub v2 service automatically discovers and syncs cTrader accounts:

```
AccountHubV2 (every 30s) → Poll slave_accounts table → Discover active slaves
    ↓
AccountDiscovery → Find accounts in accounts table or parse ctrader_account_ids
    ↓
EnvironmentConnection.authorize_account() → Sync to Appwrite broker
    ↓
POST /internal/grant/:grant_id/accounts → Update accounts table
    ↓
Admin dashboard queries accounts table → Display account details
```

**Key Components**:
- **AccountDiscovery**: Polls `slave_accounts` for active slaves and their accounts
- **EnvironmentConnection**: Shared cTrader transport per environment (live/demo)
- **Account Sync**: Calls `sync_accounts_to_broker()` after authorization to persist account data

**Troubleshooting**: If accounts don't appear in dashboard:
1. Check AccountHub logs: `docker compose logs account-hub | grep "Synced account"`
2. Verify `/internal/grant/:grant_id/accounts` endpoint connectivity
3. Confirm `accounts` table has data for the grant_id
4. Ensure `TOKEN_ENCRYPTION_KEY` matches between functions and services

### CI/CD Pipeline

Deployment is fully automated via **GitHub Actions** on push to the `develop` branch.

**Workflow**: `.github/workflows/deploy.yml`

| Job | Trigger | Purpose |
|-----|---------|---------|
| `deploy-tables` | Push to `develop` | Push TablesDB schema |
| `deploy-functions` | Push to `develop` | Sync shared code → lint → create function deployments → upsert variables → activate → set cron |
| `deploy-site` | Push to `develop` | Create site deployment and activate |
| `verify-domains` | After deploy jobs | Check custom domain status |
| `smoke-test` | After deploy jobs | Health check `auth.mrme.tech`, `app.mrme.tech` |
| `cleanup` | After deploy jobs | Remove orphaned Appwrite resources |

**Deployment flow**:
1. Developer pushes to `develop` branch
2. GitHub Actions runs `deploy-tables` first, then `deploy-functions` and `deploy-site` in parallel
3. `deploy-functions` calls `dev/scripts/deploy/deploy_auth.py --functions`, which creates each function deployment with `appwrite functions create-deployment`, upserts variables by variable ID, activates the deployment, and sets the worker cron schedule
4. `deploy-site` calls `dev/scripts/deploy/deploy_auth.py --site`, which upserts site build variables, creates the site deployment with `appwrite sites create-deployment`, and activates it
5. `smoke-test` verifies all endpoints are healthy
6. `cleanup` removes old deployments

**Branch strategy**:
- `main` — stable, protected. Manual merges from `develop` for releases
- `develop` — CI/CD active branch, all changes go here first

**Functions are NOT connected to VCS** (no auto-deploy from git pushes). GitHub Actions controls all deployments via the Appwrite CLI using API key auth.

**GitHub Secrets required** (set via `./dev.sh setup-gh-secrets`):

| Secret | Purpose |
|--------|---------|
| `APPWRITE_ENDPOINT` | Appwrite endpoint |
| `APPWRITE_PROJECT_ID` | Appwrite project ID |
| `APPWRITE_API_KEY` | Appwrite API key |
| `CF_API_TOKEN` | Cloudflare API token (fetched from Bitwarden) |
| `CF_ACCOUNT_ID` | Cloudflare account ID |
| `CF_ZONE_ID` | Cloudflare zone ID for mrme.tech |
| `CTRADER_CLIENT_ID` | cTrader OAuth client ID |
| `CTRADER_CLIENT_SECRET` | cTrader OAuth client secret |
| `TOKEN_ENCRYPTION_KEY` | AES-GCM-256 key for token encryption |
| `SESSION_HMAC_KEY` | HMAC key for session state signing |
| `INTERNAL_API_KEY` | Internal API key for `api-internal` |
| `V2_ADMIN_KEY` | Admin key for the `ssfx-hq` dashboard to call `ssfx-api` `/api/*` endpoints. Defaults to `ADMIN_API_KEY` from `.env` during `./dev.sh setup-gh-secrets`. |

### Manual Deployment (one-time setup)

```bash
# 1. Push tables + functions + site (initial setup or manual override)
./dev.sh deploy-auth

# 2. Set up master admin PIN
./dev/scripts/init/admin-pin.sh

# 3. Configure cTrader OAuth (reads dev/scripts/init/config.yml)
./dev/scripts/init/ctrader-oauth.sh

# 4. Register the auth-oauth /callback domain in openapi.ctrader.com
# 5. Update sites/ssfx-hq/config.js with deployed Function domains
#    (ssfx-hq reads V2_ADMIN_KEY / ADMIN_API_KEY at build time so the SPA can call /api/* endpoints)
# 6. E2E test: SPA → start → consent → callback → grant stored + session
```

### Function Variables (secrets)

Variables are upserted by `dev/scripts/deploy/deploy_auth.py` from environment values (local `.env` or GitHub Secrets). They are not read from per-function `.env` files.

- `CTRADER_CLIENT_ID`, `CTRADER_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY`, `SESSION_HMAC_KEY`, `SITES_URL` → `auth-oauth`
- `INTERNAL_API_KEY` → `api-internal`
- `BCRYPT_SALT_ROUNDS` → `auth-pin`
- `REFRESH_BUFFER_HOURS` → `token-refresh`
- `APPWRITE_PROJECT_ID`, `APPWRITE_API_KEY`, `APPWRITE_ENDPOINT`, `CTRADER_AUTH_DATABASE_ID` → all functions

### Site Variables (secrets)

Site build variables are upserted by `dev/scripts/deploy/deploy_auth.py` before each site deployment:

- `ADMIN_API_KEY` → `ssfx-hq` (build-time; injected into `config.js` as `v2AdminKey` for `x-admin-key` authentication against `ssfx-api`)

### Python Backend Migration

- `CTRADER_AUTH_BROKER_URL` → `api-internal` Function domain
- Add `CTRADER_AUTH_INTERNAL_KEY` env var
- Remove calls to old `/internal/ctrader/accounts` and `/internal/ctrader/account-balance`; use `ctrader-open-api` locally with the returned `access_token`
- Call `POST /internal/grant/:grant_id/accounts` once after first refresh to persist account IDs

## cTrader Runtime Services: Account Hub + Data Service

The Python runtime services in `remote-services/` now support an Appwrite-native mode:

- **Account Hub v2** maintains exactly two cTrader transports (live + demo), discovers active slave accounts from Appwrite TablesDB, persists account events to `account_events`, and fans out real-time state over WebSocket.
- **Data service** can authenticate via the Appwrite auth layer (`CTRADER_USE_APPWRITE_AUTH=true`) and writes market data primarily to **InfluxDB Cloud Serverless** (`MARKET_DATA_DB_BACKEND=influxdb`), with SQLite as an explicit fallback.

For implementation details, connection model, configuration, and migration notes, see [`docs/account-hub-and-dataservice.md`](docs/account-hub-and-dataservice.md).

### Quick config checklist

| Mode | Required settings |
|------|-------------------|
| Broker-backed data service | `CTRADER_AUTH_BROKER_URL`, `CTRADER_AUTH_GRANT_ID`, `INTERNAL_API_KEY`, `CTRADER_CLIENT_ID`, `CTRADER_CLIENT_SECRET` |
| Appwrite-native data service | `CTRADER_USE_APPWRITE_AUTH=true`, `CTRADER_AUTH_BROKER_URL`, `INTERNAL_API_KEY`, `CTRADER_CLIENT_ID`, `CTRADER_CLIENT_SECRET` |
| Account Hub v2 | `account_hub_environment_mode=true` in `ctrader/config.py`, plus broker URL, internal API key, and cTrader app credentials |
| CLI / single-account | `CTRADER_AUTH_BROKER_URL`, `CTRADER_AUTH_GRANT_ID`, `INTERNAL_API_KEY`, `CTRADER_CLIENT_ID`, `CTRADER_CLIENT_SECRET` |

## Gold Quant + AI Agent Harness

A new decision layer for XAUUSD now runs alongside the data service and trading pipeline:

- **Gold Quant Engine** (`market_data_service/gold_quant_engine/`) — real-time tick-volume/order-flow analysis, multi-timeframe confluence (M15/H1/H4/D1), key levels (S/R, FVG, order blocks, POC/VAH/VAL), and short-entry/limit-order confidence scoring. It is fed by the Data Service daemon and exposed via `/api/v1/gold/*` REST endpoints and the Data Service control API.
- **Agent Harness** (`remote-services/agent_harness/`, port `9003`) — multi-model AI decision service:
  - `SignalIntentAgent` (Mistral Small 3.2) classifies Telegram messages as new signal, update, orphan, or noise.
  - `EntryDecisionAgent` (Hermes 3) approves/rejects/modifies XAUUSD entries using the gold quant snapshot + signal experience.
  - `LifecyclePlannerAgent` (Kimi K2.7) suggests in-trade actions (partial close, breakeven, full close, hold).
- **Integration** — `ssfx_server` calls the intent agent before parsing; `ssfx_trader/executor.py` calls the entry agent for new XAUUSD signals and the lifecycle planner for follow-ups. All agent decisions are kill-switchable via `AGENT_*_ENABLED` environment variables and degrade to deterministic rules on LLM timeout/failure.

Key env vars in `remote-services/config/v2.env.example`:
- `LLM_API_KEY`, `LLM_BASE_URL` — generic OpenAI-compatible key (OpenRouter) for Hermes / Kimi
- `MISTRALAI_API_KEY`, `MISTRALAI_BASE_URL` — native Mistral API key for the intent agent (overrides generic key)
- `AGENT_HARNESS_URL`, `AGENT_INTENT_ENABLED`, `AGENT_ENTRY_ENABLED`, `AGENT_LIFECYCLE_ENABLED`, `AGENT_AUTONOMY_ENABLED`
- `AGENT_MODEL_MISTRAL`, `AGENT_MODEL_HERMES`, `AGENT_MODEL_KIMI`

Routing: local vLLM endpoint → provider-specific key → generic `LLM_API_KEY`. For Mistral, this means `MISTRALAI_API_KEY` is used when set; otherwise it falls through to `LLM_API_KEY`.

## PPLX Agent — Gold Market Intelligence

A dedicated long-term research service (port `9004`) maintains a persistent picture of the gold market in a Perplexity Space:

- **Package**: `pplx-agent/pplx_agent/` — config, API, `GoldMarketAgent`, Space manager, TradingView scanner.
- **Perplexity client**: Vendored from `remote-services/agent/pplx/pplx/` and kept in `pplx-agent/pplx/` so the image is self-contained.
- **Integration**: `PplxResearchAgent` and `/agent/v1/research/pplx*` endpoints in `agent_harness` let the entry/lifecycle agents enrich decisions with long-term context.
- **Deployment**: Built into the `ctrader-services` Docker image via a BuildKit `additional_contexts` named `pplx-agent`; publicly exposed as `research.mrme.tech` (legacy `pplx-agent.mrme.tech`).
- **Config**: `service_config.config_key = pplx_agent` in Appwrite TablesDB (init via `dev/scripts/init/pplx-agent.py`).
- **Cookies**: Perplexity cookies are loaded from Bitwarden (secure note `perplexity.ai`) or `~/.config/perplexity/cookies.json` on the host and mounted read-only into the container.

## Project Files

| File | Purpose |
|------|---------|
| `appwrite.config.json` | Appwrite CLI project config |
| `opencode.jsonc` | OpenCode MCP + project settings |
| `.mcp.json` | Claude Code project-level MCP |
| `.qwen/settings.json` | Qwen Code project-level MCP |
| `.claude/CLAUDE.md` | Claude Code project instructions (imports AGENTS.md + .rules) |
| `QWEN.md` | Qwen Code project instructions (imports AGENTS.md + .rules) |
| `.rules` | Cross-agent rules (Appwrite-as-config-source, dev.sh, init scripts) |
| `dev.sh` | Dispatcher for development operations |
| `docs/account-hub-and-dataservice.md` | Architecture guide for Appwrite-native account hub + data service |
| `pplx-agent/` | Perplexity + TradingView gold market research agent (port 9004) |
| `remote-services/ctrader_cli/` | cTrader Open API CLI (market data + trading, Appwrite-auth aware) |
| `dev/scripts/` | Repeatable scripts for dev operations |
| `dev/scripts/init/ | Repeatable init scripts for third-party services |
| `resend` | Resend CLI (global) — email sending, configured with `.env` |
| `.env` | Environment variables (credentials) – **not committed** |
| `.gitignore` | Excludes `.env`, `.DS_Store`, logs |
| `AGENTS.md` | This file – project context for all AI agents |
| `docs/SESSION_FIXES_SUMMARY.md` | Comprehensive record of all fixes implemented |
