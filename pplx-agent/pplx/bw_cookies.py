"""Cookie loader for Perplexity AI via Bitwarden Secrets Manager.

Tries BWS first (bitwarden-sdk). Falls back to the legacy bw CLI secure-note
method if BWS is not configured or the SDK is unavailable.
"""

import json
import os
from pathlib import Path

from .config import load_project_env


_DEFAULT_COOKIE_PATH = Path.home() / ".config" / "perplexity" / "cookies.json"


def load_cookies(item_name: str = "perplexity-cookies") -> dict:
    """Load Perplexity cookies.

    Resolution order:
      1. $PERPLEXITY_COOKIES_PATH file on disk.
      2. Default cookie path (~/.config/perplexity/cookies.json).
      3. Bitwarden Secrets Manager (bitwarden-sdk) secret named 'perplexity-cookies'.
      4. Legacy Bitwarden vault secure note named 'perplexity.ai' (via bw CLI).

    Args:
        item_name: Key of the BWS secret or name of the bw secure note.

    Returns:
        Dictionary of cookie key-value pairs.
    """
    load_project_env()

    # 1. Explicit disk override
    disk_path = os.getenv("PERPLEXITY_COOKIES_PATH", "")
    if disk_path and Path(disk_path).exists():
        return _parse_cookies(Path(disk_path).read_text(), disk_path)

    # 2. Default cookie path (where login.py / refresh_cookies.py save)
    if _DEFAULT_COOKIE_PATH.exists():
        return _parse_cookies(_DEFAULT_COOKIE_PATH.read_text(), str(_DEFAULT_COOKIE_PATH))

    # 3. BWS via SDK
    try:
        return _load_from_bws(item_name)
    except (RuntimeError, ImportError, ValueError) as e:
        # If BWS is explicitly configured but fails, surface the error
        if os.getenv("BWS_ACCESS_TOKEN"):
            raise RuntimeError(f"BWS cookie load failed: {e}") from e

    # 4. Legacy bw CLI fallback
    try:
        return _load_from_bw_vault("perplexity.ai")
    except Exception:
        pass

    raise RuntimeError(
        "No Perplexity cookies found.\n"
        "Options:\n"
        f"  • Ensure cookies exist at {_DEFAULT_COOKIE_PATH}\n"
        "  • Set PERPLEXITY_COOKIES_PATH=/path/to/cookies.json\n"
        "  • Run: python scripts/setup_bws_secret.py\n"
        "  • Store cookies in a Bitwarden secure note named 'perplexity.ai'"
    )


def _parse_cookies(text: str, source: str) -> dict:
    cookies = json.loads(text)
    if not isinstance(cookies, dict):
        raise RuntimeError(
            f"Cookies must be a JSON object (dict), got {type(cookies).__name__} from {source}"
        )
    return cookies


def _load_from_bws(secret_key: str = "perplexity-cookies") -> dict:
    from pplx.bws_auth import get_bws_client, get_or_create_project, get_secret_by_key

    client = get_bws_client()
    project = get_or_create_project("pplx", client=client)
    secret = get_secret_by_key(secret_key, project.id, client=client)
    if not secret:
        raise RuntimeError(
            f"Secret '{secret_key}' not found in BWS project '{project.name}'."
        )
    return _parse_cookies(secret.value, f"BWS secret {secret.id}")


def _load_from_bw_vault(note_name: str = "perplexity.ai") -> dict:
    """Legacy fallback using the bw CLI secure note."""
    import subprocess

    def _ensure_session() -> str:
        session = os.environ.get("BW_SESSION")
        if session:
            status = subprocess.run(
                ["bw", "status", "--session", session],
                capture_output=True, text=True
            )
            if status.returncode == 0:
                data = json.loads(status.stdout)
                if data.get("status") == "unlocked":
                    return session
        try:
            mp_proc = subprocess.run(
                ["security", "find-generic-password", "-a", "bw-master-password", "-w"],
                capture_output=True, text=True
            )
            if mp_proc.returncode != 0:
                raise RuntimeError("Keychain master password not found.")
            master_password = mp_proc.stdout.strip()
            unlock = subprocess.run(
                ["bw", "unlock", "--passwordenv", "BW_PASSWORD", "--raw"],
                capture_output=True, text=True,
                env={**os.environ, "BW_PASSWORD": master_password}
            )
            if unlock.returncode == 0:
                token = unlock.stdout.strip()
                os.environ["BW_SESSION"] = token
                return token
            else:
                raise RuntimeError(f"bw unlock failed: {unlock.stderr}")
        except FileNotFoundError:
            raise RuntimeError("bw CLI or security tool not found.")

    session = _ensure_session()
    list_cmd = subprocess.run(
        ["bw", "list", "items", "--search", note_name, "--session", session],
        capture_output=True, text=True
    )
    if list_cmd.returncode != 0:
        raise RuntimeError(f"bw list failed: {list_cmd.stderr}")

    items = json.loads(list_cmd.stdout)
    notes = [i for i in items if i.get("name") == note_name and i.get("type") == 2]
    if not notes:
        raise RuntimeError(f"No secure note named '{note_name}' found in vault.")

    cookies_json = notes[0].get("notes", "")
    if not cookies_json:
        raise RuntimeError(f"Secure note '{note_name}' is empty.")
    return _parse_cookies(cookies_json, f"bw vault note '{note_name}'")
