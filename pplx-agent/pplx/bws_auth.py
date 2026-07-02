"""Bitwarden Secrets Manager authentication helpers for PPLX.

Supports two flows:
1. Direct BWS_ACCESS_TOKEN (service account) - used by bitwarden-sdk
2. OAuth client_credentials via client_id + client_secret to obtain user access token

The user's regular client_id/client_secret authenticate to the Bitwarden identity server.
Secrets Manager requires a *service account* access token. If one is not available in
$BWS_ACCESS_TOKEN, the helper can bootstrap it via the Bitwarden REST API (requires org
owner or SM admin privileges).
"""

import json
import os
import uuid

from .config import load_project_env


# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

def _ensure_env():
    load_project_env()


def _client_id():
    _ensure_env()
    return os.getenv("BITWARDEN_CLIENT_ID", "")


def _client_secret():
    _ensure_env()
    return os.getenv("BITWARDEN_CLIENT_SECRET", "")


def _org_id():
    _ensure_env()
    return os.getenv("BITWARDEN_ORG_ID", "")


def _bws_token():
    _ensure_env()
    return os.getenv("BWS_ACCESS_TOKEN", "")


IDENTITY_URL = os.getenv("BITWARDEN_IDENTITY_URL", "https://identity.bitwarden.com/connect/token")
API_URL = os.getenv("BITWARDEN_API_URL", "https://api.bitwarden.com")
BWS_SERVER_URL = os.getenv("BWS_SERVER_URL", "https://vault.bitwarden.com")


# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------

def _http_post(url, headers, data):
    """Minimal POST helper without external deps."""
    try:
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        from urllib.parse import urlencode
    except ImportError:
        raise RuntimeError("urllib is required for BWS auth.")

    encoded = urlencode(data).encode("utf-8")
    req = Request(url, data=encoded, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else ""
        try:
            err_json = json.loads(body)
        except json.JSONDecodeError:
            err_json = {"error": body or str(e)}
        raise RuntimeError(f"HTTP {e.code}: {err_json.get('error_description', err_json.get('error', str(e)))}")


def get_user_access_token(client_id: str = "", client_secret: str = "") -> str:
    """Exchange client_id + client_secret for a Bitwarden user access token.

    This token is scoped to the regular vault API, not Secrets Manager.
    It can be used to create a SM service account via the management API.
    """
    cid = client_id or _client_id()
    csec = client_secret or _client_secret()
    if not cid or not csec:
        raise RuntimeError(
            "BITWARDEN_CLIENT_ID and BITWARDEN_CLIENT_SECRET must be set in .env"
        )

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Bitwarden-Client-Version": "2025.1.0",
        "Bitwarden-Client-Name": "pplx-setup",
        "Device-Type": "99",
    }
    data = {
        "grant_type": "client_credentials",
        "client_id": cid,
        "client_secret": csec,
        "deviceIdentifier": str(uuid.uuid4()),
        "deviceName": "pplx-setup",
        "deviceType": "99",
    }
    resp = _http_post(IDENTITY_URL, headers, data)
    token = resp.get("access_token")
    if not token:
        raise RuntimeError(
            f"Failed to obtain access token: {resp.get('error_description', resp)}"
        )
    return token


def get_sm_service_account_token(
    org_id: str = "",
    client_id: str = "",
    client_secret: str = "",
    token: str = "",
    service_account_name: str = "pplx-sm-sa",
) -> str:
    """Ensure a Secrets Manager service account exists and return its access token.

    Requires org owner / Secrets Manager admin privileges.
    This is a best-effort helper — if it fails, the user should create the
    service account manually in the Bitwarden web UI and export the access token
    to $BWS_ACCESS_TOKEN.

    Returns the access token string on success.
    """
    cid = client_id or _client_id()
    csec = client_secret or _client_secret()
    oid = org_id or _org_id()
    access_token = token or _bws_token()

    if not access_token:
        access_token = get_user_access_token(cid, csec)

    if not oid:
        # Try to extract org ID from the user token JWT payload
        oid = _extract_org_id_from_token(access_token)
        if not oid:
            raise RuntimeError(
                "BITWARDEN_ORG_ID is required to create a SM service account. "
                "Set it in .env or create the service account manually."
            )

    # Use Bitwarden management API to create/list service accounts
    try:
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
    except ImportError:
        raise

    auth_hdr = {"Authorization": f"Bearer {access_token}"}

    def _api_json(url, method="GET", data=None):
        req = Request(
            url,
            data=json.dumps(data).encode("utf-8") if data else None,
            headers={**auth_hdr, "Content-Type": "application/json"} if data else auth_hdr,
            method=method,
        )
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            body = e.read().decode("utf-8") if e.fp else ""
            try:
                err = json.loads(body)
            except json.JSONDecodeError:
                err = {"message": body or f"HTTP {e.code}"}
            raise RuntimeError(
                f"Bitwarden API error ({url}): {err.get('message', err)}"
            )

    # 1. List existing service accounts for org
    try:
        sas = _api_json(f"{API_URL}/organizations/{oid}/service-accounts")
    except RuntimeError:
        raise RuntimeError(
            "Cannot list service accounts via API.\n"
            "Please create a service account manually:\n"
            "1. Open https://vault.bitwarden.com  -> Admin Console -> Secrets Manager\n"
            "2. Create a service account named 'pplx-sm-sa'\n"
            "3. Create an access token and copy it\n"
            "4. Paste it into .env as BWS_ACCESS_TOKEN=..."
        )

    sa = None
    for acc in sas.get("data", []):
        if acc.get("name") == service_account_name:
            sa = acc
            break

    # 2. Create if missing — the SM API requires encrypted fields,
    #    which only the official SDK/web UI can produce.
    if not sa:
        try:
            sa = _api_json(
                f"{API_URL}/organizations/{oid}/service-accounts",
                method="POST",
                data={"name": service_account_name},
            )
        except RuntimeError as e:
            err_text = str(e).lower()
            if "encrypted string" in err_text or "model state is invalid" in err_text:
                _raise_manual_instructions(oid, service_account_name)
            raise

    sa_id = sa["id"]

    # 3. List access tokens for this SA
    try:
        tokens = _api_json(f"{API_URL}/service-accounts/{sa_id}/access-tokens")
    except RuntimeError:
        _raise_manual_instructions(oid, service_account_name)

    for t in tokens.get("data", []):
        if t.get("name") == "pplx-token":
            raise RuntimeError(
                "A BWS access token already exists but cannot be re-read.\n"
                "Either reuse the existing token from your records, or revoke it\n"
                "and re-run this script to generate a new one."
            )

    # 4. Create new access token
    try:
        tok_resp = _api_json(
            f"{API_URL}/service-accounts/{sa_id}/access-tokens",
            method="POST",
            data={"name": "pplx-token"},
        )
    except RuntimeError:
        _raise_manual_instructions(oid, service_account_name)

    bws_token = tok_resp.get("clientSecret")
    if not bws_token:
        raise RuntimeError(f"Unexpected token creation response: {tok_resp}")
    return bws_token


def _raise_manual_instructions(org_id: str, sa_name: str):
    url = (
        f"https://vault.bitwarden.com/#/sm/{org_id}/service-accounts"
    )
    raise RuntimeError(
        f"Bitwarden SM requires encrypted fields that cannot be generated via raw API.\n"
        f"\nPlease create the service account manually:\n"
        f"  1. Open: {url}\n"
        f"  2. Click 'New service account' -> Name: '{sa_name}'\n"
        f"  3. Open the service account -> 'Access tokens' -> 'New access token'\n"
        f"  4. Name it 'pplx-token', copy the token (starts with 0.)\n"
        f"  5. Paste into .env:\n"
        f"       BWS_ACCESS_TOKEN=<the-token-you-copied>\n"
        f"\nThen run:\n"
        f"  python scripts/setup_bws_secret.py --show\n"
    )


def _extract_org_id_from_token(token: str) -> str:
    """Naïve JWT payload extraction (no signature verification)."""
    try:
        import base64
        parts = token.split(".")
        if len(parts) != 3:
            return ""
        pad = 4 - len(parts[1]) % 4
        payload = base64.urlsafe_b64decode(parts[1] + "=" * pad)
        data = json.loads(payload)
        # accesssecretsmanager contains the SM org UUID
        return data.get("accesssecretsmanager", "")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# SDK helpers
# ---------------------------------------------------------------------------

def get_bws_client(access_token: str = ""):
    """Return an authenticated bitwarden_sdk.BitwardenClient.

    Args:
        access_token: BWS service-account access token. Falls back to $BWS_ACCESS_TOKEN.

    Raises:
        RuntimeError: if bitwarden-sdk is not installed or auth fails.
    """
    try:
        from bitwarden_sdk import BitwardenClient, ClientSettings
    except ImportError:
        raise RuntimeError(
            "bitwarden-sdk is not installed. Run: pip install bitwarden-sdk"
        )

    token = access_token or _bws_token()
    if not token:
        raise RuntimeError(
            "BWS_ACCESS_TOKEN is required.\n"
            "Run the setup script: python scripts/setup_bws_secret.py --create-token\n"
            "Or add BWS_ACCESS_TOKEN=<token> to your .env file."
        )

    client = BitwardenClient(settings=ClientSettings())
    result = client.auth().login_access_token(token, BWS_SERVER_URL)
    if result.success:
        return client
    raise RuntimeError(f"BWS login failed: {result}")


def get_org_id_from_env_or_token(access_token: str = "") -> str:
    """Resolve the Bitwarden organization ID for SM operations."""
    oid = os.getenv("BITWARDEN_ORG_ID", "")
    if oid:
        return oid
    tok = access_token or _bws_token()
    if tok:
        extracted = _extract_org_id_from_token(tok)
        if extracted:
            return extracted
    raise RuntimeError(
        "BITWARDEN_ORG_ID is required for project operations. "
        "Set it in .env or ensure BWS_ACCESS_TOKEN contains the org claim."
    )


def list_sm_projects(client=None, access_token: str = "", org_id: str = ""):
    """List all Secrets Manager projects."""
    if client is None:
        client = get_bws_client(access_token)
    oid = org_id or get_org_id_from_env_or_token(access_token)
    resp = client.projects().list(organization_id=oid)
    # SDK returns a ProjectsResponse with .data list
    return resp.data.data if hasattr(resp, "data") and hasattr(resp.data, "data") else resp


def get_or_create_project(name: str = "pplx", client=None, access_token: str = "", org_id: str = ""):
    """Return a project by name, creating it if necessary."""
    if client is None:
        client = get_bws_client(access_token)

    projects = list_sm_projects(client, org_id=org_id)
    for p in projects:
        if p.name == name:
            return p

    # Create
    oid = org_id or get_org_id_from_env_or_token(access_token)
    resp = client.projects().create(organization_id=oid, name=name)
    if hasattr(resp, "data"):
        return resp.data
    return resp


def get_secret_by_key(key: str, project_id: str, client=None, access_token: str = "", org_id: str = ""):
    """Look up a secret by key in a given project."""
    if client is None:
        client = get_bws_client(access_token)

    oid = org_id or get_org_id_from_env_or_token(access_token)
    resp = client.secrets().list(organization_id=oid)
    # Response is SecretIdentifiersResponse; actual list is nested
    secrets = resp.data.data if hasattr(resp, "data") and hasattr(resp.data, "data") else resp
    for s in secrets:
        if s.key == key:
            # Fetch full secret value
            full = client.secrets().get(s.id)
            return full.data if hasattr(full, "data") else full
    return None


def create_or_update_secret(key: str, value: str, project_id: str, note: str = "",
                             client=None, access_token: str = "", org_id: str = ""):
    """Create or overwrite a secret in a project."""
    if client is None:
        client = get_bws_client(access_token)

    oid = org_id or get_org_id_from_env_or_token(access_token)
    existing = get_secret_by_key(key, project_id, client=client, org_id=oid)
    if existing:
        resp = client.secrets().update(
            id=existing.id,
            key=key,
            value=value,
            note=note or existing.note,
            organization_id=oid,
            project_ids=[project_id],
        )
    else:
        resp = client.secrets().create(
            key=key,
            value=value,
            note=note,
            organization_id=oid,
            project_ids=[project_id],
        )
    return resp.data if hasattr(resp, "data") else resp
