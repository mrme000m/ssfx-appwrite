#!/usr/bin/env python3
"""
dev/scripts/ctrader_direct_auth.py — Programmatic cTrader OAuth for test accounts.

Bypasses the browser-based OAuth consent flow by:
1. Web-login to cTrader ID portal (CSRF extraction + form POST)
2. Visiting the OAuth grant URL with authenticated session
3. Following auto-redirects / submitting consent automatically
4. Extracting the auth code from the redirect
5. Exchanging code for access/refresh tokens
6. Storing encrypted tokens in Appwrite TablesDB

Usage:
    # Complete OAuth for an existing Appwrite user (creates Appwrite account if missing)
    python3 ctrader_direct_auth.py \
        --username "mrme000.m0" \
        --password "m01790476136M@" \
        --appwrite-user-id "6a49309d00040f56e11f" \
        --env demo

    # Create a new slave user + complete OAuth in one shot
    python3 ctrader_direct_auth.py \
        --username "1mjkaiden" \
        --password "#1Mbugua" \
        --new-slave "demo_user_1mjkaiden" \
        --pin "112358" \
        --env demo

    # Just get tokens into ~/.ctrader/profiles.json (no Appwrite)
    python3 ctrader_direct_auth.py \
        --username "mrme000.m0" \
        --password "m01790476136M@" \
        --profile-only \
        --profile-name "master_demo"

Requires: pip install requests appwrite
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from appwrite.client import Client
from appwrite.id import ID
from appwrite.permission import Permission
from appwrite.query import Query
from appwrite.role import Role
from appwrite.services.tables_db import TablesDB
from appwrite.services.users import Users

# ---------------------------------------------------------------------------
# Config (loaded from Appwrite service_config or env)
# ---------------------------------------------------------------------------
ENDPOINT = os.getenv("APPWRITE_ENDPOINT", "https://sgp.cloud.appwrite.io/v1")
PROJECT_ID = os.getenv("APPWRITE_PROJECT_ID", "6a22a362002b9ae880bb")
API_KEY = os.getenv("APPWRITE_API_KEY", "")
DB_ID = os.getenv("APPWRITE_DATABASE_ID", "slwp_platform")

CTRADER_LOGIN_PAGE = "https://id.ctrader.com"
CTRADER_LOGIN_URL = "https://id.ctrader.com/login"
CTRADER_OAUTH_URL = "https://id.ctrader.com/my/settings/openapi/grantingaccess/"
CTRADER_TOKEN_URL = "https://openapi.ctrader.com/apps/token"
PROFILE_FILE = Path.home() / ".ctrader" / "profiles.json"


def log(msg: str) -> None:
    print(f"[ctrader-auth] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# cTrader Web Login
# ---------------------------------------------------------------------------

def create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:152.0) "
            "Gecko/20100101 Firefox/152.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session


def extract_csrf_token(html: str) -> Optional[str]:
    for pattern in [
        r'content="([^"]+)"\s*name="csrf-token"',
        r'name="csrf-token"\s*content="([^"]+)"',
        r'"_token"\s*content="([^"]+)"',
        r'name="_token"[^>]*value="([^"]+)"',
    ]:
        m = re.search(pattern, html, re.DOTALL)
        if m:
            return m.group(1)
    return None


def ctrader_web_login(session: requests.Session, username: str, password: str) -> dict:
    """Login to cTrader ID and return session cookies or error."""
    # 1. Fetch login page for CSRF
    resp = session.get(CTRADER_LOGIN_PAGE)
    resp.raise_for_status()
    csrf = extract_csrf_token(resp.text)
    if not csrf:
        raise RuntimeError("Could not extract CSRF token from cTrader login page")

    # 2. POST login credentials
    data = {
        "_token": csrf,
        "id": username,
        "password": password,
        "remember": "1",
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": CTRADER_LOGIN_PAGE,
        "Referer": CTRADER_LOGIN_PAGE,
    }
    resp = session.post(CTRADER_LOGIN_URL, data=data, headers=headers, allow_redirects=False)

    # 3. Check result
    if resp.status_code in (302, 303):
        location = resp.headers.get("Location", "")
        if "/login" in location or "error" in location.lower():
            return {"success": False, "reason": f"redirect_to_login ({location})"}
        return {"success": True, "redirect": location}

    if resp.status_code == 200:
        lower = resp.text.lower()
        if any(ind in lower for ind in ("incorrect password", "invalid", "wrong")):
            return {"success": False, "reason": "invalid_credentials"}
        if "overview" in resp.text:
            return {"success": True, "redirect": "overview"}

    return {"success": False, "reason": f"status_{resp.status_code}"}


# ---------------------------------------------------------------------------
# OAuth Consent Automation
# ---------------------------------------------------------------------------

def request_oauth_grant(
    session: requests.Session,
    client_id: str,
    redirect_uri: str,
    state: str = "direct_auth",
    scope: str = "trading",
) -> dict:
    """
    Request cTrader OAuth grant. Defaults to 'trading' for full account + trading access.
    If the app is pre-approved, cTrader auto-redirects with a code.
    Otherwise we need to submit the consent form.
    """
    url = (
        f"{CTRADER_OAUTH_URL}"
        f"?client_id={requests.utils.quote(client_id, safe='')}&"
        f"redirect_uri={requests.utils.quote(redirect_uri, safe='')}&"
        f"scope={scope}&product=web&state={state}"
    )

    # Follow redirects up to the redirect_uri (which is external and will stop)
    # Actually we want to capture the redirect to redirect_uri, so don't follow all
    resp = session.get(url, allow_redirects=False)

    # Case A: Auto-redirect (app already approved)
    if resp.status_code in (302, 303):
        location = resp.headers.get("Location", "")
        parsed = urlparse(location)
        if redirect_uri.rstrip("/") in location or parsed.netloc:
            params = parse_qs(parsed.query)
            code = params.get("code", [None])[0]
            if code:
                return {"success": True, "code": code, "method": "auto_redirect"}
            # Check for error
            error = params.get("error", [None])[0]
            if error:
                return {"success": False, "reason": f"oauth_error: {error}"}

    # Case B: Consent page — need to extract form action and submit
    if resp.status_code == 200:
        html = resp.text
        # Look for the grant/approve form
        form_match = re.search(
            r'<form[^>]*action="([^"]*)"[^>]*>.*?<input[^>]*type="submit"[^>]*>',
            html,
            re.DOTALL | re.IGNORECASE,
        )
        if form_match:
            action_url = form_match.group(1)
            if action_url.startswith("/"):
                action_url = f"https://id.ctrader.com{action_url}"

            # Extract all hidden inputs
            inputs = {}
            for m in re.finditer(
                r'<input[^>]*type="hidden"[^>]*name="([^"]*)"[^>]*value="([^"]*)"[^>]*>',
                html,
                re.IGNORECASE,
            ):
                inputs[m.group(1)] = m.group(2)

            # Submit the approval
            approve_resp = session.post(action_url, data=inputs, allow_redirects=False)
            if approve_resp.status_code in (302, 303):
                location = approve_resp.headers.get("Location", "")
                parsed = urlparse(location)
                params = parse_qs(parsed.query)
                code = params.get("code", [None])[0]
                if code:
                    return {"success": True, "code": code, "method": "consent_submit"}
                error = params.get("error", [None])[0]
                if error:
                    return {"success": False, "reason": f"oauth_error_after_consent: {error}"}

        # No form found — maybe already on redirect page or error
        if "access denied" in html.lower():
            return {"success": False, "reason": "access_denied"}
        if "already granted" in html.lower() or "already authorized" in html.lower():
            # Try to find a link or redirect
            link_match = re.search(r'href="([^"]*code=[^"]*)"', html)
            if link_match:
                parsed = urlparse(link_match.group(1))
                params = parse_qs(parsed.query)
                code = params.get("code", [None])[0]
                if code:
                    return {"success": True, "code": code, "method": "link_extract"}

    return {"success": False, "reason": f"unexpected_status_{resp.status_code}"}


def exchange_code_for_tokens(
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict:
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    resp = requests.post(
        CTRADER_TOKEN_URL,
        data=payload,
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    # Normalize cTrader's sometimes-camelCase keys
    return {
        "access_token": data.get("accessToken") or data.get("access_token"),
        "refresh_token": data.get("refreshToken") or data.get("refresh_token"),
        "expires_in": data.get("expiresIn") or data.get("expires_in", 3600),
        "token_type": data.get("tokenType") or data.get("token_type", "Bearer"),
    }


# ---------------------------------------------------------------------------
# Appwrite Integration
# ---------------------------------------------------------------------------

def build_appwrite_clients() -> tuple[Users, TablesDB]:
    if not API_KEY:
        raise RuntimeError("APPWRITE_API_KEY not set in environment")
    client = Client()
    client.set_endpoint(ENDPOINT)
    client.set_project(PROJECT_ID)
    client.set_key(API_KEY)
    return Users(client), TablesDB(client)


def hash_pin(pin: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.scrypt(pin.encode(), salt=salt.encode("utf-8"), n=2**14, r=8, p=1, dklen=64)
    return f"{salt}:{h.hex()}"


def find_user_by_grant_id(db: TablesDB, grant_id: str) -> Optional[dict]:
    try:
        result = db.list_rows(
            database_id=DB_ID,
            table_id="users",
            queries=[Query.equal("grant_id", grant_id)],
        )
        rows = list(getattr(result, "rows", getattr(result, "documents", [])))
        if rows:
            return rows[0].to_dict().get("data", rows[0].to_dict())
    except Exception as exc:
        log(f"find_user warning: {exc}")
    return None


def create_or_update_user_tokens(
    db: TablesDB,
    appwrite_user_id: str,
    grant_id: str,
    access_token: str,
    refresh_token: str,
    expires_at: str,
    username: Optional[str] = None,
    pin_hash: Optional[str] = None,
) -> None:
    """Store OAuth tokens in the Appwrite users table."""
    try:
        result = db.list_rows(
            database_id=DB_ID,
            table_id="users",
            queries=[Query.equal("appwrite_user_id", appwrite_user_id)],
        )
        rows = list(getattr(result, "rows", getattr(result, "documents", [])))

        if rows:
            row_id = rows[0].to_dict().get("$id")
            update_data = {
                "grant_id": grant_id,
                "access_token_enc": access_token,
                "refresh_token_enc": refresh_token,
                "access_token_expires_at": expires_at,
                "status": "active",
            }
            if username:
                update_data["username"] = username
            if pin_hash:
                update_data["pin_hash"] = pin_hash

            db.update_row(database_id=DB_ID, table_id="users", row_id=row_id, data=update_data)
            log(f"Updated tokens for user {appwrite_user_id}")
        else:
            # Create new row
            body = {
                "appwrite_user_id": appwrite_user_id,
                "username": username or "",
                "pin_hash": pin_hash or "",
                "role": "slave",
                "grant_id": grant_id,
                "access_token_enc": access_token,
                "refresh_token_enc": refresh_token,
                "access_token_expires_at": expires_at,
                "ctrader_account_ids": "",
                "selected_account_id": "",
                "status": "active",
                "active": True,
                "email": "",
                "last_heartbeat_at": None,
            }
            db.create_row(
                database_id=DB_ID,
                table_id="users",
                row_id=ID.unique(),
                data=body,
                permissions=[
                    Permission.read(Role.user(appwrite_user_id)),
                    Permission.update(Role.user(appwrite_user_id)),
                    Permission.read(Role.users()),
                ],
            )
            log(f"Created users row for {appwrite_user_id}")
    except Exception as exc:
        raise RuntimeError(f"Failed to store tokens in Appwrite: {exc}") from exc


def create_appwrite_user(users: Users, email: str, name: str) -> str:
    temp_pass = secrets.token_urlsafe(32)
    user = users.create_argon2_user(user_id=ID.unique(), email=email, password=temp_pass, name=name)
    return getattr(user, "id", getattr(user, "$id", None))


# ---------------------------------------------------------------------------
# Profile persistence (local ~/.ctrader)
# ---------------------------------------------------------------------------

def save_local_profile(
    name: str,
    username: str,
    password: str,
    tokens: dict,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    live: bool = False,
) -> None:
    PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    profiles = {}
    if PROFILE_FILE.exists():
        profiles = json.loads(PROFILE_FILE.read_text())

    profiles[name] = {
        "username": username,
        "password": password,
        "client_id": client_id,
        "client_secret": client_secret,
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "redirect_uri": redirect_uri,
        "live": live,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp = PROFILE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(profiles, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(PROFILE_FILE)
    log(f"Saved local profile '{name}' to {PROFILE_FILE}")


# ---------------------------------------------------------------------------
# Main Orchestrator
# ---------------------------------------------------------------------------

def run_direct_auth(
    username: str,
    password: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    appwrite_user_id: Optional[str] = None,
    new_slave_name: Optional[str] = None,
    pin: Optional[str] = None,
    live: bool = False,
    profile_only: bool = False,
    profile_name: Optional[str] = None,
) -> dict:
    """
    Complete the full programmatic OAuth flow:
    web login → OAuth consent → code extraction → token exchange → Appwrite storage.
    """
    session = create_session()

    # 1. Web login
    log(f"Logging in as {username}...")
    login_result = ctrader_web_login(session, username, password)
    if not login_result["success"]:
        raise RuntimeError(f"cTrader web login failed: {login_result.get('reason')}")
    log("Web login succeeded")

    # 2. Request OAuth grant
    log("Requesting OAuth grant...")
    state = secrets.token_urlsafe(16)
    grant_result = request_oauth_grant(session, client_id, redirect_uri, state=state)
    if not grant_result["success"]:
        raise RuntimeError(f"OAuth grant failed: {grant_result.get('reason')}")
    log(f"OAuth grant succeeded (method: {grant_result['method']})")

    # 3. Exchange code for tokens
    code = grant_result["code"]
    log("Exchanging code for tokens...")
    tokens = exchange_code_for_tokens(code, client_id, client_secret, redirect_uri)
    log(f"Got access_token ({tokens['access_token'][:20]}...)")

    expires_at = datetime.now(timezone.utc).isoformat()
    grant_id = secrets.token_hex(16)

    # 4. Profile-only mode (no Appwrite)
    if profile_only:
        name = profile_name or f"{username}_{'live' if live else 'demo'}"
        save_local_profile(
            name, username, password, tokens,
            client_id, client_secret, redirect_uri, live,
        )
        return {"mode": "profile", "profile": name, "tokens": tokens}

    # 5. Appwrite mode
    users, db = build_appwrite_clients()

    # Create new Appwrite user if requested
    if new_slave_name and not appwrite_user_id:
        email = f"{new_slave_name}@local.slwp"
        appwrite_user_id = create_appwrite_user(users, email, new_slave_name)
        log(f"Created Appwrite user: {appwrite_user_id}")

    if not appwrite_user_id:
        raise RuntimeError("Either --appwrite-user-id or --new-slave must be provided")

    pin_hash = hash_pin(pin) if pin else None
    username_for_db = new_slave_name or username

    create_or_update_user_tokens(
        db,
        appwrite_user_id,
        grant_id,
        tokens["access_token"],
        tokens["refresh_token"],
        expires_at,
        username=username_for_db,
        pin_hash=pin_hash,
    )

    return {
        "mode": "appwrite",
        "appwrite_user_id": appwrite_user_id,
        "grant_id": grant_id,
        "username": username_for_db,
        "tokens": tokens,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Programmatic cTrader OAuth for testing")
    parser.add_argument("--username", required=True, help="cTrader ID username/email")
    parser.add_argument("--password", required=True, help="cTrader ID password")
    parser.add_argument("--client-id", default=os.getenv("CTRADER_CLIENT_ID", ""), help="OAuth client ID")
    parser.add_argument("--client-secret", default=os.getenv("CTRADER_CLIENT_SECRET", ""), help="OAuth client secret")
    parser.add_argument("--redirect-uri", default="https://auth.mrme.tech/callback", help="OAuth redirect URI")
    parser.add_argument("--appwrite-user-id", help="Existing Appwrite user ID to link tokens to")
    parser.add_argument("--new-slave", help="Create a new slave user with this username")
    parser.add_argument("--pin", default="112358", help="PIN for new slave user")
    parser.add_argument("--env", choices=["demo", "live"], default="demo", help="cTrader environment")
    parser.add_argument("--profile-only", action="store_true", help="Only save to local profile, skip Appwrite")
    parser.add_argument("--profile-name", help="Name for local profile")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without executing")
    args = parser.parse_args()

    if not args.client_id or not args.client_secret:
        parser.error("--client-id and --client-secret required (or set CTRADER_CLIENT_ID / CTRADER_CLIENT_SECRET)")

    if args.dry_run:
        print("[dry-run] Would perform:")
        print(f"  1. Web login to cTrader ID as {args.username}")
        print(f"  2. Request OAuth grant for client_id={args.client_id[:20]}...")
        print(f"  3. Exchange auth code for tokens")
        if args.profile_only:
            print(f"  4. Save to local profile '{args.profile_name or args.username}'")
        else:
            target = args.appwrite_user_id or f"new slave '{args.new_slave}'"
            print(f"  4. Store tokens in Appwrite for user: {target}")
        return 0

    try:
        result = run_direct_auth(
            username=args.username,
            password=args.password,
            client_id=args.client_id,
            client_secret=args.client_secret,
            redirect_uri=args.redirect_uri,
            appwrite_user_id=args.appwrite_user_id,
            new_slave_name=args.new_slave,
            pin=args.pin,
            live=(args.env == "live"),
            profile_only=args.profile_only,
            profile_name=args.profile_name,
        )

        print("\n" + "=" * 60)
        print("cTrader Direct Auth — SUCCESS")
        print("=" * 60)
        print(f"\nMode:         {result['mode']}")
        if result['mode'] == 'appwrite':
            print(f"Appwrite UID: {result['appwrite_user_id']}")
            print(f"Grant ID:     {result['grant_id']}")
            print(f"Username:     {result['username']}")
        else:
            print(f"Profile:      {result['profile']}")
        print(f"Access Token: {result['tokens']['access_token'][:50]}...")
        print(f"Expires In:   {result['tokens']['expires_in']}s")
        print(f"Environment:  {'live' if args.env == 'live' else 'demo'}")
        print("=" * 60)
        return 0

    except Exception as exc:
        print(f"\n[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
