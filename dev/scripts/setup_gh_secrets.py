#!/usr/bin/env python3
"""
dev/scripts/setup_gh_secrets.py

Fetches Cloudflare API token from Bitwarden, populates .env with CF_ vars,
and sets all required GitHub Actions secrets for the deployment workflow.

Idempotent: safe to rerun. Only updates .env if CF_API_TOKEN is missing.

Usage:
    ./dev.sh setup-gh-secrets
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
ENV_FILE = PROJECT_ROOT / ".env"
REPO = "mrme000m/ssfx-appwrite"

# Cloudflare constants (not secrets)
CF_ACCOUNT_ID = "4f6d43db5dbe773f750a2c8f941d0cdc"
CF_ZONE_ID = "5290d99f626b08c46c1eca6cc7cfa090"
BW_ITEM_NAME = "Cloudflare — mrme.tech"


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def load_env():
    if not ENV_FILE.exists():
        return {}
    env = {}
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                env[key.strip()] = val.strip()
    return env


def ensure_env_key(env_dict, key, value):
    """Add key=value to .env if not present. Returns updated env dict."""
    if key in env_dict and env_dict[key]:
        return env_dict
    env_dict[key] = value
    lines = []
    existing = {}
    if ENV_FILE.exists():
        with open(ENV_FILE) as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    k, _ = stripped.split("=", 1)
                    existing[k.strip()] = line
                    if k.strip() == key:
                        lines.append(f"{key}={value}\n")
                        continue
                lines.append(line)
    if key not in existing:
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append(f"\n# Cloudflare (auto-populated by setup_gh_secrets.py)\n")
        lines.append(f"{key}={value}\n")
    with open(ENV_FILE, "w") as f:
        f.writelines(lines)
    print(f"[gh-secrets] Added {key} to .env")
    return env_dict


def get_bw_session():
    bw_password = run(
        ["security", "find-generic-password", "-a", "bw-master-password", "-w"]
    ).stdout.strip()
    if not bw_password:
        print("[gh-secrets] No bw master password in keychain", file=sys.stderr)
        sys.exit(1)
    env = {**os.environ, "BW_PASSWORD": bw_password}
    session = run(
        ["bw", "unlock", "--passwordenv", "BW_PASSWORD", "--raw"],
        env=env,
    ).stdout.strip()
    if not session:
        print("[gh-secrets] Could not unlock Bitwarden", file=sys.stderr)
        sys.exit(1)
    return session


def get_cf_token(bw_session):
    result = run(
        ["bw", "get", "item", BW_ITEM_NAME, "--session", bw_session]
    )
    if result.returncode != 0:
        print(f"[gh-secrets] Bitwarden item '{BW_ITEM_NAME}' not found", file=sys.stderr)
        sys.exit(1)
    item = json.loads(result.stdout)
    for field in item.get("fields", []):
        if field.get("name") == "api_token":
            return field["value"]
    print(f"[gh-secrets] No 'api_token' field in '{BW_ITEM_NAME}'", file=sys.stderr)
    sys.exit(1)


def set_gh_secret(name, value):
    result = run(
        ["gh", "secret", "set", name, "-R", REPO, "-b", value]
    )
    if result.returncode == 0:
        print(f"[gh-secrets] ✓ {name}")
    else:
        print(f"[gh-secrets] ✗ {name}: {result.stderr}", file=sys.stderr)
        sys.exit(1)


def read_function_env(function_id):
    """Read .env file from a function directory."""
    fn_env = PROJECT_ROOT / "functions" / function_id / ".env"
    if not fn_env.exists():
        return {}
    env = {}
    with open(fn_env) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                env[key.strip()] = val.strip()
    return env


def main():
    env = load_env()

    # Ensure Appwrite secrets are in env
    aw_keys = ["APPWRITE_ENDPOINT", "APPWRITE_PROJECT_ID", "APPWRITE_API_KEY"]
    missing = [k for k in aw_keys if not env.get(k)]
    if missing:
        print(f"[gh-secrets] Missing from .env: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    # Fetch CF token from Bitwarden if not in .env
    cf_token = env.get("CF_API_TOKEN")
    if not cf_token:
        print("[gh-secrets] CF_API_TOKEN not in .env — fetching from Bitwarden...")
        bw_session = get_bw_session()
        cf_token = get_cf_token(bw_session)
        env = ensure_env_key(env, "CF_API_TOKEN", cf_token)

    # Ensure CF account/zone IDs in .env
    env = ensure_env_key(env, "CF_ACCOUNT_ID", CF_ACCOUNT_ID)
    env = ensure_env_key(env, "CF_ZONE_ID", CF_ZONE_ID)

    # Set all GitHub Actions secrets
    print(f"\n[gh-secrets] Setting secrets on {REPO}...")
    set_gh_secret("APPWRITE_ENDPOINT", env["APPWRITE_ENDPOINT"])
    set_gh_secret("APPWRITE_PROJECT_ID", env["APPWRITE_PROJECT_ID"])
    set_gh_secret("APPWRITE_API_KEY", env["APPWRITE_API_KEY"])
    set_gh_secret("CF_API_TOKEN", cf_token)
    set_gh_secret("CF_ACCOUNT_ID", CF_ACCOUNT_ID)
    set_gh_secret("CF_ZONE_ID", CF_ZONE_ID)

    # Function-specific secrets from function .env files
    fn_envs = {
        "ctrader-auth": read_function_env("ctrader-auth"),
        "ctrader-internal": read_function_env("ctrader-internal"),
    }
    fn_secret_keys = {
        "ctrader-auth": ["CTRADER_CLIENT_ID", "CTRADER_CLIENT_SECRET",
                         "TOKEN_ENCRYPTION_KEY", "SESSION_HMAC_KEY"],
        "ctrader-internal": ["INTERNAL_API_KEY"],
    }
    for fn_id, keys in fn_secret_keys.items():
        for key in keys:
            val = fn_envs[fn_id].get(key)
            if val:
                set_gh_secret(key, val)

    total = 6 + sum(len(v) for v in fn_secret_keys.values())
    print(f"\n[gh-secrets] Done. {total} secrets set on {REPO}.")
    print("[gh-secrets] The GitHub Actions workflow can now deploy from develop branch.")


if __name__ == "__main__":
    main()
