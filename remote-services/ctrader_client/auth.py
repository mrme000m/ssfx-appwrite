"""Authentication — OAuth token management, persistence, and auto-refresh."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import aiohttp
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# cTrader OAuth token endpoint
TOKEN_URL = "https://openapi.ctrader.com/apps/token"


class TokenData(BaseModel):
    """Persisted OAuth token data."""

    access_token: str
    refresh_token: str
    expires_at: float  # Unix timestamp


class TokenManager:
    """Manages OAuth 2.0 tokens for cTrader Open API.

    Handles:
    - Loading/saving tokens from JSON file
    - Refreshing tokens before expiry
    - Auto-refresh background task
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_data: TokenData | None = None,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_data = token_data
        self._refresh_task: asyncio.Task[None] | None = None

    @property
    def access_token(self) -> str:
        if self._token_data is None:
            raise RuntimeError("No tokens loaded — authenticate first")
        return self._token_data.access_token

    @property
    def refresh_token(self) -> str:
        if self._token_data is None:
            raise RuntimeError("No tokens loaded — authenticate first")
        return self._token_data.refresh_token

    @property
    def has_tokens(self) -> bool:
        return self._token_data is not None

    # ── Persistence ────────────────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        """Save tokens to a JSON file."""
        if self._token_data is None:
            return
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self._token_data.model_dump_json(indent=2))
        logger.info("Tokens saved to %s", p)

    @classmethod
    def load(cls, path: str | Path, client_id: str, client_secret: str) -> TokenManager:
        """Load tokens from a JSON file."""
        p = Path(path)
        if not p.exists():
            return cls(client_id, client_secret)
        data = json.loads(p.read_text())
        token = TokenData(**data)
        return cls(client_id, client_secret, token_data=token)

    # ── Token refresh ──────────────────────────────────────────────────────

    async def refresh(self) -> TokenData:
        """Refresh access token using the refresh token.

        POSTs to the cTrader token endpoint with grant_type=refresh_token.
        Returns the new TokenData and updates internal state.
        """
        if self._token_data is None:
            raise RuntimeError("No refresh token available")

        form_data = aiohttp.FormData()
        form_data.add_field("client_id", self._client_id)
        form_data.add_field("client_secret", self._client_secret)
        form_data.add_field("grant_type", "refresh_token")
        form_data.add_field("refresh_token", self._token_data.refresh_token)

        async with aiohttp.ClientSession() as session:
            async with session.post(TOKEN_URL, data=form_data) as resp:
                body: dict[str, Any] = await resp.json()
                status = getattr(resp, "status", 200)
                if isinstance(status, int) and status >= 400:
                    raise RuntimeError(
                        f"Token refresh failed: HTTP {status} body={body}"
                    )

        access_token = body.get("access_token")
        if not access_token:
            raise RuntimeError(f"Token refresh failed: missing access_token in body={body}")

        expires_in = body.get("expires_in", 2_628_000)  # ~30 days default
        new_token = TokenData(
            access_token=access_token,
            refresh_token=body.get("refresh_token", self._token_data.refresh_token),
            expires_at=time.time() + expires_in,
        )
        self._token_data = new_token
        logger.info("Token refreshed, expires in %ds", expires_in)
        return new_token

    # ── Expiry check ───────────────────────────────────────────────────────

    def is_expired(self, buffer_seconds: float = 300) -> bool:
        """Check if the access token is expired (with safety buffer)."""
        if self._token_data is None:
            return True
        return time.time() + buffer_seconds >= self._token_data.expires_at

    # ── Auto-refresh ───────────────────────────────────────────────────────

    async def start_auto_refresh(self, refresh_ahead_seconds: float = 3600) -> None:
        """Start background task that refreshes tokens before expiry."""
        async def _loop() -> None:
            while True:
                if self._token_data is None:
                    await asyncio.sleep(60)
                    continue
                sleep_time = max(
                    0,
                    self._token_data.expires_at - time.time() - refresh_ahead_seconds,
                )
                if sleep_time > 0:
                    logger.info(
                        "Token refresh scheduled in %.0fs", sleep_time
                    )
                    await asyncio.sleep(sleep_time)
                try:
                    await self.refresh()
                except Exception as exc:
                    logger.error("Token auto-refresh failed: %s", exc)
                    await asyncio.sleep(60)  # retry in 60s on failure

        self._refresh_task = asyncio.create_task(_loop())

    async def stop_auto_refresh(self) -> None:
        """Cancel the auto-refresh background task."""
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None
