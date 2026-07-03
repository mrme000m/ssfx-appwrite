"""Configuration loading — reads cTrader credentials from environment variables.

Resolution priority (highest wins):
1. CLI flags (--live, --account-id, --broker-url, --grant-id)
2. Environment variables (CTRADER_*)
3. .env file in project root
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Prefer CTRADER_AUTH_INTERNAL_KEY, fall back to generic INTERNAL_API_KEY
INTERNAL_KEY_ENV_VARS = ["CTRADER_AUTH_INTERNAL_KEY", "INTERNAL_API_KEY"]


@dataclass
class CliConfig:
    """Resolved cTrader CLI configuration."""

    client_id: str = ""
    client_secret: str = ""
    account_id: int = 0
    use_live: bool = False
    broker_url: str = ""  # api-internal / auth broker URL
    grant_id: str = ""
    internal_api_key: str = ""

    @property
    def is_appwrite_mode(self) -> bool:
        return bool(self.broker_url and self.grant_id and self.internal_api_key)

    @property
    def is_valid(self) -> bool:
        if not self.client_id:
            return False
        if self.is_appwrite_mode:
            return True
        return self.account_id != 0


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _find_dotenv() -> Path | None:
    """Search upward for .env file."""
    cwd = Path.cwd()
    for path in [cwd, cwd.parent, cwd.parent.parent]:
        env_file = path / ".env"
        if env_file.exists():
            return env_file
    return None


def load_config(
    *,
    use_live: bool | None = None,
    account_id: int | None = None,
    broker_url: str | None = None,
    grant_id: str | None = None,
) -> CliConfig:
    """Load cTrader credentials from environment / .env.

    Args:
        use_live: Override to live environment.
        account_id: Override account ID.
        broker_url: Override auth broker / api-internal URL.
        grant_id: Override grant ID.
    """
    dotenv_path = _find_dotenv()
    if dotenv_path:
        load_dotenv(dotenv_path, override=False)
        logger.info("Loaded .env from %s", dotenv_path)
    else:
        load_dotenv(override=False)

    client_id = _env("CTRADER_CLIENT_ID")
    client_secret = _env("CTRADER_CLIENT_SECRET")
    raw_account_id = _env("CTRADER_ACCOUNT_ID", "0")
    env_broker_url = _env("CTRADER_AUTH_BROKER_URL")
    env_grant_id = _env("CTRADER_AUTH_GRANT_ID")
    env_use_live = _env("CTRADER_USE_LIVE", "").lower() in ("1", "true", "yes")

    internal_api_key = ""
    for key in INTERNAL_KEY_ENV_VARS:
        internal_api_key = _env(key)
        if internal_api_key:
            break

    cfg = CliConfig(
        client_id=client_id,
        client_secret=client_secret,
        account_id=int(raw_account_id) if raw_account_id.isdigit() else 0,
        use_live=env_use_live,
        broker_url=env_broker_url,
        grant_id=env_grant_id,
        internal_api_key=internal_api_key,
    )

    # Apply CLI overrides (highest priority)
    if use_live is not None:
        cfg.use_live = use_live
    if account_id is not None:
        cfg.account_id = account_id
    if broker_url is not None:
        cfg.broker_url = broker_url
    if grant_id is not None:
        cfg.grant_id = grant_id

    return cfg
