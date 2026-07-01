#!/usr/bin/env python3
"""Update Cloudflare tunnel ingress rules from a JSON config file."""
import json
import os
import sys
import urllib.request

from _config import CF_ACCOUNT_ID, CF_TUNNEL_ID


def cf_api_put(url: str, token: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("success"):
        errors = data.get("errors", [])
        raise RuntimeError(f"Cloudflare API error: {errors}")
    return data.get("result", {})


def main() -> int:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_file = sys.argv[1] if len(sys.argv) > 1 else os.path.join(script_dir, "cf-tunnel-config.json")

    token = os.getenv("CF_API_TOKEN")
    if not token:
        print(
            "Error: CF_API_TOKEN is not set. Set it in .env or export it before running this script.",
            file=sys.stderr,
        )
        print(
            "The token can be retrieved from the Bitwarden item 'Cloudflare — mrme.tech'.",
            file=sys.stderr,
        )
        return 1

    if not os.path.isfile(config_file):
        print(f"Error: tunnel config file not found: {config_file}", file=sys.stderr)
        return 1

    with open(config_file, "r", encoding="utf-8") as f:
        config = json.load(f)

    ingress = config.get("ingress")
    if not ingress:
        print("Error: config file must contain an 'ingress' array.", file=sys.stderr)
        return 1

    print(f"[dev] Updating tunnel {CF_TUNNEL_ID} ingress rules from {config_file}...")
    result = cf_api_put(
        f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/cfd_tunnel/{CF_TUNNEL_ID}/configurations",
        token,
        {"config": {"ingress": ingress, "warp_routing": None}},
    )
    print(json.dumps(result.get("config", {}).get("ingress", []), indent=2))
    print("[dev] Tunnel ingress rules updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
