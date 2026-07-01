#!/usr/bin/env python3
"""
dev/scripts/cleanup_appwrite_resources.py

Lists orphaned Appwrite resources that exist in the cloud but are not
tracked in local config files. Marks them for cleanup.

Checks:
- Sites in Appwrite Cloud not in appwrite/sites.json
- Functions in Appwrite Cloud not in appwrite/functions.json
- Stale proxy rules (old deployments, deleted resources)
- Old function deployments beyond retention

Usage:
    ./dev.sh cleanup-appwrite-resources           # List only (safe)
    ./dev.sh cleanup-appwrite-resources --delete   # Actually delete orphans
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _config import ZONE_DOMAIN

# ─── Env ───────────────────────────────────────────────────────────────────

def load_env():
    root = Path(__file__).parent.parent.parent
    env_file = root / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key, val.strip())


APPWRITE_HEADERS = {}

def init_appwrite():
    missing = [k for k in ["APPWRITE_ENDPOINT", "APPWRITE_PROJECT_ID", "APPWRITE_API_KEY"] if not os.environ.get(k)]
    if missing:
        print(f"Error: missing env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    APPWRITE_HEADERS.update({
        "X-Appwrite-Project": os.environ["APPWRITE_PROJECT_ID"],
        "X-Appwrite-Key": os.environ["APPWRITE_API_KEY"],
        "X-Appwrite-Response-Format": "1.9.5",
        "Content-Type": "application/json",
    })


def appwrite_api(method, path, payload=None):
    # APPWRITE_ENDPOINT already includes /v1, so strip it from path if present
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
        print(f"[cleanup] Appwrite API error {e.code}: {body}", file=sys.stderr)
        raise


def run_cli(args):
    """Run appwrite CLI command, return parsed JSON or None."""
    try:
        result = subprocess.run(
            ["appwrite"] + args,
            capture_output=True, text=True, check=True,
            env={**os.environ}
        )
        # Try to parse JSON, handle extra output
        stdout = result.stdout.strip()
        # Find first { and last } for JSON extraction
        start = stdout.find("{")
        end = stdout.rfind("}")
        if start >= 0 and end > start:
            return json.loads(stdout[start:end+1])
        if stdout.startswith("["):
            end = stdout.rfind("]")
            if end > 0:
                return json.loads(stdout[:end+1])
        return None
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None


# ─── Local config ──────────────────────────────────────────────────────────

def load_local_config(filename):
    root = Path(__file__).parent.parent.parent
    filepath = root / "appwrite" / filename
    if not filepath.exists():
        return []
    with open(filepath) as f:
        return json.load(f)


# ─── Checks ────────────────────────────────────────────────────────────────

def check_sites(delete=False):
    print("\n" + "=" * 70)
    print("SITES: Local config vs Appwrite Cloud")
    print("=" * 70)

    local_sites = load_local_config("sites.json")
    local_ids = {s["$id"] for s in local_sites}
    print(f"Local sites.json: {sorted(local_ids)}")

    cloud_sites = run_cli(["sites", "list", "--json"]) or []
    cloud_list = cloud_sites.get("sites", cloud_sites) if isinstance(cloud_sites, dict) else cloud_sites

    print(f"Cloud sites:     {sorted(s.get('$id', s.get('id','')) for s in cloud_list)}")

    orphans = [s for s in cloud_list if s.get("$id", s.get("id", "")) not in local_ids]

    if not orphans:
        print("✓ No orphaned sites.")
        return

    print(f"\n⚠ {len(orphans)} orphaned site(s) in Appwrite Cloud:")
    for s in orphans:
        sid = s.get("$id", s.get("id", ""))
        name = s.get("name", sid)
        created = s.get("$createdAt", "?")[:10]
        print(f"  - {sid} ({name}) created {created}")
        print(f"    Delete: appwrite sites delete --site-id {sid}")

    if delete:
        print("\nDeleting orphaned sites...")
        for s in orphans:
            sid = s.get("$id", s.get("id", ""))
            print(f"  Deleting {sid}...")
            subprocess.run(["appwrite", "sites", "delete", "--site-id", sid], check=False)
            print(f"    Deleted.")


def check_functions(delete=False):
    print("\n" + "=" * 70)
    print("FUNCTIONS: Local config vs Appwrite Cloud")
    print("=" * 70)

    local_funcs = load_local_config("functions.json")
    local_ids = {f["$id"] for f in local_funcs}
    print(f"Local functions.json: {sorted(local_ids)}")

    cloud_funcs = run_cli(["functions", "list", "--json"]) or []
    cloud_list = cloud_funcs.get("functions", cloud_funcs) if isinstance(cloud_funcs, dict) else cloud_funcs

    print(f"Cloud functions:     {sorted(f.get('$id', f.get('id','')) for f in cloud_list)}")

    orphans = [f for f in cloud_list if f.get("$id", f.get("id", "")) not in local_ids]

    if not orphans:
        print("✓ No orphaned functions.")
    else:
        print(f"\n⚠ {len(orphans)} orphaned function(s):")
        for f in orphans:
            fid = f.get("$id", f.get("id", ""))
            print(f"  - {fid}")
            print(f"    Delete: appwrite functions delete --function-id {fid}")
        if delete:
            for f in orphans:
                fid = f.get("$id", f.get("id", ""))
                print(f"  Deleting {fid}...")
                subprocess.run(["appwrite", "functions", "delete", "--function-id", fid], check=False)


def check_proxy_rules(delete=False):
    print("\n" + "=" * 70)
    print("PROXY RULES: Stale / duplicate rules")
    print("=" * 70)

    rules = appwrite_api("GET", "/v1/proxy/rules").get("rules", [])

    # Group by resource — find duplicates (multiple rules for same resource)
    by_resource = {}
    for r in rules:
        rid = r.get("deploymentResourceId", "")
        rtype = r.get("deploymentResourceType", "")
        key = f"{rtype}:{rid}"
        by_resource.setdefault(key, []).append(r)

    duplicates = {k: v for k, v in by_resource.items() if len(v) > 3}

    if duplicates:
        print(f"⚠ Found {len(duplicates)} resource(s) with many proxy rules (old deployments):")
        for key, rule_list in sorted(duplicates.items()):
            print(f"\n  {key} ({len(rule_list)} rules):")
            for r in sorted(rule_list, key=lambda x: x.get("$createdAt", "")):
                domain = r.get("domain", "")
                created = r.get("$createdAt", "?")[:10]
                status = r.get("status", "?")
                rule_id = r.get("$id", "")
                is_custom = ZONE_DOMAIN in domain
                marker = "★ CUSTOM" if is_custom else ""
                print(f"    [{created}] {domain[:50]:<50} {status:<12} {marker}")
                if not is_custom:
                    print(f"      Delete: appwrite proxy delete-rule --rule-id {rule_id}")

        if delete:
            print("\nDeleting auto-generated deployment rules (keeping custom domains)...")
            for key, rule_list in duplicates.items():
                for r in rule_list:
                    domain = r.get("domain", "")
                    if ZONE_DOMAIN not in domain:
                        rule_id = r.get("$id", "")
                        print(f"  Deleting {rule_id} ({domain[:40]})...")
                        try:
                            appwrite_api("DELETE", f"/v1/proxy/rules/{rule_id}")
                            print(f"    Deleted.")
                        except Exception as e:
                            print(f"    Error: {e}")
    else:
        print("✓ No excessive duplicate proxy rules.")

    # Show custom domain rules summary
    custom_rules = [r for r in rules if ZONE_DOMAIN in r.get("domain", "")]
    print(f"\nCustom domain rules ({len(custom_rules)}):")
    for r in custom_rules:
        domain = r.get("domain", "")
        rid = r.get("deploymentResourceId", "")
        status = r.get("status", "?")
        print(f"  {domain:<35} -> {rid:<30} {status}")


def check_old_deployments():
    print("\n" + "=" * 70)
    print("FUNCTION DEPLOYMENTS: Retention check")
    print("=" * 70)

    local_funcs = load_local_config("functions.json")
    for f in local_funcs:
        fid = f["$id"]
        retention = f.get("deploymentRetention", 7)
        deployments = run_cli(["functions", "list-deployments", "--function-id", fid, "--json"]) or []
        dep_list = deployments.get("deployments", deployments) if isinstance(deployments, dict) else deployments

        if len(dep_list) > retention:
            old = dep_list[retention:]
            print(f"  {fid}: {len(dep_list)} deployments (retention: {retention}) — {len(old)} can be cleaned")
            for d in old[:3]:
                did = d.get("$id", d.get("id", ""))
                print(f"    {did}")
            if len(old) > 3:
                print(f"    ... and {len(old)-3} more")
        else:
            print(f"  {fid}: {len(dep_list)} deployments (within retention limit)")


# ─── Main ──────────────────────────────────────────────────────────────────

def main():
    load_env()
    init_appwrite()

    delete = "--delete" in sys.argv

    print("=" * 70)
    print("APPWRITE RESOURCE CLEANUP REPORT")
    print("=" * 70)
    print(f"Mode: {'DELETE' if delete else 'LIST ONLY (safe)'}")

    check_sites(delete)
    check_functions(delete)
    check_proxy_rules(delete)
    check_old_deployments()

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    if not delete:
        print("This was a dry run. To actually delete orphaned resources, run:")
        print("  ./dev.sh cleanup-appwrite-resources --delete")
    else:
        print("Cleanup complete.")
    print()


if __name__ == "__main__":
    main()
