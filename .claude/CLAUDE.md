# slwp — Claude Code Instructions

@../AGENTS.md
@../.rules

## Claude Code Specific

- Use `/plan` mode for non-trivial changes that touch Appwrite database tables, deployment scripts, or third-party integrations.
- Prefer `dev.sh <command>` for all development, deployment, and status operations.
- When creating new init scripts for third-party services, place them in `init-scripts/` and ensure they read from a YAML config and persist values to Appwrite Database.
- Before modifying the cTrader account hub, data service, or InfluxDB persistence layer, read `docs/account-hub-and-dataservice.md`.

## Plugin: appwrite-ctrader

The `appwrite-ctrader` plugin is loaded from `~/.claude/plugins/appwrite-ctrader/` via `.claude/settings.json`. It provides:
- **7 skills**: appwrite-functions, appwrite-tablesdb, appwrite-sites, appwrite-cicd, ctrader-auth, ctrader-trading, pplx-agent
- **7 slash commands**: `/deploy-auth`, `/deploy-functions`, `/deploy-site`, `/deploy-all`, `/init-service`, `/status`, `/create-table`
- **3 sub-agents**: appwrite-deployer, appwrite-reviewer, trade-system-architect
- **2 hooks**: validate_appwrite_code (PreToolUse), post_deploy_check (PostToolUse)
