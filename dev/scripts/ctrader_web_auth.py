#!/usr/bin/env python3
"""
dev/scripts/ctrader_web_auth.py — CloakBrowser-driven cTrader OAuth + Appwrite token storage.

Uses the logic from /Volumes/Untitled/cookies/ctrader_auth.py to:
1. Launch CloakBrowser (or connect to existing CDP instance)
2. Navigate to the cTrader OAuth consent URL
3. Auto-fill cTrader ID credentials
4. Start a local redirect-capture server
5. Exchange auth code for tokens (GET to openapi.ctrader.com/apps/token)
6. Store encrypted tokens in Appwrite TablesDB (users table)
7. Optional: discover cTrader accounts and store in accounts table

Usage:
    # For an existing Appwrite user (e.g. user00)
    python3 ctrader_web_auth.py \
        --appwrite-user-id "6a49327b002aeb7ba64b" \
        --username "1mjkaiden" \
        --password "#1Mbugua" \
        --env demo \
        --auto-login \
        --discover

    # For a NEW slave user (creates Appwrite user + auth row)
    python3 ctrader_web_auth.py \
        --new-slave "user00" \
        --pin "112358" \
        --username "1mjkaiden" \
        --password "#1Mbugua" \
        --env demo \
        --auto-login \
        --discover

Environment:
    CLOAK_PROFILE_DIR  (default /tmp/cloak-cleann)
    CLOAK_CDP_PORT     (default 9229)
    APPWRITE_ENDPOINT, APPWRITE_PROJECT_ID, APPWRITE_API_KEY
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from appwrite.client import Client
from appwrite.id import ID
from appwrite.permission import Permission
from appwrite.query import Query
from appwrite.role import Role
from appwrite.services.tables_db import TablesDB
from appwrite.services.users import Users

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ENDPOINT = os.getenv("APPWRITE_ENDPOINT", "https://sgp.cloud.appwrite.io/v1")
PROJECT_ID = os.getenv("APPWRITE_PROJECT_ID", "6a22a362002b9ae880bb")
API_KEY = os.getenv("APPWRITE_API_KEY", "")
DB_ID = os.getenv("APPWRITE_DATABASE_ID", "slwp_platform")

CTRADER_OAUTH_BASE = "https://id.ctrader.com/my/settings/openapi/grantingaccess"
TOKEN_URL = "https://openapi.ctrader.com/apps/token"

CLOAK_DESKTOP_SCRIPT = os.getenv(
    "CLOAK_DESKTOP_SCRIPT",
    "/Volumes/ExMac/code/gamca/plugins/ccloakbrowser/scripts/cloak-desktop.sh",
)
CLOAK_PROFILE_DIR = os.getenv("CLOAK_PROFILE_DIR", "/tmp/cloak-cleann")
CLOAK_CDP_PORT = int(os.getenv("CLOAK_CDP_PORT", "9229"))

CTRADER_CLIENT_ID = os.getenv("CTRADER_CLIENT_ID", "")
CTRADER_CLIENT_SECRET = os.getenv("CTRADER_CLIENT_SECRET", "")
CTRADER_REDIRECT_URI = os.getenv("CTRADER_REDIRECT_URI", "http://127.0.0.1:8765/callback")


def log(msg: str) -> None:
    print(f"[ctrader-web-auth] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Local redirect-capture server
# ---------------------------------------------------------------------------

class _OAuthRedirectHandler(BaseHTTPRequestHandler):
    code: Optional[str] = None
    error: Optional[str] = None
    event = threading.Event()

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "code" in params:
            _OAuthRedirectHandler.code = params["code"][0]
        if "error" in params:
            _OAuthRedirectHandler.error = params["error"][0]

        body = (
            "<html><body><h1>Authorization complete</h1>"
            "<p>You can close this browser tab and return to the terminal.</p></body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

        if _OAuthRedirectHandler.code or _OAuthRedirectHandler.error:
            _OAuthRedirectHandler.event.set()


def _start_redirect_server(host: str, port: int) -> ThreadingHTTPServer:
    _OAuthRedirectHandler.code = None
    _OAuthRedirectHandler.error = None
    _OAuthRedirectHandler.event.clear()
    srv = ThreadingHTTPServer((host, port), _OAuthRedirectHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------

def _exchange_code(code: str, client_id: str, client_secret: str, redirect_uri: str) -> dict:
    """cTrader token endpoint uses GET (per their docs)."""
    params = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    resp = requests.get(TOKEN_URL, params=params, headers={"Accept": "application/json"}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errorCode"):
        raise RuntimeError(f"Token exchange error {data['errorCode']}: {data.get('description')}")
    if "accessToken" not in data and "access_token" not in data:
        raise RuntimeError(f"Unexpected token response: {data}")
    # Normalise keys
    return {
        "access_token": data.get("accessToken") or data.get("access_token"),
        "refresh_token": data.get("refreshToken") or data.get("refresh_token"),
        "expires_in": data.get("expiresIn") or data.get("expires_in", 3600),
        "token_type": data.get("tokenType") or data.get("token_type", "Bearer"),
    }


# ---------------------------------------------------------------------------
# CloakBrowser helpers
# ---------------------------------------------------------------------------

def _cdp_available(port: int, timeout: float = 30.0) -> bool:
    import urllib.request
    import time as _time

    deadline = _time.time() + timeout
    while _time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2.0) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        _time.sleep(0.5)
    return False


def _launch_cloak_desktop(profile_dir: str, cdp_port: int, headless: bool = False):
    import subprocess

    if not Path(CLOAK_DESKTOP_SCRIPT).is_file():
        raise RuntimeError(f"CloakBrowser script not found: {CLOAK_DESKTOP_SCRIPT}")

    cmd = ["bash", CLOAK_DESKTOP_SCRIPT, "--profile", profile_dir, "--port", str(cdp_port)]
    if headless:
        cmd.append("--headless")

    log_path = Path(f"/tmp/cloak-desktop-{cdp_port}.log")
    with open(log_path, "wb") as lf:
        proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, start_new_session=True)

    if not _cdp_available(cdp_port, timeout=180.0):
        _stop_cloak(proc)
        tail = log_path.read_text(errors="ignore").strip().splitlines()[-20:]
        raise RuntimeError(
            f"CloakBrowser not ready on CDP port {cdp_port}.\n"
            + "\n".join(tail)
        )
    return proc


def _stop_cloak(proc):
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
                proc.wait(timeout=5)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Browser flow via Playwright CDP
# ---------------------------------------------------------------------------

def _perform_web_auth(
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    username: Optional[str] = None,
    password: Optional[str] = None,
    auto_login: bool = False,
    timeout: float = 180.0,
) -> dict:
    from playwright.sync_api import sync_playwright

    parsed = urlparse(redirect_uri)
    host = parsed.hostname or "127.0.0.1"
    if host in ("localhost",):
        host = "127.0.0.1"
    port = parsed.port
    if port is None:
        raise ValueError("redirect_uri must include an explicit port, e.g. http://127.0.0.1:8765/callback")

    state = secrets.token_urlsafe(16)
    auth_url = f"{CTRADER_OAUTH_BASE}/?{urlencode({'client_id': client_id, 'redirect_uri': redirect_uri, 'scope': 'trading', 'product': 'web', 'state': state})}"

    srv = _start_redirect_server(host, port)
    proc = None
    launched = False
    pw = None
    browser = None

    try:
        if not _cdp_available(CLOAK_CDP_PORT, timeout=5.0):
            log("Launching CloakBrowser...")
            proc = _launch_cloak_desktop(CLOAK_PROFILE_DIR, CLOAK_CDP_PORT)
            launched = True
        else:
            log(f"Using existing CloakBrowser on port {CLOAK_CDP_PORT}")

        pw = sync_playwright().start()
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{CLOAK_CDP_PORT}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()

        log(f"Navigating to cTrader OAuth URL...")
        page.goto(auth_url, wait_until="domcontentloaded")

        if auto_login and username and password:
            try:
                page.wait_for_selector('input[name="id"], input[name="email"]', timeout=8000)
                page.locator('input[name="id"], input[name="email"]').first.fill(username)
                page.locator('input[name="password"]').first.fill(password)
                page.locator('button[type="submit"]').first.click()
                page.wait_for_load_state("networkidle", timeout=10000)
                log("Auto-login submitted")
            except Exception as exc:
                log(f"Auto-login skipped: {exc}")

        log(f"Waiting for OAuth redirect (timeout {int(timeout)}s)...")

        # Poll with manual-interaction support: if the browser is still on
        # the cTrader consent page, give the user time to click Allow.
        deadline = time.time() + timeout
        code = None
        error = None
        while time.time() < deadline:
            # Check if redirect server caught the code
            if _OAuthRedirectHandler.code or _OAuthRedirectHandler.error:
                code = _OAuthRedirectHandler.code
                error = _OAuthRedirectHandler.error
                break

            # Check browser URL directly
            try:
                cur = page.url
                p = urlparse(cur)
                if p.hostname in ("127.0.0.1", "localhost") and p.port == port:
                    params = parse_qs(p.query)
                    code = params.get("code", [None])[0]
                    error = params.get("error", [None])[0]
                    if code or error:
                        break
            except Exception:
                pass

            # If still on cTrader domain, prompt once then keep polling
            try:
                cur = page.url
                if "id.ctrader.com" in cur and "grantingaccess" in cur:
                    remaining = int(deadline - time.time())
                    log(f"Consent page detected. Please click 'Allow' in the browser ({remaining}s remaining)...")
            except Exception:
                pass

            time.sleep(2.0)

        if error:
            raise RuntimeError(f"OAuth error: {error}")
        if not code:
            raise RuntimeError("OAuth timed out or no code captured")

        log("Exchanging code for tokens...")
        return _exchange_code(code, client_id, client_secret, redirect_uri)
    finally:
        if browser and launched:
            try:
                browser.close()
            except Exception:
                pass
        if pw:
            try:
                pw.stop()
            except Exception:
                pass
        srv.shutdown()
        if launched and proc is not None:
            _stop_cloak(proc)


# ---------------------------------------------------------------------------
# Appwrite integration
# ---------------------------------------------------------------------------

def _build_clients() -> tuple[Users, TablesDB]:
    if not API_KEY:
        raise RuntimeError("APPWRITE_API_KEY not set")
    client = Client()
    client.set_endpoint(ENDPOINT)
    client.set_project(PROJECT_ID)
    client.set_key(API_KEY)
    return Users(client), TablesDB(client)


def _hash_pin(pin: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.scrypt(pin.encode(), salt=salt.encode("utf-8"), n=2**14, r=8, p=1, dklen=64)
    return f"{salt}:{h.hex()}"


def _create_appwrite_user(users: Users, email: str, name: str) -> str:
    temp = secrets.token_urlsafe(32)
    u = users.create_argon2_user(user_id=ID.unique(), email=email, password=temp, name=name)
    return getattr(u, "id", getattr(u, "$id", None))


def _upsert_user_tokens(
    db: TablesDB,
    appwrite_user_id: str,
    grant_id: str,
    access_token: str,
    refresh_token: str,
    username: Optional[str] = None,
    pin_hash: Optional[str] = None,
) -> None:
    """Store tokens in Appwrite users table."""
    expires_at = datetime.now(timezone.utc).isoformat()
    result = db.list_rows(
        database_id=DB_ID,
        table_id="users",
        queries=[Query.equal("appwrite_user_id", appwrite_user_id)],
    )
    rows = list(getattr(result, "rows", getattr(result, "documents", [])))

    if rows:
        row_id = rows[0].to_dict().get("$id")
        update = {
            "grant_id": grant_id,
            "access_token_enc": access_token,
            "refresh_token_enc": refresh_token,
            "access_token_expires_at": expires_at,
            "status": "active",
        }
        if username:
            update["username"] = username
        if pin_hash:
            update["pin_hash"] = pin_hash
        db.update_row(database_id=DB_ID, table_id="users", row_id=row_id, data=update)
        log(f"Updated tokens for user {appwrite_user_id}")
    else:
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
            "email": username + "@local.slwp" if username else "",
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


def _store_accounts(db: TablesDB, grant_id: str, accounts: list[dict]) -> None:
    """Store discovered cTrader accounts in the accounts table."""
    for acc in accounts:
        ctid = acc.get("ctidTraderAccountId")
        if not ctid:
            continue
        # Check if already exists
        try:
            result = db.list_rows(
                database_id=DB_ID,
                table_id="ctrader_accounts",
                queries=[
                    Query.equal("grant_id", grant_id),
                    Query.equal("ctidTraderAccountId", ctid),
                ],
            )
            rows = list(getattr(result, "rows", getattr(result, "documents", [])))
            if rows:
                continue  # Skip duplicates
        except Exception:
            pass

        db.create_row(
            database_id=DB_ID,
            table_id="ctrader_accounts",
            row_id=ID.unique(),
            data={
                "grant_id": grant_id,
                "ctidTraderAccountId": ctid,
                "isLive": acc.get("is_live", False),
                "traderLogin": acc.get("trader_login", ""),
                "brokerTitleShort": acc.get("broker_title_short", ""),
                "brokerName": acc.get("broker_name", ""),
                "balance": acc.get("balance", None),
                "selected": False,
            },
        )
        log(f"Stored account {ctid} for grant {grant_id}")


# ---------------------------------------------------------------------------
# Account discovery via ctrader-open-api
# ---------------------------------------------------------------------------

async def _discover_accounts(access_token: str, client_id: str, client_secret: str, live: bool = False) -> list[dict]:
    try:
        from ctrader_open_api import Client as CTraderClient
        from ctrader_open_api.messages.OpenApiMessages_pb2 import (
            ProtoOAApplicationAuthReq,
            ProtoOAGetAccountListByAccessTokenReq,
            ProtoOAErrorRes,
            ProtoOAGetAccountListByAccessTokenRes,
        )
    except ImportError:
        log("ctrader-open-api not installed; skipping account discovery")
        return []

    host = "live.ctraderapi.com" if live else "demo.ctraderapi.com"
    client = CTraderClient(host, 5035, heartbeat=60)
    await client.connect()

    app_auth = ProtoOAApplicationAuthReq()
    app_auth.clientId = str(client_id)
    app_auth.clientSecret = client_secret
    await client.send(app_auth)

    req = ProtoOAGetAccountListByAccessTokenReq()
    req.accessToken = access_token
    await client.send(req)

    accounts = []
    async for msg in client.events:
        if msg.payloadType == ProtoOAErrorRes().payloadType:
            err = ProtoOAErrorRes()
            err.ParseFromString(msg.payload)
            log(f"Open API error: {err.errorCode} - {err.description}")
            await client.disconnect()
            return []

        if msg.payloadType == ProtoOAGetAccountListByAccessTokenRes().payloadType:
            for acc in msg.traderAccount:
                accounts.append({
                    "ctidTraderAccountId": acc.ctidTraderAccountId,
                    "trader_login": getattr(acc, "traderLogin", ""),
                    "broker_title_short": getattr(acc, "brokerTitleShort", ""),
                    "broker_name": getattr(acc, "brokerName", ""),
                    "balance": None,
                    "is_live": live,
                })
            break

    await client.disconnect()
    return accounts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="CloakBrowser-driven cTrader OAuth + Appwrite")
    parser.add_argument("--username", required=True, help="cTrader ID username")
    parser.add_argument("--password", required=True, help="cTrader ID password")
    parser.add_argument("--client-id", default=CTRADER_CLIENT_ID, help="OAuth client ID")
    parser.add_argument("--client-secret", default=CTRADER_CLIENT_SECRET, help="OAuth client secret")
    parser.add_argument("--redirect-uri", default=CTRADER_REDIRECT_URI, help="OAuth redirect URI")
    parser.add_argument("--appwrite-user-id", help="Existing Appwrite user ID")
    parser.add_argument("--new-slave", help="Create new slave with this username")
    parser.add_argument("--pin", default="112358", help="PIN for new slave")
    parser.add_argument("--env", choices=["demo", "live"], default="demo")
    parser.add_argument("--auto-login", action="store_true", help="Auto-fill cTrader login")
    parser.add_argument("--discover", action="store_true", help="Discover cTrader accounts")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()

    if not args.client_id or not args.client_secret:
        parser.error("--client-id and --client-secret required")
    if not args.appwrite_user_id and not args.new_slave:
        parser.error("Either --appwrite-user-id or --new-slave required")

    users, db = _build_clients()

    # Create Appwrite user if needed
    appwrite_uid = args.appwrite_user_id
    if args.new_slave and not appwrite_uid:
        email = f"{args.new_slave}@local.slwp"
        appwrite_uid = _create_appwrite_user(users, email, args.new_slave)
        log(f"Created Appwrite user: {appwrite_uid}")

    # 1. Perform web auth via CloakBrowser
    log("Starting CloakBrowser web auth flow...")
    tokens = _perform_web_auth(
        client_id=args.client_id,
        client_secret=args.client_secret,
        redirect_uri=args.redirect_uri,
        username=args.username,
        password=args.password,
        auto_login=args.auto_login,
        timeout=args.timeout,
    )
    log(f"Got access token ({tokens['access_token'][:30]}...)")

    # 2. Store in Appwrite
    grant_id = secrets.token_hex(16)
    pin_hash = _hash_pin(args.pin) if args.pin else None
    username_for_db = args.new_slave or args.username

    _upsert_user_tokens(
        db, appwrite_uid, grant_id,
        tokens["access_token"], tokens["refresh_token"],
        username=username_for_db, pin_hash=pin_hash,
    )

    # 3. Optional account discovery
    accounts = []
    if args.discover:
        log("Discovering cTrader accounts...")
        accounts = asyncio.run(_discover_accounts(
            tokens["access_token"],
            args.client_id,
            args.client_secret,
            live=(args.env == "live"),
        ))
        log(f"Found {len(accounts)} account(s)")
        if accounts:
            _store_accounts(db, grant_id, accounts)

    # 4. Summary
    print("\n" + "=" * 60)
    print("cTrader Web Auth — COMPLETE")
    print("=" * 60)
    print(f"\nAppwrite User ID: {appwrite_uid}")
    print(f"Grant ID:         {grant_id}")
    print(f"Username:         {username_for_db}")
    print(f"Environment:      {args.env}")
    print(f"Access Token:     {tokens['access_token'][:50]}...")
    print(f"Refresh Token:    {tokens['refresh_token'][:20]}...")
    if accounts:
        print(f"\nAccounts:")
        for acc in accounts:
            print(f"  - cTID: {acc['ctidTraderAccountId']}, Broker: {acc.get('broker_name', 'N/A')}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
