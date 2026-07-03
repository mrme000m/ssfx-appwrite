---
name: ssfx-remote-vm-setup
description: Use when deploying the SSFX remote-services stack to a fresh Linux VM, switching cloud providers, or repairing the VM setup (Docker, cloudflared, tunnel ingress).
compatibility: opencode
metadata:
  current_target: aws-ssfx
  domain: mrme.tech
  tunnel_id: d1e96e86-a44a-457a-a60c-e7d5d5d675bd
---

# SSFX Remote VM Setup

## Quick Reference

```bash
# 1. Copy the example target config and edit SSH_HOST
cp remote-services/config/vm.env.example remote-services/config/vm.env

# 2. Run the one-shot provisioner
python3 remote-services/setup_vm.py
# or
./remote-services/setup-vm.sh
```

## What It Does

`remote-services/setup_vm.py` provisions a fresh Linux VM end-to-end:

1. Installs Docker Engine, Docker Compose plugin, and Docker Buildx.
2. Installs `cloudflared` and registers the existing Cloudflare tunnel as a systemd service.
3. Updates the tunnel ingress from `remote-services/config/tunnel-ingress.json`.
4. Syncs `remote-services/` and `pplx-agent/` to the VM.
5. Builds and starts the Docker Compose stack.
6. Verifies public health endpoints.

## Switching VMs (2–3 Changes)

Edit `remote-services/config/vm.env`:

```bash
# Required: SSH alias from ~/.ssh/config or user@ip
SSH_HOST=aws-ssfx

# Optional: only if user is not in the SSH alias
# VM_USER=ec2-user

# Optional: only if OS is not auto-detected from /etc/os-release
# VM_OS_FAMILY=amazonlinux
```

Supported `VM_OS_FAMILY` values: `amazonlinux`, `rhel`, `ubuntu`.

## Files

| File | Purpose |
|------|---------|
| `remote-services/setup_vm.py` | Main orchestrator (canonical agent entry point) |
| `remote-services/setup-vm.sh` | Thin shell wrapper around `setup_vm.py` |
| `remote-services/config/vm.env.example` | Example target configuration |
| `remote-services/config/tunnel-ingress.json` | Source of truth for Cloudflare tunnel public hostnames |
| `remote-services/vm-scripts/install-docker.sh` | Remote Docker/Compose/Buildx installer |
| `remote-services/vm-scripts/install-cloudflared.sh` | Remote cloudflared installer |
| `remote-services/vm-scripts/deploy-stack.sh` | Remote Docker Compose build/start script |

## Environment Variables

Loaded from `.env` (project root) and `remote-services/config/vm.env`:

- `SSH_HOST` — target VM (required)
- `VM_USER` — remote user (auto-derived from `SSH_HOST` if omitted)
- `VM_OS_FAMILY` — `amazonlinux` / `rhel` / `ubuntu` (auto-detected if omitted)
- `REMOTE_DIR` — deployment path on VM (default `~/ssfx-remote-services`)
- `CF_API_TOKEN`, `CF_ACCOUNT_ID`, `CF_TUNNEL_ID` — from `.env`

Skips (useful for re-deploys):
- `SKIP_VM_SETUP=1` — skip Docker and cloudflared installation
- `SKIP_TUNNEL=1` — skip Cloudflare tunnel ingress update
- `SKIP_DEPLOY=1` — skip code sync and Docker deploy

## Adding a New Public Service

1. Add the service to `remote-services/docker-compose.yml` and expose its port.
2. Add an ingress rule to `remote-services/config/tunnel-ingress.json`.
3. Ensure a proxied CNAME record exists in Cloudflare for the subdomain.
4. Run `python3 remote-services/setup_vm.py` to apply.

## Troubleshooting

```bash
# Check tunnel status and ingress
./dev.sh cf-tunnel-status

# Update only tunnel ingress from JSON
./dev.sh cf-tunnel-update

# SSH into the VM and inspect logs
ssh <target>
cd ~/ssfx-remote-services
docker compose logs --tail 50
```

## TODO

Replace the SSH + shell helper approach with an Ansible playbook for post-VM configuration. When that happens, switching clouds should require only an inventory change.
