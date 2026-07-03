#!/usr/bin/env python3
"""Initialise Cloudflare tunnel ingress + DNS for the Azure VM Docker services.

This script:
  1. Loads the current Cloudflare tunnel configuration.
  2. Removes any existing ingress rules for the hostnames we publish.
  3. Adds fresh ingress rules pointing at the Docker-exposed host ports.
  4. Ensures proxied CNAME records exist for each hostname.
  5. Polls the public endpoints until they are reachable.

Run locally or on the Azure VM after `docker compose up -d`:

    export CF_API_TOKEN=...
    python3 dev/scripts/init-tunnel.py

Required env vars (read from .env or exported):
    CF_API_TOKEN, CF_ACCOUNT_ID, CF_ZONE_ID, CF_TUNNEL_ID
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


# Hostnames we publish through the Cloudflare tunnel for this Docker stack.
# The `service` value is what the cloudflared connector on the VM resolves.
DEFAULT_INGRESS = [
    {"hostname": "ssfx-api.mrme.tech",    "service": "http://localhost:8000", "originRequest": {}},
    {"hostname": "ds-control.mrme.tech",  "service": "http://localhost:9000", "originRequest": {}},
    {"hostname": "ds-sse.mrme.tech",      "service": "http://localhost:9001", "originRequest": {}},
    {"hostname": "dataservice.mrme.tech", "service": "http://localhost:9002", "originRequest": {}},
    {"hostname": "ctrader-api.mrme.tech", "service": "http://localhost:9300", "originRequest": {}},
    {"hostname": "ctrader-ws.mrme.tech",  "service": "http://localhost:9301", "originRequest": {}},
]


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

# Ingress rules can be customised by placing config/tunnel-ingress.json next to
# this script. The defaults match the docker-compose.yml port mappings.
INGRESS_CONFIG_PATH = SCRIPT_DIR / "config" / "tunnel-ingress.json"


def load_ingress() -> list[dict]:
    """Load desired ingress rules from config or fall back to defaults."""
    if INGRESS_CONFIG_PATH.exists():
        with INGRESS_CONFIG_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    return DEFAULT_INGRESS


def load_env_file(path: Path) -> None:
    """Load a dotenv-style file into os.environ (only if the key is unset)."""
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
    """Load repo root .env and the remote-services config env files."""
    load_env_file(REPO_ROOT / ".env")
    load_env_file(SCRIPT_DIR / "config" / "dataservice.env")
    load_env_file(SCRIPT_DIR / "config" / "v2.env")


def require_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        print(f"Error: {name} is not set. Export it or add it to .env.", file=sys.stderr)
        sys.exit(1)
    return val


def cf_request(method: str, url: str, token: str, payload: dict | None = None) -> dict:
    """Make a Cloudflare API request and return the result object."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read().decode("utf-8") or "{}")
        print(f"Cloudflare API error ({exc.code}): {body.get('errors', body)}", file=sys.stderr)
        raise
    if not body.get("success"):
        raise RuntimeError(f"Cloudflare API error: {body.get('errors', body)}")
    return body.get("result", {})


def get_tunnel_config(account_id: str, tunnel_id: str, token: str) -> dict:
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/"
        f"cfd_tunnel/{tunnel_id}/configurations"
    )
    return cf_request("GET", url, token)


def update_tunnel_config(account_id: str, tunnel_id: str, token: str, ingress: list[dict]) -> dict:
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/"
        f"cfd_tunnel/{tunnel_id}/configurations"
    )
    return cf_request("PUT", url, token, {"config": {"ingress": ingress, "warp_routing": None}})


def list_dns_records(zone_id: str, token: str) -> list[dict]:
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?per_page=500"
    return cf_request("GET", url, token)


def create_dns_record(zone_id: str, token: str, record: dict) -> dict:
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records"
    return cf_request("POST", url, token, record)


def patch_dns_record(zone_id: str, record_id: str, token: str, record: dict) -> dict:
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}"
    return cf_request("PATCH", url, token, record)


def ensure_dns_records(
    zone_id: str,
    token: str,
    hostnames: list[str],
    tunnel_id: str,
) -> None:
    """Ensure each hostname has a proxied CNAME to <tunnel-id>.cfargotunnel.com."""
    print("[dns] Ensuring DNS records...")
    records = list_dns_records(zone_id, token)
    by_name = {r["name"]: r for r in records}
    target = f"{tunnel_id}.cfargotunnel.com"

    for hostname in hostnames:
        existing = by_name.get(hostname)
        desired = {
            "type": "CNAME",
            "name": hostname,
            "content": target,
            "proxied": True,
            "ttl": 1,
        }
        if existing:
            if existing.get("type") != "CNAME" or existing.get("content") != target:
                print(f"[dns] Updating {hostname} -> {target}")
                patch_dns_record(zone_id, existing["id"], token, desired)
            else:
                print(f"[dns] {hostname} already correct")
        else:
            print(f"[dns] Creating {hostname} -> {target}")
            create_dns_record(zone_id, token, desired)


def rebuild_ingress(current_ingress: list[dict], desired: list[dict]) -> list[dict]:
    """Remove existing rules for our hostnames and prepend the desired rules."""
    our_hostnames = {rule["hostname"] for rule in desired if rule.get("hostname")}
    cleaned = [rule for rule in current_ingress if rule.get("hostname") not in our_hostnames]

    # Drop the catch-all so we can prepend our rules and re-add it last.
    catch_all = [rule for rule in cleaned if not rule.get("hostname")]
    named = [rule for rule in cleaned if rule.get("hostname")]

    new_ingress = desired + named
    if catch_all:
        new_ingress.extend(catch_all)
    else:
        new_ingress.append({"service": "http_status:404"})
    return new_ingress


def verify_public_endpoints(hostnames: list[str], timeout_seconds: int = 120) -> bool:
    """Poll each public hostname until it returns a non-5xx status or timeout."""
    print("[verify] Checking public availability...")
    deadline = time.time() + timeout_seconds
    remaining = list(hostnames)
    ok: list[str] = []

    while remaining and time.time() < deadline:
        hostname = remaining.pop(0)
        url = f"https://{hostname}/health"
        try:
            req = urllib.request.Request(url, method="GET", headers={"User-Agent": "ctrader-services-init"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        except Exception as exc:
            print(f"[verify] {hostname} not ready yet ({exc}); retrying...")
            remaining.append(hostname)
            time.sleep(3)
            continue

        if 200 <= status < 500:
            print(f"[verify] {hostname} responded HTTP {status}")
            ok.append(hostname)
        else:
            print(f"[verify] {hostname} returned HTTP {status}; retrying...")
            remaining.append(hostname)
            time.sleep(3)

    if remaining:
        print(f"[verify] Timed out waiting for: {', '.join(remaining)}", file=sys.stderr)
        return False
    print("[verify] All public endpoints are reachable.")
    return True


def main() -> int:
    load_env()
    token = require_env("CF_API_TOKEN")
    account_id = require_env("CF_ACCOUNT_ID")
    zone_id = require_env("CF_ZONE_ID")
    tunnel_id = require_env("CF_TUNNEL_ID")

    hostnames = [rule["hostname"] for rule in load_ingress()]

    print(f"[init] Tunnel: {tunnel_id}")
    print(f"[init] Publishing hostnames: {', '.join(hostnames)}")

    # 1. Read existing tunnel config.
    current = get_tunnel_config(account_id, tunnel_id, token)
    current_ingress = current.get("config", {}).get("ingress", [])

    # 2. Rebuild ingress rules.
    desired_ingress = load_ingress()
    new_ingress = rebuild_ingress(current_ingress, desired_ingress)

    # 3. Push updated tunnel config.
    print("[init] Updating tunnel ingress rules...")
    update_tunnel_config(account_id, tunnel_id, token, new_ingress)
    print(json.dumps(new_ingress, indent=2))
    print("[init] Tunnel ingress updated.")

    # 4. Ensure DNS records.
    ensure_dns_records(zone_id, token, hostnames, tunnel_id)

    # 5. Verify public availability.
    if not verify_public_endpoints(hostnames):
        return 1

    print("[init] Done. Services are published and reachable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
