#!/usr/bin/env python3
"""
dev/scripts/deploy_site.py — Deploy one or all Appwrite Sites using API-key auth.

Usage:
    ./dev.sh deploy-site [site-id]          # deploy a specific site
    ./dev.sh deploy-site --all              # deploy all sites in appwrite/sites.json
"""

import argparse
import json
import os
import sys
import tarfile
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _config import SITE_DOMAINS, load_env

# ─── Configuration ──────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
APPWRITE_DIR = PROJECT_ROOT / "appwrite"
SITES_JSON = APPWRITE_DIR / "sites.json"

REQUIRED_ENV = ["APPWRITE_ENDPOINT", "APPWRITE_PROJECT_ID", "APPWRITE_API_KEY"]

DEFAULT_SITE_IGNORE = "node_modules\n.tmp\n.env\n.env.example\n.git"

BUILD_TIMEOUT_SECONDS = 300
BUILD_POLL_INTERVAL_SECONDS = 3


# ─── Helpers ────────────────────────────────────────────────────────────────

def log(message: str) -> None:
    print(f"[deploy-site] {message}")


def run_cli(cmd: list[str], check: bool = True, capture: bool = False):
    import subprocess
    import shlex

    def redact_echo(parts: list[str]) -> str:
        out = []
        skip = False
        is_client = len(parts) >= 2 and parts[0] == "appwrite" and parts[1] == "client"
        for i, token in enumerate(parts):
            if skip:
                skip = False
                continue
            if is_client and token == "--key" and i + 1 < len(parts):
                out.extend([token, "***"])
                skip = True
                continue
            out.append(token)
        return " ".join(shlex.quote(str(p)) for p in out)

    log("$ " + redact_echo(cmd))
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=capture, text=True)
    if capture and result.stderr:
        for line in result.stderr.strip().splitlines():
            log(f"stderr: {line}")
    if check and result.returncode != 0:
        if capture:
            log(f"stdout: {result.stdout}")
            log(f"stderr: {result.stderr}")
        raise RuntimeError(f"Command failed: {redact_echo(cmd)}")
    return result


def setup_cli() -> None:
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        log(f"Error: missing environment variables: {', '.join(missing)}")
        sys.exit(1)

    run_cli([
        "appwrite", "client",
        "--endpoint", os.environ["APPWRITE_ENDPOINT"],
        "--project-id", os.environ["APPWRITE_PROJECT_ID"],
        "--key", os.environ["APPWRITE_API_KEY"],
    ])


def load_json(path: Path):
    with open(path) as f:
        return json.load(f)


def resolve_code_path(item: dict) -> Path:
    return (APPWRITE_DIR / item["path"]).resolve()


def should_ignore(relative_path: str, patterns: list[str]) -> bool:
    import fnmatch
    rel = relative_path.replace(os.sep, "/")
    parts = rel.split("/")
    for pattern in patterns:
        pattern = pattern.strip()
        if not pattern:
            continue
        if fnmatch.fnmatch(rel, pattern):
            return True
        if any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
    return False


def build_tarball(code_dir: Path, ignore_text: str) -> Path:
    import fnmatch
    patterns = [p.strip() for p in ignore_text.splitlines() if p.strip()]

    fd, tar_path = tempfile.mkstemp(suffix=".tar.gz", prefix=f"{code_dir.name}-")
    os.close(fd)

    with tarfile.open(tar_path, "w:gz") as tar:
        for root, dirs, files in os.walk(code_dir):
            rel_root = os.path.relpath(root, code_dir).replace(os.sep, "/")
            if rel_root == ".":
                rel_root = ""

            dirs[:] = [
                d for d in dirs
                if not should_ignore(
                    (rel_root + "/" + d).lstrip("/"),
                    patterns,
                )
            ]

            for file in files:
                rel_path = (rel_root + "/" + file).lstrip("/")
                if should_ignore(rel_path, patterns):
                    continue
                full_path = Path(root) / file
                tar.add(full_path, arcname=rel_path)

    return Path(tar_path)


def poll_site_status(site_id: str) -> tuple[str, str, str]:
    deadline = time.time() + BUILD_TIMEOUT_SECONDS
    while True:
        result = run_cli([
            "appwrite", "sites", "get",
            "--site-id", site_id,
            "--json",
        ], capture=True)
        data = json.loads(result.stdout)
        latest_id = data.get("latestDeploymentId", "")
        active_id = data.get("deploymentId", "")
        status = data.get("latestDeploymentStatus", "")

        if status in ("ready", "failed"):
            return status, latest_id, active_id

        if time.time() > deadline:
            raise RuntimeError(f"Timed out waiting for site {site_id} deployment")

        log(f"  ... latest deployment status={status}, waiting {BUILD_POLL_INTERVAL_SECONDS}s")
        time.sleep(BUILD_POLL_INTERVAL_SECONDS)


def deploy_site(site: dict) -> None:
    site_id = site["$id"]
    install_cmd = site.get("installCommand", "")
    build_cmd = site.get("buildCommand", "")
    output_dir = site.get("outputDirectory", ".")
    ignore_text = site.get("ignore", DEFAULT_SITE_IGNORE)
    code_dir = resolve_code_path(site)

    if not code_dir.exists():
        log(f"Warning: code directory {code_dir} does not exist; skipping {site_id}")
        return

    log(f"Deploying site {site_id} from {code_dir}...")

    tarball = build_tarball(code_dir, ignore_text)
    try:
        cmd = [
            "appwrite", "sites", "create-deployment",
            "--site-id", site_id,
            "--code", str(tarball),
            "--build-command", build_cmd,
            "--output-directory", output_dir,
            "--activate", "true",
        ]
        if install_cmd:
            cmd.extend(["--install-command", install_cmd])

        run_cli(cmd)

        status, latest_id, active_id = poll_site_status(site_id)
        if status == "failed":
            raise RuntimeError(f"Site {site_id} deployment failed")

        if latest_id and active_id != latest_id:
            log(f"  activating deployment {latest_id}")
            run_cli([
                "appwrite", "sites", "update-site-deployment",
                "--site-id", site_id,
                "--deployment-id", latest_id,
            ])
        else:
            log(f"  deployment {latest_id} active")
    finally:
        tarball.unlink(missing_ok=True)


# ─── Main ───────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deploy Appwrite Sites using API-key auth.")
    parser.add_argument("site_id", nargs="?", help="Specific site ID to deploy")
    parser.add_argument("--all", action="store_true", help="Deploy all sites in appwrite/sites.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_env()
    setup_cli()

    sites = load_json(SITES_JSON)

    if args.all:
        for site in sites:
            deploy_site(site)
    elif args.site_id:
        matches = [s for s in sites if s["$id"] == args.site_id]
        if not matches:
            log(f"Error: site {args.site_id} not found in {SITES_JSON}")
            sys.exit(1)
        deploy_site(matches[0])
    else:
        # Default: deploy the command center site if it exists.
        matches = [s for s in sites if s["$id"] == "ctrader-command-center"]
        if matches:
            deploy_site(matches[0])
        else:
            log("Error: no site ID provided and ctrader-command-center not found")
            log("Usage: ./dev.sh deploy-site [site-id] | ./dev.sh deploy-site --all")
            sys.exit(1)

    log("Site deployment complete.")
    print("")
    print("Custom domains:")
    for sid, domain in SITE_DOMAINS.items():
        print(f"  {domain:<20} -> {sid}")


if __name__ == "__main__":
    main()
