#!/usr/bin/env python3
"""Check Cloudflare tunnel status and ingress rules."""
import json
import os
import sys
import urllib.request

from _config import CF_ACCOUNT_ID, CF_TUNNEL_ID, CF_ZONE_ID


def cf_api(url: str, token: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("success"):
        errors = data.get("errors", [])
        raise RuntimeError(f"Cloudflare API error: {errors}")
    return data.get("result", {})


def main() -> int:
    token = os.getenv("CF_API_TOKEN")
    if not token:
        print(
            "Warning: CF_API_TOKEN is not set. Set it in .env or export it before running this script.",
            file=sys.stderr,
        )
        print(
            "The token can be retrieved from the Bitwarden item 'Cloudflare — mrme.tech'.",
            file=sys.stderr,
        )
        return 1

    print("[dev] Cloudflare tunnel status:")
    tunnel = cf_api(
        f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/cfd_tunnel/{CF_TUNNEL_ID}",
        token,
    )
    print(
        json.dumps(
            {
                "name": tunnel.get("name"),
                "status": tunnel.get("status"),
                "connections": len(tunnel.get("connections", [])),
                "colos": [c.get("colo_name") for c in tunnel.get("connections", [])],
            },
            indent=2,
        )
    )

    print("\n[dev] Tunnel ingress rules:")
    config = cf_api(
        f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/cfd_tunnel/{CF_TUNNEL_ID}/configurations",
        token,
    )
    ingress = config.get("config", {}).get("ingress", [])
    for rule in ingress:
        hostname = rule.get("hostname") or "catch-all"
        print(f"  {hostname} -> {rule.get('service')}")

    print("\n[dev] Relevant DNS records:")
    records = cf_api(
        f"https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records?per_page=100",
        token,
    )
    for record in records:
        if record.get("name", "").endswith("mrme.tech"):
            print(
                f"  {record.get('type')} {record.get('name')} -> {record.get('content')} "
                f"(proxied: {record.get('proxied')})"
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
