# slwp — Qwen Code Instructions

@AGENTS.md
@.rules

## Qwen Code Specific

- Use `/goal` for multi-step tasks that involve deployments or third-party service initialization.
- Use `dev.sh <command>` for all development operations; do not run raw shell commands for start/stop/deploy/status.
- When generating skills from repeated workflows, save them under `.qwen/skills/` and reference them in future sessions.
- Use `/remember` to persist project conventions discovered during work so they survive across sessions.
