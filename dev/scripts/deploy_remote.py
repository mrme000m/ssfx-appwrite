#!/usr/bin/env python3
"""
dev/scripts/deploy_remote.py — Rsync + hot-reload deploy to the AWS VM.

Minimal host configuration: only Docker + Docker Compose are needed.
All code changes are pushed via rsync and picked up by bind-mounts in
docker-compose.yml. The image is only rebuilt on explicit request.

Usage:
    python3 dev/scripts/deploy_remote.py          # rsync + restart compose
    python3 dev/scripts/deploy_remote.py --build  # also rebuild image
    python3 dev/scripts/deploy_remote.py --prune  # clean old images
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
SERVICES_DIR = PROJECT_ROOT / "remote-services"
VM_ENV = SERVICES_DIR / "config" / "vm.env"

SSH_HOST = os.getenv("SSH_HOST", "aws-ssfx")
REMOTE_DIR = os.getenv("REMOTE_DIR", "/home/ec2-user/ssfx-remote-services")
PPLX_REMOTE_DIR = os.getenv("PPLX_REMOTE_DIR", "/home/ec2-user/pplx-agent")

RSYNC_EXCLUDES = [
    ".git",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    "*.pyc",
    "*.pyo",
    "*.egg-info",
    "logs/*.log",
    "*.db",
    ".DS_Store",
    "node_modules",
    ".venv",
    "*.tmp",
    # Do NOT wipe the VM's local .env — Docker Compose interpolates ${VAR} from it.
    ".env",
]


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print(f"[deploy-remote] {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, capture_output=False, text=True)


def ssh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return run(
        ["ssh", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new", SSH_HOST, *args],
        check=check,
    )


def rsync(local_dir: Path, remote_path: str, extra_excludes: list[str] | None = None) -> None:
    excludes = list(RSYNC_EXCLUDES)
    if extra_excludes:
        excludes.extend(extra_excludes)

    cmd = ["rsync", "-avz", "--delete"]
    for pattern in excludes:
        cmd.extend(["--exclude", pattern])
    cmd.append(f"{local_dir}/")
    cmd.append(f"{SSH_HOST}:{remote_path}/")
    run(cmd)


def check_vm_docker() -> bool:
    result = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new", SSH_HOST,
         "docker compose version >/dev/null 2>&1 && echo OK || echo MISSING"],
        capture_output=True, text=True, check=False,
    )
    return "OK" in (result.stdout or "")


def clean_old_images() -> None:
    print("[deploy-remote] Cleaning old Docker images...")
    ssh("docker image prune -a -f --filter 'until=24h' 2>/dev/null || true", check=False)
    ssh("docker builder prune -f 2>/dev/null || true", check=False)


def restart_compose(build: bool = False) -> None:
    print("[deploy-remote] Restarting Docker Compose...")
    # Docker Compose interpolates ${APPWRITE_ENDPOINT} etc. from the host shell.
    # config/v2.env contains these credentials, so source it first.
    compose_cmd = f"cd {REMOTE_DIR} && source config/v2.env && docker compose up -d"
    if build:
        compose_cmd += " --build"
    ssh(compose_cmd)

    # Check container health
    print("[deploy-remote] Waiting for services to come up...")
    import time
    time.sleep(5)

    for port in [8000, 9001, 9002, 9003, 9301]:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new", SSH_HOST,
             f"docker exec ctrader-services bash -c 'nc -z 127.0.0.1 {port} && echo OK || echo FAIL'"],
            capture_output=True, text=True, check=False,
        )
        status = (result.stdout or "").strip()
        svc = {
            8000: "ssfx-server (webhook/API)",
            9001: "dataservice SSE",
            9002: "dataservice REST",
            9003: "agent-harness",
            9301: "account-hub",
        }.get(port, str(port))
        if status == "OK":
            print(f"[deploy-remote]   ✓ {svc} on port {port}")
        else:
            print(f"[deploy-remote]   ✗ {svc} on port {port} — {status}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Rsync + hot-reload deploy to remote VM")
    parser.add_argument("--build", action="store_true", help="Rebuild Docker image")
    parser.add_argument("--prune", action="store_true", help="Prune old Docker images after deploy")
    parser.add_argument("--only-sync", action="store_true", help="Only rsync, don't restart")
    args = parser.parse_args()

    if not check_vm_docker():
        print("[deploy-remote] ERROR: Docker Compose not found on remote VM.", file=sys.stderr)
        return 1

    print(f"[deploy-remote] Target: {SSH_HOST}:{REMOTE_DIR}")

    # 1. Rsync remote-services
    print("[deploy-remote] Syncing remote-services...")
    rsync(SERVICES_DIR, REMOTE_DIR, extra_excludes=["pplx-agent"])

    # 2. Rsync pplx-agent (separate directory in project root)
    pplx_local = PROJECT_ROOT / "pplx-agent"
    if pplx_local.exists():
        print("[deploy-remote] Syncing pplx-agent...")
        rsync(pplx_local, PPLX_REMOTE_DIR)

    if args.only_sync:
        print("[deploy-remote] Sync complete (--only-sync).")
        return 0

    # 3. Restart compose
    restart_compose(build=args.build)

    # 4. Optional prune
    if args.prune:
        clean_old_images()

    print("[deploy-remote] Deploy complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
