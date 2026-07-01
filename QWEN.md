# slwp — Qwen Code Instructions

@AGENTS.md
@.rules

## Qwen Code Specific

- Use `/goal` for multi-step tasks that involve deployments or third-party service initialization.
- Use `dev.sh <command>` for all development operations; do not run raw shell commands for start/stop/deploy/status.
- When generating skills from repeated workflows, save them under `.qwen/skills/` and reference them in future sessions.
- Use `/remember` to persist project conventions discovered during work so they survive across sessions.

## Plugin: appwrite-ctrader

The `appwrite-ctrader` plugin skills are symlinked from `~/.claude/plugins/appwrite-ctrader/skills/` to `.qwen/skills/` and `~/.qwen/skills/`. Available skills:
- `appwrite-functions` — Create, deploy, and manage Appwrite Functions
- `appwrite-tablesdb` — Create TablesDB tables with correct column types and permissions
- `appwrite-sites` — Deploy and extend the auth/admin SPA
- `appwrite-cicd` — Hybrid GitHub Actions + Appwrite git deployment pipeline
- `ctrader-auth` — OAuth flow, PIN login, grant_id token management
- `ctrader-trading` — TG signal ingestion, copy trading, position monitoring

Use `dev.sh` commands as equivalents to Claude Code slash commands (e.g., `./dev.sh deploy-auth` for `/deploy-auth`).
