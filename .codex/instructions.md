# slwp — Codex Project Instructions

> This file provides project-specific context for Codex CLI.
> For full details, also read `AGENTS.md` and `.rules` in the project root.

## Project Overview

**slwp** — Appwrite-based backend for cTrader master-slave copy trading, market data, and AI agent harness. Region: `sgp`. Project ID: `6a22a362002b9ae880bb`.

- **Endpoint**: `https://sgp.cloud.appwrite.io/v1`
- **Organization**: `685456084475475206c2`
- **Primary domain**: `mrme.tech`

## Architecture Principle

**Appwrite Database is the source of truth.** All user-level and system-level configuration lives in Appwrite Database tables. Runtime code, local services, and CI/CD read configuration from Appwrite Database at startup.

`.env` is reserved **only** for bootstrap secrets (`APPWRITE_PROJECT_ID`, `APPWRITE_API_KEY`, `APPWRITE_ENDPOINT`). Do not add application config, feature flags, or third-party credentials to `.env`.

## Coding Conventions

- Use **TablesDB** (not the deprecated `Databases` class) for all new database code.
- Prefer object-param calling style: `{ databaseId: '...' }`.
- Use explicit string column types: `varchar`, `text`, `mediumtext`, `longtext`.
- Set `Permissions` and `Roles` explicitly on rows/files or at the table/bucket level. Default is no access.
- For SSR auth, use two clients: an **admin client** (API key, reusable) and a **session client** (per-request with cookie).
- Cookie name for sessions: `a_session_<PROJECT_ID>`.
- Handle errors with `AppwriteException` catch blocks.

## Security Requirements (Mandatory)

1. **Never log sensitive data.** Tokens, passwords, PINs, API keys, or any credentials must never be logged in plaintext.
2. **Row-level permissions.** When `rowSecurity: true` on a table, always set explicit row-level permissions when creating rows: `Permission.read(Role.user(userId))`, `Permission.update(Role.user(userId))`, `Permission.delete(Role.user(userId))`.
3. **Avoid broad table permissions.** Do not use `create("any")` or `create("users")` at the table level.
4. **Webhook signature verification.** All external webhooks (Telegram, etc.) must verify the signature/secret token before processing payloads.
5. **Session validation via HTTP.** To validate user sessions, make HTTP requests to Appwrite's `/account` endpoint with the session cookie. Do not call `setSession()` on an admin client.
6. **Config storage.** Store third-party service credentials in dedicated config tables (e.g., `service_config`), not in user/data tables like `slave_accounts`.

## Development Operations

All development operations are invoked through `dev.sh <command>`. The implementation lives in `dev/scripts/<command>.py` (preferred) or `dev/scripts/<command>.sh` (simple wrappers).

Key commands:
- `dev.sh start` — start local services
- `dev.sh stop` — stop local services
- `dev.sh status` — show service status
- `dev.sh logs` — tail relevant logs
- `dev.sh test` — run tests
- `dev.sh lint` — run linting / type checking
- `dev.sh deploy` — deploy to AWS VM
- `dev.sh deploy-remote <target>` — deploy to specific remote target
- `dev.sh deploy-status` — show deployment status
- `dev.sh init` — run all third-party service init scripts

Do not run ad-hoc commands for these operations; add them to `dev/scripts/` and expose them through `dev.sh`.

## Remote Deployment

Remote deployments target the AWS VM (`aws-ssfx` in `~/.ssh/config`). The canonical script is `remote-services/setup_vm.py`. VM target is configured in `remote-services/config/vm.env`.

```bash
python3 remote-services/setup_vm.py
# Code-only sync/rebuild:
SKIP_VM_SETUP=1 SKIP_TUNNEL=1 python3 remote-services/setup_vm.py
```

## Public Access: Cloudflare Tunnel

All public access uses the Cloudflare Tunnel `ssfx_azurue` for `mrme.tech`.

Current public hostnames:
- `ssfx-api.mrme.tech` → `http://localhost:8000`
- `ds-control.mrme.tech` → `http://localhost:9000`
- `ds-sse.mrme.tech` → `http://localhost:9001`
- `dataservice.mrme.tech` → `http://localhost:9002`
- `agent.mrme.tech` → `http://localhost:9003`
- `pplx-agent.mrme.tech` → `http://localhost:9004`
- `ctrader.mrme.tech` → `http://localhost:9300`
- `account-hub.mrme.tech` → `http://localhost:9301`

## CI/CD

Deployment is automated via GitHub Actions (`.github/workflows/deploy.yml`), triggered by pushes to `develop`.

- `main` — stable, protected. Manual merges from `develop` for releases
- `develop` — CI/CD active branch, all changes go here first

Functions are deployed via `appwrite push` CLI, **not** VCS git deployment.

## Third-Party Service Initialization

Configure third-party services using repeatable init scripts in `dev/scripts/init/`. Each init script reads setup values from a YAML file (e.g., `dev/scripts/init/config.yml`), then writes them to Appwrite Database.

## Key Project Files

| File | Purpose |
|------|---------|
| `AGENTS.md` | Full project context for all AI agents |
| `.rules` | Cross-agent rules |
| `dev.sh` | Dispatcher for development operations |
| `appwrite.config.json` | Appwrite CLI project config |
| `opencode.jsonc` | OpenCode config |
| `.mcp.json` | MCP server config (OpenCode / other agents) |
| `.claude/CLAUDE.md` | Claude Code instructions |
| `.qwen/settings.json` | Qwen Code config |
| `QWEN.md` | Qwen Code project instructions |

## Environment

- mise manages Node, Python, Go, `uv`
- `uv` / `uvx` invoked via mise shims at `/Users/m/.local/share/mise/shims/`
- Requires `MISE_DATA_DIR=/Volumes/Spare/mise`
- cTrader Open API client library installed via pip
- Docker Compose for remote services

## Language Preferences

- **TypeScript / Node.js**: Appwrite Functions and Sites
- **Python**: Remote services (data service, account hub, trading engine)
- **Bash**: Simple dev tool wrappers only
- Use Python for scripts that parse JSON, call HTTP APIs, manage Azure / Cloudflare / Resend resources, or handle structured data.

## Contact / References

- For cTrader-specific questions, load the `ctrader-auth-layer` or `ctrader-trading-system` skills
- For Appwrite TablesDB questions, load the `appwrite-tablesdb` skill
- For deployment questions, load the `appwrite-cicd` skill
