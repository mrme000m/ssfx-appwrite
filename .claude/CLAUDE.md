# slwp — Claude Code Instructions

@../AGENTS.md
@../.rules

## Claude Code Specific

- Use `/plan` mode for non-trivial changes that touch Appwrite database tables, deployment scripts, or third-party integrations.
- Prefer `dev.sh <command>` for all development, deployment, and status operations.
- When creating new init scripts for third-party services, place them in `init-scripts/` and ensure they read from a YAML config and persist values to Appwrite Database.
