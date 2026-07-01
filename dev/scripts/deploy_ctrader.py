#!/usr/bin/env python3
"""dev/scripts/deploy_ctrader.py — Deploy the unified ctrader service to the Azure VM."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def get_vm_ip():
    result = subprocess.run(
        [
            "az", "vm", "list-ip-addresses",
            "--name", "ubuntu-server",
            "--resource-group", "RG-UBUNTU-VM",
            "--query", "[0].virtualMachine.network.publicIpAddresses[0].ipAddress",
            "--output", "tsv",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def run_ssh(host, command, check=True):
    return subprocess.run(
        ["ssh", f"m@{host}", command],
        capture_output=True,
        text=True,
        check=check,
    )


def main():
    ip = get_vm_ip()
    if not ip:
        print("Error: could not resolve Azure VM IP", file=sys.stderr)
        sys.exit(1)

    print(f"[deploy] Target VM: {ip}")

    repo_root = Path(__file__).resolve().parents[3]
    v2_dir = repo_root / "v2"
    remote_dir = "/home/m/ssfx/v2"

    # 1. Sync v2 code to the VM
    print(f"[deploy] Syncing {v2_dir} to {ip}:{remote_dir}")
    subprocess.run(
        [
            "rsync",
            "-avz",
            "--delete",
            "--exclude=.env",
            "--exclude=venv",
            "--exclude=__pycache__",
            "--exclude=.pytest_cache",
            "--exclude=.ruff_cache",
            "--exclude=ssfx_webui.tar.gz",
            "--exclude=ssfx_webui.zip",
            str(v2_dir) + "/",
            f"m@{ip}:{remote_dir}/",
        ],
        check=True,
    )

    # 2. Ensure a Linux venv exists and dependencies are installed
    print("[deploy] Ensuring Python 3.11 venv and dependencies on VM")
    run_ssh(
        ip,
        "cd /home/m/ssfx/v2 \u0026\u0026 "
        "if [[ ! -d venv/bin ]]; then python3.11 -m venv venv; fi \u0026\u0026 "
        "venv/bin/pip install --upgrade pip \u0026\u0026 "
        "venv/bin/pip install -e .",
    )

    # 2. Write environment file
    env_vars = {
        "APPWRITE_ENDPOINT": os.environ.get("APPWRITE_ENDPOINT", ""),
        "APPWRITE_PROJECT_ID": os.environ.get("APPWRITE_PROJECT_ID", ""),
        "APPWRITE_API_KEY": os.environ.get("APPWRITE_API_KEY", ""),
        "APPWRITE_DATABASE_ID": os.environ.get("APPWRITE_DATABASE_ID", "ctrader_auth"),
        "INTERNAL_API_KEY": os.environ.get("INTERNAL_API_KEY", ""),
        "CTRADER_AUTH_BROKER_URL": os.environ.get("CTRADER_AUTH_BROKER_URL", ""),
        "CTRADER_CLIENT_ID": os.environ.get("CTRADER_CLIENT_ID", ""),
        "CTRADER_CLIENT_SECRET": os.environ.get("CTRADER_CLIENT_SECRET", ""),
        "DATA_SERVICE_URL": os.environ.get("DATA_SERVICE_URL", "https://dataservice.mrme.tech"),
        "DATA_SERVICE_API_KEY": os.environ.get("DATA_SERVICE_API_KEY", ""),
        "SLAVE_API_KEY": os.environ.get("SLAVE_API_KEY", ""),
        "ADMIN_API_KEY": os.environ.get("ADMIN_API_KEY", ""),
        "CTRADER_DATA_API_SECRET": os.environ.get("CTRADER_DATA_API_SECRET", ""),
    }
    env_lines = [f'{k}="{v}"' for k, v in env_vars.items() if v]
    env_content = "\n".join(env_lines) + "\n"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
        f.write(env_content)
        local_env = f.name

    try:
        subprocess.run(
            ["scp", local_env, f"m@{ip}:/home/m/ssfx/v2/.env"],
            check=True,
        )
    finally:
        os.unlink(local_env)

    # 3. Install systemd service
    service_src = v2_dir / "ctrader" / "deploy" / "ctrader.service"
    if service_src.exists():
        subprocess.run(
            ["scp", str(service_src), f"m@{ip}:/tmp/ctrader.service"],
            check=True,
        )
        run_ssh(ip, "sudo mv /tmp/ctrader.service /etc/systemd/system/ctrader.service && sudo systemctl daemon-reload")
        run_ssh(ip, "sudo systemctl enable ctrader")
    else:
        print("[deploy] Warning: systemd service file not found; skipping service install")

    # 4. Restart service
    print("[deploy] Restarting ctrader service")
    run_ssh(ip, "sudo systemctl restart ctrader")

    # 5. Health check
    print("[deploy] Running health check")
    result = run_ssh(ip, "curl -fsS http://127.0.0.1:9300/health || true", check=False)
    print(result.stdout.strip())
    if "ok" not in result.stdout:
        print("[deploy] Health check did not return ok; check logs with: sudo journalctl -u ctrader -n 100", file=sys.stderr)
        sys.exit(1)

    print("[deploy] Done")


if __name__ == "__main__":
    main()
