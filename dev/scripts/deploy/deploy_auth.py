#!/usr/bin/env python3
"""
dev/scripts/deploy_auth.py — Deploy cTrader auth functions + site + custom domains

Replaces the previous `appwrite push functions` / `appwrite push sites` flow with
API-key-compatible `create-deployment` calls. Variables are upserted explicitly
by variable ID so updates are reliable in both local and CI/CD runs.

Usage:
    ./dev.sh deploy-auth [--no-sync] [--no-lint] [--no-tables] [--no-functions]
                         [--no-site] [--no-domains] [--smoke]

In CI/CD:
    python3 dev/scripts/deploy_auth.py --functions --no-tables --no-site \
        --no-domains --no-smoke --no-sync --no-lint
    python3 dev/scripts/deploy_auth.py --site --no-tables --no-functions \
        --no-domains --no-smoke
"""

import argparse
import fnmatch
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import uuid
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from _config import (
    FUNCTION_DOMAINS,
    SITE_DOMAINS,
    load_env,
)

# ─── Configuration ──────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.resolve()
APPWRITE_DIR = PROJECT_ROOT / "appwrite"
FUNCTIONS_JSON = APPWRITE_DIR / "functions.json"
SITES_JSON = APPWRITE_DIR / "sites.json"

REQUIRED_ENV = ["APPWRITE_ENDPOINT", "APPWRITE_PROJECT_ID", "APPWRITE_API_KEY"]
REQUIRED_SECRETS = [
    "CTRADER_CLIENT_ID",
    "CTRADER_CLIENT_SECRET",
    "TOKEN_ENCRYPTION_KEY",
    "SESSION_HMAC_KEY",
    "INTERNAL_API_KEY",
]

DEFAULT_FUNCTION_IGNORE = "node_modules\n.tmp\n.env"
DEFAULT_SITE_IGNORE = "node_modules\n.tmp\n.env\n.env.example\n.git"

SITE_VARIABLES = {
    "ssfx-hq": {
        # Exposes the ssfx-server admin API key to the SPA build so it can
        # call /api/* endpoints via the x-admin-key header.
        "ADMIN_API_KEY": "{ADMIN_API_KEY}",
    },
}

FUNCTION_VARIABLES = {
    "auth-oauth": {
        "APPWRITE_ENDPOINT": "{APPWRITE_ENDPOINT}",
        "APPWRITE_PROJECT_ID": "{APPWRITE_PROJECT_ID}",
        "APPWRITE_API_KEY": "{APPWRITE_API_KEY}",
        "APPWRITE_DATABASE_ID": "slwp_platform",
        "CTRADER_AUTH_DATABASE_ID": "slwp_platform",
        "CTRADER_ACCOUNTS_TABLE_ID": "ctrader_accounts",
        "CTRADER_CLIENT_ID": "{CTRADER_CLIENT_ID}",
        "CTRADER_CLIENT_SECRET": "{CTRADER_CLIENT_SECRET}",
        "CTRADER_REDIRECT_URI": "https://auth.mrme.tech/callback",
        "CTRADER_SCOPE": "{CTRADER_SCOPE}",
        "TOKEN_ENCRYPTION_KEY": "{TOKEN_ENCRYPTION_KEY}",
        "SESSION_HMAC_KEY": "{SESSION_HMAC_KEY}",
        "SITES_URL": "https://app.mrme.tech",
    },
    "auth-pin": {
        "APPWRITE_ENDPOINT": "{APPWRITE_ENDPOINT}",
        "APPWRITE_PROJECT_ID": "{APPWRITE_PROJECT_ID}",
        "APPWRITE_API_KEY": "{APPWRITE_API_KEY}",
        "APPWRITE_DATABASE_ID": "slwp_platform",
        "CTRADER_AUTH_DATABASE_ID": "slwp_platform",
        "CTRADER_ACCOUNTS_TABLE_ID": "ctrader_accounts",
        "BCRYPT_SALT_ROUNDS": "10",
        "SITES_URL": "https://app.mrme.tech",
        "RESEND_API_KEY": "{RESEND_API_KEY}",
        "RESEND_FROM_EMAIL": "{RESEND_FROM_EMAIL}",
        "PIN_RESET_BASE_URL": "https://app.mrme.tech",
    },
    "api-internal": {
        "APPWRITE_ENDPOINT": "{APPWRITE_ENDPOINT}",
        "APPWRITE_PROJECT_ID": "{APPWRITE_PROJECT_ID}",
        "APPWRITE_API_KEY": "{APPWRITE_API_KEY}",
        "APPWRITE_DATABASE_ID": "slwp_platform",
        "CTRADER_AUTH_DATABASE_ID": "slwp_platform",
        "CTRADER_ACCOUNTS_TABLE_ID": "ctrader_accounts",
        "INTERNAL_API_KEY": "{INTERNAL_API_KEY}",
        "CTRADER_CLIENT_ID": "{CTRADER_CLIENT_ID}",
        "CTRADER_CLIENT_SECRET": "{CTRADER_CLIENT_SECRET}",
        "TOKEN_ENCRYPTION_KEY": "{TOKEN_ENCRYPTION_KEY}",
        "SITES_URL": "https://app.mrme.tech",
    },
    "token-refresh": {
        "APPWRITE_ENDPOINT": "{APPWRITE_ENDPOINT}",
        "APPWRITE_PROJECT_ID": "{APPWRITE_PROJECT_ID}",
        "APPWRITE_API_KEY": "{APPWRITE_API_KEY}",
        "APPWRITE_DATABASE_ID": "slwp_platform",
        "CTRADER_AUTH_DATABASE_ID": "slwp_platform",
        "CTRADER_ACCOUNTS_TABLE_ID": "ctrader_accounts",
        "CTRADER_CLIENT_ID": "{CTRADER_CLIENT_ID}",
        "CTRADER_CLIENT_SECRET": "{CTRADER_CLIENT_SECRET}",
        "TOKEN_ENCRYPTION_KEY": "{TOKEN_ENCRYPTION_KEY}",
        "REFRESH_BUFFER_HOURS": "48",
    },
}

# Secret-ish keys that should be redacted from echoed commands.
REDACT_KEYS = {
    "APPWRITE_API_KEY",
    "CTRADER_CLIENT_SECRET",
    "TOKEN_ENCRYPTION_KEY",
    "SESSION_HMAC_KEY",
    "INTERNAL_API_KEY",
    "RESEND_API_KEY",
    "ADMIN_API_KEY",
    "V2_ADMIN_KEY",
}

# Variables that should be stored as plain (non-secret) in Appwrite.
PLAIN_KEYS = {
    "APPWRITE_ENDPOINT",
    "APPWRITE_PROJECT_ID",
    "APPWRITE_DATABASE_ID",
    "CTRADER_AUTH_DATABASE_ID",
    "CTRADER_ACCOUNTS_TABLE_ID",
    "CTRADER_REDIRECT_URI",
    "SITES_URL",
    "BCRYPT_SALT_ROUNDS",
    "REFRESH_BUFFER_HOURS",
    "RESEND_FROM_EMAIL",
    "PIN_RESET_BASE_URL",
}

# How long to wait for a build before giving up.
BUILD_TIMEOUT_SECONDS = 300
BUILD_POLL_INTERVAL_SECONDS = 3


# ─── Helpers ────────────────────────────────────────────────────────────────

def log(message: str) -> None:
    print(f"[deploy-auth] {message}")


def redact_value(key: str, value: str) -> str:
    if key in REDACT_KEYS or any(k.lower() in key.lower() for k in REDACT_KEYS):
        return "***"
    return value


def echo_command(cmd: list[str]) -> str:
    """Render a command for logging, redacting secrets."""
    parts = []
    skip_next = False
    is_client_cmd = len(cmd) >= 2 and cmd[0] == "appwrite" and cmd[1] == "client"
    for i, token in enumerate(cmd):
        if skip_next:
            skip_next = False
            continue
        # Redact the API key passed to `appwrite client --key`.
        if is_client_cmd and token == "--key" and i + 1 < len(cmd):
            parts.append(token)
            parts.append("***")
            skip_next = True
            continue
        # Heuristic: if the previous token was `--key`, redact the value only if
        # the key itself is sensitive. We keep the key name visible.
        if token == "--value" and i + 1 < len(cmd):
            # Try to find the matching --key; scan backwards.
            key = ""
            for j in range(i - 1, -1, -1):
                if cmd[j] == "--key" and j + 1 < len(cmd):
                    key = cmd[j + 1]
                    break
            parts.append(token)
            parts.append(redact_value(key, cmd[i + 1]))
            skip_next = True
        elif token == "--key" and i + 1 < len(cmd):
            parts.append(token)
            parts.append(cmd[i + 1])
            skip_next = True
        else:
            parts.append(token)
    return " ".join(shlex.quote(p) for p in parts)


def run_cli(cmd: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    """Run an Appwrite CLI command, echoing it (with secrets redacted)."""
    log(f"$ {echo_command(cmd)}")
    result = subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        capture_output=capture,
        text=True,
    )
    if capture:
        # Log stderr if there is any, unless it's just progress noise.
        if result.stderr and "[deploy-auth]" not in result.stderr:
            for line in result.stderr.strip().splitlines():
                log(f"stderr: {line}")
    if check and result.returncode != 0:
        if capture:
            log(f"stdout: {result.stdout}")
            log(f"stderr: {result.stderr}")
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {echo_command(cmd)}")
    return result


def setup_cli() -> None:
    """Configure the Appwrite CLI to use the API key."""
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        log(f"Error: missing environment variables: {', '.join(missing)}")
        log("Add them to .env and rerun.")
        sys.exit(1)

    run_cli([
        "appwrite", "client",
        "--endpoint", os.environ["APPWRITE_ENDPOINT"],
        "--project-id", os.environ["APPWRITE_PROJECT_ID"],
        "--key", os.environ["APPWRITE_API_KEY"],
    ])


def load_json(path: Path) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def resolve_code_path(item: dict) -> Path:
    """Resolve the code directory from the JSON `path` field."""
    return (APPWRITE_DIR / item["path"]).resolve()


def should_ignore(relative_path: str, patterns: list[str]) -> bool:
    """Check whether a relative path matches any ignore pattern."""
    rel = relative_path.replace(os.sep, "/")
    parts = rel.split("/")
    for pattern in patterns:
        pattern = pattern.strip()
        if not pattern:
            continue
        # Match against the full path and against each path component.
        if fnmatch.fnmatch(rel, pattern):
            return True
        if any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
    return False


def build_tarball(code_dir: Path, ignore_text: str) -> Path:
    """Create a filtered .tar.gz of the code directory in a temp location."""
    patterns = [p.strip() for p in ignore_text.splitlines() if p.strip()]

    fd, tar_path = tempfile.mkstemp(suffix=".tar.gz", prefix=f"{code_dir.name}-")
    os.close(fd)

    with tarfile.open(tar_path, "w:gz") as tar:
        for root, dirs, files in os.walk(code_dir):
            rel_root = os.path.relpath(root, code_dir).replace(os.sep, "/")
            if rel_root == ".":
                rel_root = ""

            # Prune ignored directories while walking.
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


# ─── Variables ──────────────────────────────────────────────────────────────

def render_variables(template: dict[str, str]) -> dict[str, str]:
    """Substitute environment placeholders into a variable template."""
    rendered = {}
    for key, value in template.items():
        try:
            rendered[key] = value.format(**os.environ)
        except KeyError as e:
            missing = str(e).strip("'")
            log(f"Error: environment variable {missing} is required for variable {key} but not set")
            log("Add it to .env and rerun.")
            sys.exit(1)
    return rendered


def list_function_variable_metadata(function_id: str) -> dict[str, dict]:
    """Return a mapping variable-key -> metadata dict for a function."""
    result = run_cli([
        "appwrite", "functions", "list-variables",
        "--function-id", function_id,
        "--json",
    ], capture=True)
    data = json.loads(result.stdout)
    variables = data.get("variables", [])
    return {v["key"]: v for v in variables if "key" in v}


def upsert_function_variables(function_id: str, variables: dict[str, str]) -> None:
    """Create or update variables for a function, recreating when secret flag changes."""
    existing_meta = list_function_variable_metadata(function_id)
    existing = {k: v["$id"] for k, v in existing_meta.items()}
    for key, value in variables.items():
        safe_value = redact_value(key, value)
        desired_secret = key not in PLAIN_KEYS
        secret_flag = "true" if desired_secret else "false"

        if key in existing:
            current_secret = existing_meta[key].get("secret", True)
            if current_secret != desired_secret:
                log(f"  recreating variable {key}={safe_value} (secret {current_secret} -> {desired_secret})")
                run_cli([
                    "appwrite", "functions", "delete-variable",
                    "--function-id", function_id,
                    "--variable-id", existing[key],
                ])
                run_cli([
                    "appwrite", "functions", "create-variable",
                    "--function-id", function_id,
                    "--variable-id", uuid.uuid4().hex[:20],
                    "--key", key,
                    "--value", value,
                    "--secret", secret_flag,
                ])
            else:
                log(f"  updating variable {key}={safe_value}")
                run_cli([
                    "appwrite", "functions", "update-variable",
                    "--function-id", function_id,
                    "--variable-id", existing[key],
                    "--key", key,
                    "--value", value,
                    "--secret", secret_flag,
                ])
        else:
            log(f"  creating variable {key}={safe_value}")
            run_cli([
                "appwrite", "functions", "create-variable",
                "--function-id", function_id,
                "--variable-id", uuid.uuid4().hex[:20],
                "--key", key,
                "--value", value,
                "--secret", secret_flag,
            ])


def list_site_variable_metadata(site_id: str) -> dict[str, dict]:
    """Return a mapping variable-key -> metadata dict for a site."""
    result = run_cli([
        "appwrite", "sites", "list-variables",
        "--site-id", site_id,
        "--json",
    ], capture=True)
    data = json.loads(result.stdout)
    variables = data.get("variables", [])
    return {v["key"]: v for v in variables if "key" in v}


def upsert_site_variables(site_id: str, variables: dict[str, str]) -> None:
    """Create or update variables for a site, recreating when secret flag changes."""
    existing_meta = list_site_variable_metadata(site_id)
    existing = {k: v["$id"] for k, v in existing_meta.items()}
    for key, value in variables.items():
        safe_value = redact_value(key, value)
        desired_secret = key not in PLAIN_KEYS
        secret_flag = "true" if desired_secret else "false"

        if key in existing:
            current_secret = existing_meta[key].get("secret", True)
            if current_secret != desired_secret:
                log(f"  recreating site variable {key}={safe_value} (secret {current_secret} -> {desired_secret})")
                run_cli([
                    "appwrite", "sites", "delete-variable",
                    "--site-id", site_id,
                    "--variable-id", existing[key],
                ])
                run_cli([
                    "appwrite", "sites", "create-variable",
                    "--site-id", site_id,
                    "--variable-id", uuid.uuid4().hex[:20],
                    "--key", key,
                    "--value", value,
                    "--secret", secret_flag,
                ])
            else:
                log(f"  updating site variable {key}={safe_value}")
                run_cli([
                    "appwrite", "sites", "update-variable",
                    "--site-id", site_id,
                    "--variable-id", existing[key],
                    "--key", key,
                    "--value", value,
                    "--secret", secret_flag,
                ])
        else:
            log(f"  creating site variable {key}={safe_value}")
            run_cli([
                "appwrite", "sites", "create-variable",
                "--site-id", site_id,
                "--variable-id", uuid.uuid4().hex[:20],
                "--key", key,
                "--value", value,
                "--secret", secret_flag,
            ])


# ─── Deployment polling ─────────────────────────────────────────────────────

def poll_function_status(function_id: str) -> tuple[str, str, str]:
    """Poll until latest deployment is ready/failed. Returns (status, latest_id, active_id)."""
    deadline = time.time() + BUILD_TIMEOUT_SECONDS
    while True:
        result = run_cli([
            "appwrite", "functions", "get",
            "--function-id", function_id,
            "--json",
        ], capture=True)
        data = json.loads(result.stdout)
        latest_id = data.get("latestDeploymentId", "")
        active_id = data.get("deploymentId", "")
        status = data.get("latestDeploymentStatus", "")

        if status in ("ready", "failed"):
            return status, latest_id, active_id

        if time.time() > deadline:
            raise RuntimeError(f"Timed out waiting for function {function_id} deployment")

        log(f"  ... latest deployment status={status}, waiting {BUILD_POLL_INTERVAL_SECONDS}s")
        time.sleep(BUILD_POLL_INTERVAL_SECONDS)


def poll_site_status(site_id: str) -> tuple[str, str, str]:
    """Poll until latest deployment is ready/failed. Returns (status, latest_id, active_id)."""
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


# ─── Function deploy ────────────────────────────────────────────────────────

def deploy_function(fn: dict) -> None:
    function_id = fn["$id"]
    entrypoint = fn.get("entrypoint", "src/main.js")
    commands = fn.get("commands", "npm install")
    ignore_text = fn.get("ignore", DEFAULT_FUNCTION_IGNORE)
    code_dir = resolve_code_path(fn)

    log(f"Deploying function {function_id}...")

    # Variables
    template = FUNCTION_VARIABLES.get(function_id)
    if template is None:
        raise RuntimeError(f"No variable mapping defined for function {function_id}")
    upsert_function_variables(function_id, render_variables(template))

    # Build tarball
    tarball = build_tarball(code_dir, ignore_text)
    try:
        # Create and activate deployment
        run_cli([
            "appwrite", "functions", "create-deployment",
            "--function-id", function_id,
            "--code", str(tarball),
            "--entrypoint", entrypoint,
            "--commands", commands,
            "--activate", "true",
        ])

        # Poll
        status, latest_id, active_id = poll_function_status(function_id)
        if status == "failed":
            raise RuntimeError(f"Function {function_id} deployment failed")

        if latest_id and active_id != latest_id:
            log(f"  activating deployment {latest_id}")
            run_cli([
                "appwrite", "functions", "update-function-deployment",
                "--function-id", function_id,
                "--deployment-id", latest_id,
            ])
        else:
            log(f"  deployment {latest_id} active")
    finally:
        tarball.unlink(missing_ok=True)


# ─── Site deploy ────────────────────────────────────────────────────────────

def deploy_site(site: dict) -> None:
    site_id = site["$id"]
    install_cmd = site.get("installCommand", "")
    build_cmd = site.get("buildCommand", "")
    output_dir = site.get("outputDirectory", ".")
    ignore_text = site.get("ignore", DEFAULT_SITE_IGNORE)
    code_dir = resolve_code_path(site)

    log(f"Deploying site {site_id}...")

    if site_id in SITE_VARIABLES:
        upsert_site_variables(site_id, render_variables(SITE_VARIABLES[site_id]))

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


# ─── Smoke tests ────────────────────────────────────────────────────────────

def smoke_test() -> None:
    log("Running smoke tests...")
    run_cli([
        "curl", "-fsS", "--retry", "5", "--retry-delay", "5",
        "--connect-timeout", "10", "https://auth.mrme.tech/session",
    ])
    run_cli([
        "curl", "-fsS", "--retry", "5", "--retry-delay", "5",
        "--connect-timeout", "10", "https://app.mrme.tech/",
    ])
    log("Smoke tests passed.")


def validate_function_secrets() -> None:
    """Ensure all secrets required for function variables are present."""
    missing = [k for k in REQUIRED_SECRETS if not os.environ.get(k)]
    if missing:
        log(f"Error: missing secrets required for function variables: {', '.join(missing)}")
        log("Add them to .env and rerun.")
        sys.exit(1)


# ─── Main ───────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deploy the cTrader auth layer to Appwrite using API-key auth.",
    )
    parser.add_argument("--no-sync", action="store_true", help="Skip syncing shared module")
    parser.add_argument("--no-lint", action="store_true", help="Skip linting")
    parser.add_argument("--no-tables", action="store_true", help="Skip pushing TablesDB")
    parser.add_argument("--no-functions", action="store_true", help="Skip deploying functions")
    parser.add_argument("--no-site", action="store_true", help="Skip deploying site")
    parser.add_argument("--no-domains", action="store_true", help="Skip domain status check")
    parser.add_argument("--smoke", action="store_true", help="Run smoke tests after deploy")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_env()
    # cTrader OAuth scope defaults to full trading + account access.
    os.environ.setdefault("CTRADER_SCOPE", "trading")
    setup_cli()

    if not args.no_sync:
        log("Syncing shared module into function packages...")
        run_cli([sys.executable, str(PROJECT_ROOT / "dev" / "scripts" / "ops" / "sync_shared.py")])

    if not args.no_lint:
        log("Running lint...")
        run_cli(["bash", str(PROJECT_ROOT / "dev" / "scripts" / "testing" / "lint.sh")])

    if not args.no_tables:
        log("Pushing TablesDB config...")
        run_cli(["appwrite", "push", "tables", "--all", "--force"])

    functions = load_json(FUNCTIONS_JSON)
    sites = load_json(SITES_JSON)

    if not args.no_functions:
        validate_function_secrets()
        for fn in functions:
            deploy_function(fn)

        log("Setting cron schedule on token-refresh...")
        run_cli([
            "appwrite", "functions", "update",
            "--function-id", "token-refresh",
            "--name", "token-refresh",
            "--schedule", "0 3 * * *",
        ])

    if not args.no_site:
        for site in sites:
            deploy_site(site)

    if not args.no_domains:
        log("Checking custom domain status...")
        run_cli([sys.executable, str(PROJECT_ROOT / "dev" / "scripts" / "setup_function_domains.py"), "--status"])

    if args.smoke:
        smoke_test()

    log("Deployment complete.")
    if not args.no_domains:
        print("")
        print("Custom domains:")
        for fid, domain in FUNCTION_DOMAINS.items():
            print(f"  {domain:<20} -> {fid}")
        for sid, domain in SITE_DOMAINS.items():
            print(f"  {domain:<20} -> {sid}")


if __name__ == "__main__":
    main()
