#!/usr/bin/env python3
"""One-shot fresh-VM provisioner for SSFX remote-services.

Run from the project root or remote-services/:

    SSH_HOST=aws-ssfx python3 remote-services/setup_vm.py

To move to a different VM, change at most 2-3 values in
remote-services/config/vm.env (see config/vm.env.example).

TODO: Replace ad-hoc SSH + shell helpers with an Ansible playbook for
post-VM configuration. The playbook should cover Docker, cloudflared, and
stack deployment so that switching clouds requires only an inventory change.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
VM_SCRIPTS_DIR = SCRIPT_DIR / "vm-scripts"

HEALTH_HOSTS = [
    "ssfx-api.mrme.tech",
    "ctrader.mrme.tech",
    "agent.mrme.tech",
    "dataservice.mrme.tech",
]


def load_env_file(path: Path) -> None:
    """Load a dotenv-style file into os.environ (only if key is unset)."""
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if val and os.environ.get(key) is None:
                os.environ[key] = val


def load_env() -> None:
    """Load repo root .env and optional VM target config."""
    load_env_file(PROJECT_ROOT / ".env")
    load_env_file(SCRIPT_DIR / "config" / "vm.env")


def require_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        print(f"ERROR: {name} is not set.", file=sys.stderr)
        sys.exit(1)
    return val


def run(cmd: list[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    print(f"[setup-vm] {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=True,
    )
    return result


def ssh_cmd(*args: str) -> list[str]:
    return [
        "ssh",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=accept-new",
        require_env("SSH_HOST"),
        *args,
    ]


def ssh(*args: str, capture: bool = False, input: str | None = None) -> subprocess.CompletedProcess:
    cmd = ssh_cmd(*args)
    print(f"[setup-vm] {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        input=input,
        capture_output=capture,
        text=True,
        check=True,
    )
    return result


def ssh_quiet(*args: str) -> bool:
    result = subprocess.run(
        ssh_cmd(*args),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def scp(local: Path, remote_path: str) -> None:
    host = require_env("SSH_HOST")
    run([
        "scp",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=accept-new",
        str(local),
        f"{host}:{remote_path}",
    ])


def run_vm_script(name: str, env: dict[str, str] | None = None) -> None:
    """Upload and execute a vm-script on the remote host."""
    local_script = VM_SCRIPTS_DIR / f"{name}.sh"
    remote_script = f"/tmp/setup-vm-{name}.sh"
    scp(local_script, remote_script)

    env_prefix = ""
    if env:
        env_prefix = " ".join(f'{k}="{v}"' for k, v in env.items()) + " "

    ssh(f"{env_prefix}bash {remote_script}")


def detect_os_family() -> str:
    result = ssh("source /etc/os-release 2>/dev/null && echo $ID", capture=True)
    os_id = result.stdout.strip() if result.returncode == 0 else ""
    mapping = {
        "amzn": "amazonlinux",
        "amazon": "amazonlinux",
        "amzn2": "amazonlinux",
        "amzn2023": "amazonlinux",
        "rhel": "rhel",
        "centos": "rhel",
        "rocky": "rhel",
        "almalinux": "rhel",
        "fedora": "rhel",
        "ubuntu": "ubuntu",
        "debian": "ubuntu",
    }
    family = mapping.get(os_id)
    if not family:
        print(
            f"ERROR: Could not auto-detect OS family (got ID='{os_id}'). "
            "Set VM_OS_FAMILY to one of: amazonlinux, rhel, ubuntu",
            file=sys.stderr,
        )
        sys.exit(1)
    return family


def cf_api_request(method: str, url: str, token: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read().decode("utf-8") or "{}")
        print(f"Cloudflare API error ({exc.code}): {body.get('errors', body)}", file=sys.stderr)
        raise
    if not body.get("success"):
        raise RuntimeError(f"Cloudflare API error: {body.get('errors', body)}")
    return body.get("result", {})


def fetch_tunnel_token(account_id: str, tunnel_id: str, token: str) -> str:
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/"
        f"cfd_tunnel/{tunnel_id}/token"
    )
    tunnel_token = cf_api_request("GET", url, token)
    if isinstance(tunnel_token, dict):
        tunnel_token = tunnel_token.get("token", "")
    if not tunnel_token:
        print("ERROR: Failed to fetch tunnel token from Cloudflare API", file=sys.stderr)
        sys.exit(1)
    return str(tunnel_token)


def update_tunnel_ingress(account_id: str, tunnel_id: str, token: str, ingress_file: Path) -> None:
    with ingress_file.open("r", encoding="utf-8") as f:
        ingress = json.load(f)

    if not isinstance(ingress, list):
        print("ERROR: ingress config must be a JSON array", file=sys.stderr)
        sys.exit(1)

    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/"
        f"cfd_tunnel/{tunnel_id}/configurations"
    )
    result = cf_api_request(
        "PUT",
        url,
        token,
        {"config": {"ingress": ingress, "warp_routing": None}},
    )
    print("[setup-vm] Tunnel ingress updated.")
    for rule in result.get("config", {}).get("ingress", []):
        hostname = rule.get("hostname") or "catch-all"
        print(f"  {hostname} -> {rule.get('service')}")


def rsync_local_to_remote(local_dir: Path, remote_path: str, extra_excludes: list[str] | None = None) -> None:
    host = require_env("SSH_HOST")
    excludes = [
        ".git",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        "logs/*.log",
        "*.db",
    ]
    if extra_excludes:
        excludes.extend(extra_excludes)

    cmd = ["rsync", "-avz"]
    for pattern in excludes:
        cmd.extend(["--exclude", pattern])
    cmd.append(f"{local_dir}/")
    cmd.append(f"{host}:{remote_path}/")
    run(cmd)


def verify_public_endpoints() -> bool:
    print("[setup-vm] === Phase 5: Verification ===")
    all_ok = True
    for host in HEALTH_HOSTS:
        url = f"https://{host}/health"
        try:
            req = urllib.request.Request(
                url,
                method="GET",
                headers={"User-Agent": "ssfx-setup-vm/1.0"},
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                code = resp.status
        except urllib.error.HTTPError as exc:
            code = exc.code
        except urllib.error.URLError:
            code = "000"
        if code == 200:
            print(f"[setup-vm] ✓ https://{host}/health -> {code}")
        else:
            print(f"[setup-vm] ✗ https://{host}/health -> {code}")
            all_ok = False
    return all_ok


def main() -> int:
    load_env()

    ssh_host = require_env("SSH_HOST")
    cf_token = require_env("CF_API_TOKEN")
    cf_account_id = require_env("CF_ACCOUNT_ID")
    cf_tunnel_id = require_env("CF_TUNNEL_ID")

    if "@" in ssh_host:
        vm_user = os.environ.get("VM_USER") or ssh_host.split("@", 1)[0]
    else:
        vm_user = os.environ.get("VM_USER") or ""
    if not vm_user:
        result = run(["ssh", "-G", ssh_host], capture=True, check=False)
        for line in result.stdout.splitlines():
            if line.startswith("user "):
                vm_user = line.split(None, 1)[1]
                break
    vm_user = vm_user or "ec2-user"

    remote_dir = os.environ.get("REMOTE_DIR") or f"/home/{vm_user}/ssfx-remote-services"
    pplx_remote_dir = os.environ.get("PPLX_REMOTE_DIR") or f"/home/{vm_user}/pplx-agent"
    local_cookie_path = Path.home() / ".config" / "perplexity" / "cookies.json"
    remote_cookie_path = os.environ.get("REMOTE_COOKIE_PATH") or f"/home/{vm_user}/.config/perplexity/cookies.json"

    os.environ["VM_USER"] = vm_user
    os.environ["REMOTE_DIR"] = remote_dir
    os.environ["PPLX_REMOTE_DIR"] = pplx_remote_dir

    print(f"[setup-vm] Target: {ssh_host} (user: {vm_user})")
    print(f"[setup-vm] Remote dir: {remote_dir}")

    if not ssh_quiet("echo ssh-ok"):
        print(f"ERROR: Cannot connect to {ssh_host}. Check SSH config and host availability.", file=sys.stderr)
        return 1

    vm_os_family = os.environ.get("VM_OS_FAMILY", "").strip()
    if not vm_os_family:
        print("[setup-vm] Detecting OS family ...")
        vm_os_family = detect_os_family()
        print(f"[setup-vm] Detected OS family: {vm_os_family}")
    else:
        print(f"[setup-vm] Using OS family: {vm_os_family}")

    skip_vm_setup = os.environ.get("SKIP_VM_SETUP", "") == "1"
    skip_tunnel = os.environ.get("SKIP_TUNNEL", "") == "1"
    skip_deploy = os.environ.get("SKIP_DEPLOY", "") == "1"

    if skip_vm_setup:
        print("[setup-vm] Skipping VM setup (Docker/cloudflared)")
    else:
        print("[setup-vm] === Phase 1: Installing Docker stack ===")
        run_vm_script("install-docker", {"VM_OS_FAMILY": vm_os_family})

        print("[setup-vm] === Phase 2: Installing cloudflared ===")
        print("[setup-vm] Fetching tunnel token from Cloudflare API ...")
        tunnel_token = fetch_tunnel_token(cf_account_id, cf_tunnel_id, cf_token)

        # Write token to a short-lived remote env file instead of the command line.
        token_env = f'TUNNEL_TOKEN="{tunnel_token}"\n'
        ssh("cat > /tmp/setup-vm-tunnel-token.env", input=token_env)
        run_vm_script("install-cloudflared", {"VM_OS_FAMILY": vm_os_family})

    if skip_tunnel:
        print("[setup-vm] Skipping tunnel ingress update")
    else:
        print("[setup-vm] === Phase 3: Updating Cloudflare tunnel ingress ===")
        ingress_file = SCRIPT_DIR / "config" / "tunnel-ingress.json"
        if not ingress_file.exists():
            print(f"ERROR: Tunnel ingress config not found: {ingress_file}", file=sys.stderr)
            return 1
        update_tunnel_ingress(cf_account_id, cf_tunnel_id, cf_token, ingress_file)

    if skip_deploy:
        print("[setup-vm] Skipping stack deploy")
    else:
        print("[setup-vm] === Phase 4: Deploying SSFX stack ===")

        pplx_dir = PROJECT_ROOT / "pplx-agent"

        print("[setup-vm] Syncing remote-services code ...")
        ssh(f"mkdir -p {remote_dir}")
        rsync_local_to_remote(SCRIPT_DIR, remote_dir)

        print("[setup-vm] Syncing PPLX agent code ...")
        ssh(f"mkdir -p {pplx_remote_dir}")
        rsync_local_to_remote(
            pplx_dir,
            pplx_remote_dir,
            extra_excludes=["reports", ".cache"],
        )

        if local_cookie_path.exists():
            print("[setup-vm] Syncing Perplexity cookies ...")
            ssh(f"mkdir -p {Path(remote_cookie_path).parent}")
            run([
                "rsync", "-avz",
                str(local_cookie_path),
                f"{ssh_host}:{remote_cookie_path}",
            ])
        else:
            print(f"[setup-vm] WARN: Perplexity cookies not found at {local_cookie_path}")

        print("[setup-vm] Building and starting Docker stack ...")
        compose_env = (
            f"APPWRITE_ENDPOINT={os.environ.get('APPWRITE_ENDPOINT', '')}\n"
            f"APPWRITE_PROJECT_ID={os.environ.get('APPWRITE_PROJECT_ID', '')}\n"
            f"APPWRITE_API_KEY={os.environ.get('APPWRITE_API_KEY', '')}\n"
        )
        ssh(f"cat > {remote_dir}/.env.compose", input=compose_env)
        run_vm_script("deploy-stack", {"REMOTE_DIR": remote_dir})

    if verify_public_endpoints():
        print("[setup-vm] === Setup complete ===")
        return 0
    else:
        print("[setup-vm] Some health checks failed. Investigate with:", file=sys.stderr)
        print(f"  ssh {ssh_host} 'cd {remote_dir} && docker compose logs --tail 50'", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
