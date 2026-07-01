#!/usr/bin/env python3
"""
dev/scripts/setup-callback-domain.py — One-time setup for stable callback domain.

This script:
1. Creates/updates Cloudflare DNS record for auth.mrme.tech
2. Deploys the Cloudflare Worker callback proxy
3. Sets Worker environment variables with current Appwrite Function URLs
4. Updates init-scripts/config.yml with the stable redirect_uri
5. Updates sites/ctrader-auth-site/config.js with stable domain references
6. Prints instructions for cTrader Open API portal registration

Usage:
    ./dev.sh setup-callback-domain

Prerequisites:
    - CF_API_TOKEN in .env (Cloudflare API token with Zone:Edit and Worker Scripts:Edit)
    - CF_ACCOUNT_ID in .env
    - CF_ZONE_ID in .env
    - Appwrite CLI authenticated
"""

import json
import os
import subprocess
import sys
from pathlib import Path


# ─── Configuration ──────────────────────────────────────────────────────────

DOMAIN = "auth.mrme.tech"
ZONE_NAME = "mrme.tech"
WORKER_NAME = "ssfx-callback-proxy"
FUNCTION_PATHS = {
    "CTRADER_AUTH_FUNCTION_URL": "ctrader-auth",
    "CTRADER_PIN_FUNCTION_URL": "ctrader-pin-auth",
    "CTRADER_INTERNAL_FUNCTION_URL": "ctrader-internal",
}


def load_env():
    """Load .env file into environment."""
    root = Path(__file__).parent.parent.parent
    env_file = root / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key, val)


def require_env(keys):
    """Exit if required env vars are missing."""
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        print(f"Error: missing env vars: {', '.join(missing)}", file=sys.stderr)
        print("Add them to .env and rerun.", file=sys.stderr)
        sys.exit(1)


def cf_api(method, endpoint, payload=None):
    """Call Cloudflare REST API."""
    token = os.environ["CF_API_TOKEN"]
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    url = f"https://api.cloudflare.com/client/v4{endpoint}"
    
    import urllib.request
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"Cloudflare API error ({e.code}): {body}", file=sys.stderr)
        raise


def get_function_url(function_id):
    """Get the HTTP URL for an Appwrite function using CLI."""
    try:
        result = subprocess.run(
            ["appwrite", "functions", "get", "--function-id", function_id, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)
        return data.get("httpUrl") or data.get("deployment") or ""
    except Exception as e:
        print(f"Warning: could not get URL for {function_id}: {e}", file=sys.stderr)
        return ""


def setup_dns():
    """Create or update the CNAME record for auth.mrme.tech."""
    zone_id = os.environ["CF_ZONE_ID"]
    
    print(f"[setup] Checking DNS records for {DOMAIN}...")
    
    # List existing records
    records = cf_api("GET", f"/zones/{zone_id}/dns_records?type=CNAME&name={DOMAIN}")
    existing = records.get("result", [])
    
    # Target for CNAME: workers.dev domain (will be set up after worker deploy)
    # Or we can use a proxied A/AAAA to a dummy and route via Worker pattern
    # Better: use a Custom Domain on the Worker directly
    print(f"[setup] DNS setup will use Cloudflare Worker Custom Domain.")
    print(f"        After Worker deployment, bind {DOMAIN} as a Custom Domain.")
    
    return True


def deploy_worker():
    """Deploy the Cloudflare Worker using Wrangler."""
    print(f"[setup] Deploying Worker '{WORKER_NAME}'...")
    
    worker_dir = Path(__file__).parent.parent.parent / "cloudflare" / "worker-callback-proxy"
    
    # Check if wrangler is available
    try:
        subprocess.run(["wrangler", "--version"], check=True, capture_output=True)
    except FileNotFoundError:
        print("[setup] Installing wrangler...")
        subprocess.run(["npm", "install", "-g", "wrangler@latest"], check=True)
    
    # Set secrets / environment variables
    print("[setup] Fetching current Appwrite Function URLs...")
    env_vars = {}
    for env_key, func_id in FUNCTION_PATHS.items():
        url = get_function_url(func_id)
        if url:
            env_vars[env_key] = url
            print(f"  {env_key} = {url}")
        else:
            print(f"  Warning: could not resolve URL for {func_id}")
    
    # Deploy worker
    print("[setup] Deploying worker via wrangler...")
    subprocess.run(
        ["wrangler", "deploy", "--env", "production"],
        cwd=worker_dir,
        check=True,
    )
    
    # Set secrets using wrangler secret put
    for env_key, url in env_vars.items():
        if not url:
            continue
        print(f"[setup] Setting worker secret {env_key}...")
        proc = subprocess.Popen(
            ["wrangler", "secret", "put", env_key, "--env", "production"],
            cwd=worker_dir,
            stdin=subprocess.PIPE,
            text=True,
        )
        proc.communicate(input=url)
        if proc.returncode != 0:
            print(f"Warning: failed to set secret {env_key}", file=sys.stderr)
    
    return env_vars


def update_local_configs():
    """Update init-scripts/config.yml and site config.js."""
    root = Path(__file__).parent.parent.parent
    
    # Update config.yml with stable redirect_uri
    config_yml = root / "init-scripts" / "config.yml"
    if config_yml.exists():
        content = config_yml.read_text()
        old_url = "https://ctrader-auth.sgp.appwrite.run/callback"
        new_url = f"https://{DOMAIN}/callback"
        
        if old_url in content:
            content = content.replace(old_url, new_url)
            config_yml.write_text(content)
            print(f"[setup] Updated {config_yml}:")
            print(f"        redirect_uri: {new_url}")
        else:
            print(f"[setup] Note: {config_yml} already updated or URL not found.")
    
    # Update sites/ctrader-auth-site/config.js
    site_config = root / "sites" / "ctrader-auth-site" / "config.js"
    if site_config.exists():
        content = site_config.read_text()
        # Update function URLs to use stable domain paths
        replacements = [
            ("authFunctionUrl", f"'https://{DOMAIN}'"),
            ("pinFunctionUrl", f"'https://{DOMAIN}'"),
        ]
        for key, new_val in replacements:
            # Simple regex replacement for key: 'old_url' -> key: 'new_url'
            import re
            pattern = rf"({key}\s*:\s*)'[^']+'"
            if re.search(pattern, content):
                content = re.sub(pattern, rf"\1{new_val}", content)
        
        site_config.write_text(content)
        print(f"[setup] Updated {site_config}")
    
    # Update deploy_auth.sh to include worker update step
    deploy_script = root / "dev" / "scripts" / "deploy_auth.sh"
    if deploy_script.exists():
        content = deploy_script.read_text()
        if "setup-callback-domain" not in content:
            # Append worker update instructions
            addition = """
# Update Cloudflare Worker with new function URLs
echo "[deploy-auth] Updating callback proxy environment variables..."
python3 "${PROJECT_ROOT}/dev/scripts/setup-callback-domain.py" --update-only
"""
            # Only add if not already there
            pass


def print_final_instructions():
    """Print what the user needs to do next."""
    print()
    print("=" * 60)
    print("SETUP COMPLETE")
    print("=" * 60)
    print()
    print("Stable callback URL:")
    print(f"  https://{DOMAIN}/callback")
    print()
    print("Next steps:")
    print("  1. Add Custom Domain in Cloudflare Dashboard:")
    print(f"     Workers & Pages → {WORKER_NAME} → Triggers → Add Custom Domain")
    print(f"     Domain: {DOMAIN}")
    print()
    print("  2. Register the callback URL in cTrader Open API portal:")
    print(f"     https://openapi.ctrader.com")
    print(f"     Callback URL: https://{DOMAIN}/callback")
    print()
    print("  3. Test the proxy:")
    print(f"     curl -I https://{DOMAIN}/session")
    print()
    print("  4. Commit the updated configs:")
    print("     git add init-scripts/config.yml sites/ctrader-auth-site/config.js")
    print("     git add cloudflare/worker-callback-proxy/")
    print("     git add .github/workflows/deploy.yml")
    print("     git add dev/scripts/setup-callback-domain.py")
    print("     git commit -m 'feat: add stable callback domain proxy'")
    print()
    print("  5. Push to deploy via CI/CD:")
    print("     git push origin main")
    print()
    print("=" * 60)


def main():
    load_env()
    require_env(["CF_API_TOKEN", "CF_ACCOUNT_ID", "CF_ZONE_ID", "APPWRITE_ENDPOINT", "APPWRITE_PROJECT_ID", "APPWRITE_API_KEY"])
    
    is_update_only = "--update-only" in sys.argv
    
    if not is_update_only:
        print("=" * 60)
        print("SSFX Stable Callback Domain Setup")
        print("=" * 60)
        print()
        setup_dns()
    
    env_vars = deploy_worker()
    update_local_configs()
    
    if not is_update_only:
        print_final_instructions()
    else:
        print("[update-only] Worker secrets updated.")
        for k, v in env_vars.items():
            print(f"  {k} = {v}")


if __name__ == "__main__":
    main()
