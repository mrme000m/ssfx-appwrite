#!/usr/bin/env python3
"""
dev/scripts/setup_function_domains.py

Configures native Appwrite custom domains for all functions + site.
Eliminates the need for the Cloudflare Worker proxy layer.

For each function/site:
1. Creates an Appwrite Proxy Rule (custom domain) via REST API
2. Creates a Cloudflare CNAME record pointing to the generated domain
3. Triggers domain verification (TLS certificate auto-provisioning)

Usage:
    ./dev.sh setup-function-domains                # Setup all domains
    ./dev.sh setup-function-domains --verify-only  # Retry verification on unverified
    ./dev.sh setup-function-domains --status       # Show current status only
    ./dev.sh setup-function-domains --cleanup-worker  # Remove old Worker proxy

Prerequisites:
    - APPWRITE_ENDPOINT, APPWRITE_PROJECT_ID, APPWRITE_API_KEY in .env
    - CF_API_TOKEN in .env (Cloudflare API token with Zone:Edit)
      If not set, will attempt retrieval from Bitwarden vault item
      "Cloudflare — mrme.tech"
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _config import (
    CF_ACCOUNT_ID,
    CF_ZONE_ID,
    FUNCTION_DOMAINS,
    SITE_DOMAINS,
    ZONE_DOMAIN,
    load_env,
)

# ─── Env loading ────────────────────────────────────────────────────────────

def require_env(keys):
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        print(f"Error: missing env vars: {', '.join(missing)}", file=sys.stderr)
        print("Add them to .env and rerun.", file=sys.stderr)
        sys.exit(1)


def get_cf_api_token():
    token = os.environ.get("CF_API_TOKEN")
    if token:
        return token
    print("[domains] CF_API_TOKEN not in .env — trying Bitwarden...")
    try:
        bw_password = subprocess.run(
            ["security", "find-generic-password", "-a", "bw-master-password", "-w"],
            capture_output=True, text=True
        ).stdout.strip()
        if not bw_password:
            raise ValueError("No bw master password in keychain")
        bw_session = subprocess.run(
            ["bw", "unlock", "--passwordenv", "BW_PASSWORD", "--raw"],
            capture_output=True, text=True,
            env={**os.environ, "BW_PASSWORD": bw_password}
        ).stdout.strip()
        if not bw_session:
            raise ValueError("Could not unlock Bitwarden")
        item = subprocess.run(
            ["bw", "get", "item", "Cloudflare — mrme.tech", "--session", bw_session],
            capture_output=True, text=True
        ).stdout
        token = json.loads(item)["fields"]
        token = next(f["value"] for f in token if f["name"] == "api_token")
        print("[domains] Retrieved CF_API_TOKEN from Bitwarden.")
        return token
    except Exception as e:
        print(f"[domains] Could not retrieve CF_API_TOKEN from Bitwarden: {e}", file=sys.stderr)
        print("Set CF_API_TOKEN in .env manually.", file=sys.stderr)
        sys.exit(1)


# ─── Appwrite REST API ─────────────────────────────────────────────────────

APPWRITE_HEADERS = {}

def init_appwrite():
    require_env(["APPWRITE_ENDPOINT", "APPWRITE_PROJECT_ID", "APPWRITE_API_KEY"])
    APPWRITE_HEADERS.update({
        "X-Appwrite-Project": os.environ["APPWRITE_PROJECT_ID"],
        "X-Appwrite-Key": os.environ["APPWRITE_API_KEY"],
        "X-Appwrite-Response-Format": "1.9.5",
        "Content-Type": "application/json",
    })


def appwrite_api(method, path, payload=None):
    endpoint = os.environ["APPWRITE_ENDPOINT"].rstrip("/")
    if path.startswith("/v1/"):
        path = path[3:]  # strip /v1 prefix
    url = f"{endpoint}{path}"
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(url, data=data, headers=APPWRITE_HEADERS, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            err = json.loads(body)
            msg = err.get("message", body)
        except Exception:
            msg = body
        if e.code == 409:
            return {"error": "conflict", "message": msg}
        print(f"[domains] Appwrite API error {e.code}: {msg}", file=sys.stderr)
        raise


def list_proxy_rules():
    data = appwrite_api("GET", "/v1/proxy/rules")
    return data.get("rules", [])


def get_site_generated_domain(site_id: str) -> str | None:
    """Fetch the auto-generated domain for a site from the Appwrite sites API."""
    try:
        data = appwrite_api("GET", f"/v1/sites/{site_id}")
        return data.get("domain", "")
    except Exception as exc:
        print(f"[domains] Could not fetch generated domain for site {site_id}: {exc}", file=sys.stderr)
        return None


def get_generated_domains():
    """Map function_id -> generated domain from existing deployment rules."""
    rules = list_proxy_rules()
    result = {}
    for r in rules:
        domain = r.get("domain", "")
        rtype = r.get("deploymentResourceType", "")
        rid = r.get("deploymentResourceId", "")
        if "appwrite.run" in domain and rtype == "function":
            result[rid] = domain
    return result


def create_function_rule(domain, function_id):
    return appwrite_api("POST", "/v1/proxy/rules/function", {
        "domain": domain,
        "functionId": function_id,
    })


def create_site_rule(domain, site_id):
    return appwrite_api("POST", "/v1/proxy/rules/site", {
        "domain": domain,
        "siteId": site_id,
    })


def verify_rule(rule_id):
    try:
        return appwrite_api("PATCH", f"/v1/proxy/rules/{rule_id}/verification")
    except urllib.error.HTTPError as e:
        if e.code == 400:
            body = e.read().decode()
            try:
                err = json.loads(body)
                msg = err.get("message", body)
            except Exception:
                msg = body
            return {"error": "verification_pending", "message": msg}
        raise


def delete_rule(rule_id):
    return appwrite_api("DELETE", f"/v1/proxy/rules/{rule_id}")


# ─── Cloudflare DNS API ────────────────────────────────────────────────────

CF_TOKEN = None

def cf_api(method, endpoint, payload=None):
    url = f"https://api.cloudflare.com/client/v4{endpoint}"
    headers = {
        "Authorization": f"Bearer {CF_TOKEN}",
        "Content-Type": "application/json",
    }
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"[domains] Cloudflare API error {e.code}: {body}", file=sys.stderr)
        raise


def find_cname(zone_id, name):
    records = cf_api("GET", f"/zones/{zone_id}/dns_records?type=CNAME&name={name}")
    results = records.get("result", [])
    return results[0] if results else None


def create_cname(zone_id, name, target, proxied=False):
    return cf_api("POST", f"/zones/{zone_id}/dns_records", {
        "type": "CNAME",
        "name": name,
        "content": target,
        "proxied": proxied,
        "comment": "Appwrite native custom domain",
    })


def update_cname(zone_id, record_id, name, target, proxied=False):
    return cf_api("PATCH", f"/zones/{zone_id}/dns_records/{record_id}", {
        "type": "CNAME",
        "name": name,
        "content": target,
        "proxied": proxied,
    })


def delete_dns_record(zone_id, record_id):
    return cf_api("DELETE", f"/zones/{zone_id}/dns_records/{record_id}")


# ─── Main logic ────────────────────────────────────────────────────────────

def print_status(rules, generated):
    print("\n" + "=" * 80)
    print("DOMAIN STATUS")
    print("=" * 80)
    print(f"{'Domain':<35} {'Resource':<30} {'Status':<12} {'Trigger'}")
    print("-" * 80)

    custom_rules = {}
    for r in rules:
        domain = r.get("domain", "")
        if ZONE_DOMAIN in domain:
            rid = r.get("deploymentResourceId", "")
            custom_rules[domain] = r
            print(f"{domain:<35} {rid:<30} {r.get('status',''):<12} {r.get('trigger','')}")

    print()
    print("Function generated domains:")
    for fid, domain in sorted(generated.items()):
        custom = "✓" if any(
            r.get("deploymentResourceId") == fid and ZONE_DOMAIN in r.get("domain", "")
            for r in rules
        ) else "✗"
        print(f"  {custom} {fid:<35} -> {domain}")

    unverified = [r for r in rules if r.get("status") != "verified" and ZONE_DOMAIN in r.get("domain", "")]
    if unverified:
        print(f"\n⚠ {len(unverified)} domain(s) need verification:")
        for r in unverified:
            print(f"  - {r.get('domain','')} (rule: {r.get('$id','')})")
            logs = r.get("logs", "")
            if logs:
                last_line = logs.strip().split("\n")[-1] if logs.strip() else ""
                print(f"    Last log: {last_line}")

    print("=" * 80)


def setup_domain(name, resource_id, resource_type, generated_domains):
    """Setup a custom domain for a function or site."""
    rules = list_proxy_rules()

    existing = None
    for r in rules:
        if r.get("domain") == name:
            existing = r
            break

    if existing and existing.get("status") == "verified":
        existing_rid = existing.get("deploymentResourceId", "")
        if existing_rid == resource_id:
            print(f"[domains] ✓ {name} -> {resource_id} already verified")
            return True
        # Resource mismatch: delete stale rule and recreate.
        print(f"[domains] ⟳ {name} points to {existing_rid}, remapping to {resource_id}...")
        delete_rule(existing["$id"])
        existing = None

    if existing and existing.get("status") != "verified":
        print(f"[domains] ⟳ {name} exists but unverified — retrying verification...")
        verify_rule(existing["$id"])
        return True

    # Need to create the rule
    # Determine CNAME target:
    # - Functions: sgp.cloud.appwrite.io (regional endpoint, same as verified auth.mrme.tech)
    # - Sites: site's generated domain (*.appwrite.network)
    if resource_type == "function":
        cname_target = "sgp.cloud.appwrite.io"
        print(f"[domains] + Creating proxy rule: {name} -> {resource_id}")
        result = create_function_rule(name, resource_id)
    else:  # site
        cname_target = get_site_generated_domain(resource_id)
        if not cname_target:
            print(f"[domains] ⚠ Could not find generated domain for site {resource_id}")
            return False
        print(f"[domains] + Creating site proxy rule: {name} -> {resource_id}")
        result = create_site_rule(name, resource_id)

    if result.get("error") == "conflict":
        print(f"[domains] ℹ {name} rule already exists (race)")
        return True

    rule_id = result.get("$id")
    if not rule_id:
        print(f"[domains] ⚠ Unexpected response: {result}")
        return False

    # Create/update CNAME in Cloudflare
    print(f"[domains] + Creating CNAME: {name} -> {cname_target}")
    existing_cname = find_cname(CF_ZONE_ID, name)
    if existing_cname:
        if existing_cname["content"] != cname_target:
            print(f"[domains] ⟳ Updating existing CNAME (was: {existing_cname['content']})")
            update_cname(CF_ZONE_ID, existing_cname["id"], name, cname_target)
        else:
            print(f"[domains] ✓ CNAME already correct")
    else:
        create_cname(CF_ZONE_ID, name, cname_target)
        print(f"[domains] ✓ CNAME created")

    # Trigger verification
    print(f"[domains] ⟳ Triggering verification...")
    result = verify_rule(rule_id)
    if result.get("error") == "verification_pending":
        print(f"[domains] ℹ DNS not yet propagated — verification will retry automatically.")
        print(f"          Run './dev.sh setup-function-domains --verify-only' in a few minutes.")
    else:
        print(f"[domains] ✓ Verification triggered")

    return True


def cleanup_worker():
    """Remove Cloudflare Worker proxy resources (no longer needed)."""
    print("\n[domains] Cleaning up Cloudflare Worker proxy resources...")

    # 1. Remove the auth.mrme.tech CNAME pointing to workers.dev (if any)
    #    The native domain CNAME will replace it
    # 2. The Worker itself can be deleted via wrangler

    # Check for Worker-related DNS records
    records = cf_api("GET", f"/zones/{CF_ZONE_ID}/dns_records?per_page=100")
    worker_records = []
    for r in records.get("result", []):
        name = r.get("name", "")
        content = r.get("content", "")
        if "workers.dev" in content or "ssfx-callback-proxy" in content:
            worker_records.append(r)

    if worker_records:
        print(f"[domains] Found {len(worker_records)} Worker-related DNS record(s):")
        for r in worker_records:
            print(f"  {r['type']} {r['name']} -> {r['content']} (id: {r['id']})")
            delete_dns_record(CF_ZONE_ID, r["id"])
            print(f"    Deleted.")
    else:
        print("[domains] No Worker-related DNS records found.")

    # Delete the Worker itself
    print("\n[domains] To delete the Cloudflare Worker:")
    print("  wrangler delete --name ssfx-callback-proxy")
    print()


def main():
    load_env()
    init_appwrite()

    mode = sys.argv[1] if len(sys.argv) > 1 else ""

    if mode == "--status":
        rules = list_proxy_rules()
        generated = get_generated_domains()
        print_status(rules, generated)
        return

    if mode == "--verify-only":
        rules = list_proxy_rules()
        generated = get_generated_domains()
        print_status(rules, generated)
        print("\n[domains] Retrying verification on all custom domains...")
        for r in rules:
            if ZONE_DOMAIN in r.get("domain", "") and r.get("status") != "verified":
                print(f"  Verifying {r['domain']} (rule: {r['$id']})...")
                verify_rule(r["$id"])
        print("[domains] Verification triggered. Check status in a few minutes:")
        print("  ./dev.sh setup-function-domains --status")
        return

    # Modes that need CF API token
    global CF_TOKEN
    CF_TOKEN = get_cf_api_token()

    if mode == "--cleanup-worker":
        cleanup_worker()
        return

    # Full setup
    rules = list_proxy_rules()
    generated = get_generated_domains()
    print_status(rules, generated)

    print("\n[domains] Setting up function domains...")
    for func_id, domain in FUNCTION_DOMAINS.items():
        setup_domain(domain, func_id, "function", generated)

    print("\n[domains] Setting up site domains...")
    for site_id, domain in SITE_DOMAINS.items():
        setup_domain(domain, site_id, "site", generated)

    # Final status
    rules = list_proxy_rules()
    print_status(rules, generated)

    print("\n[domains] Setup complete. DNS propagation + TLS provisioning may take")
    print("         a few minutes. Check status with:")
    print("  ./dev.sh setup-function-domains --status")
    print()
    print("Next steps:")
    print("  1. Verify all domains show 'verified' status")
    print("  2. Update site config.js if any domains changed")
    print("  3. Remove Worker proxy: ./dev.sh setup-function-domains --cleanup-worker")
    print("  4. Register callback URL in cTrader Open API portal:")
    print("     https://auth.mrme.tech/callback")


if __name__ == "__main__":
    main()
