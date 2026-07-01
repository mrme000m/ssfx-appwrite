"""Appwrite-based token management — fetches tokens from ctrader-internal Appwrite Function.

This is the unified auth module for services that need cTrader access tokens
managed through the Appwrite auth layer. Tokens are encrypted at rest in
Appwrite TablesDB (slave_accounts table) and decrypted only inside the
ctrader-internal function. The Python side receives only short-lived access
tokens and grant_ids.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

import aiohttp

from .auth import TokenData

logger = logging.getLogger(__name__)


class AppwriteTokenManager:
    """Token manager that delegates refresh to the ctrader-internal Appwrite Function.

    Compatible with the BrokerTokenManager interface so it can be dropped into
    CTraderSession or anywhere a TokenManager/BrokerTokenManager is expected.

    Usage::

        mgr = AppwriteTokenManager(
            internal_url="https://internal.mrme.tech",
            grant_id="abc123...",
            internal_api_key="sk-...",
        )
        await mgr.refresh()
        token = mgr.access_token
    """

    def __init__(
        self,
        internal_url: str,
        grant_id: str,
        internal_api_key: str,
    ):
        self._internal_url = internal_url.rstrip("/")
        self._grant_id = grant_id
        self._internal_api_key = internal_api_key
        self._token_data: TokenData | None = None
        self._refresh_task: asyncio.Task[None] | None = None

    @property
    def access_token(self) -> str:
        if self._token_data is None:
            raise RuntimeError("No tokens loaded — call refresh() first")
        return self._token_data.access_token

    @property
    def has_tokens(self) -> bool:
        return self._token_data is not None

    @property
    def grant_id(self) -> str:
        return self._grant_id

    async def refresh(self) -> TokenData:
        """Refresh the access token via ctrader-internal.

        POST /internal/ctrader/refresh with grant_id, receives
        {access_token, expires_at} from the Appwrite function.
        """
        async with aiohttp.ClientSession() as session, session.post(
            f"{self._internal_url}/internal/ctrader/refresh",
            json={"grantId": self._grant_id},
            headers={"x-internal-key": self._internal_api_key},
        ) as resp:
            body: dict[str, Any] = await resp.json()
            if resp.status != 200:
                raise RuntimeError(
                    f"Token refresh failed: HTTP {resp.status} body={body}"
                )

        access_token = body.get("access_token")
        if not access_token:
            raise RuntimeError(
                f"Token refresh failed: missing access_token in body={body}"
            )

        expires_at_raw = body.get("expires_at", time.time() + 2_628_000)
        if isinstance(expires_at_raw, str):
            dt = datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            expires_at = dt.timestamp()
        else:
            expires_at = float(expires_at_raw)

        self._token_data = TokenData(
            access_token=access_token,
            refresh_token="",
            expires_at=expires_at,
        )
        logger.info("Token refreshed via Appwrite for grant %s", self._grant_id)
        return self._token_data

    def is_expired(self, buffer_seconds: float = 300) -> bool:
        if self._token_data is None:
            return True
        return time.time() + buffer_seconds >= self._token_data.expires_at

    async def start_auto_refresh(self, refresh_ahead_seconds: float = 3600) -> None:
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
                        "Appwrite token refresh for grant %s in %.0fs",
                        self._grant_id, sleep_time,
                    )
                    await asyncio.sleep(sleep_time)
                try:
                    await self.refresh()
                except Exception as exc:
                    logger.error(
                        "Auto-refresh failed for grant %s: %s", self._grant_id, exc
                    )
                    await asyncio.sleep(60)

        self._refresh_task = asyncio.create_task(_loop())

    async def stop_auto_refresh(self) -> None:
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None


class AppwriteMultiTokenClient:
    """Multi-grant token client backed by Appwrite ctrader-internal.

    Maintains a cache of access tokens for multiple grant_ids with
    per-grant locking to avoid thundering-herd refreshes.
    Used by the AccountHub to manage tokens for all slave accounts.
    """

    def __init__(self, internal_url: str, internal_api_key: str):
        self._internal_url = internal_url.rstrip("/")
        self._internal_api_key = internal_api_key
        self._cache: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, grant_id: str) -> asyncio.Lock:
        return self._locks.setdefault(grant_id, asyncio.Lock())

    async def refresh(self, grant_id: str, buffer_seconds: float = 300.0) -> str:
        """Get a valid access token for grant_id, refreshing if needed."""
        async with self._lock(grant_id):
            cached = self._cache.get(grant_id)
            if cached and time.time() + buffer_seconds < cached["expires_at"]:
                return cached["access_token"]

            async with aiohttp.ClientSession() as session, session.post(
                f"{self._internal_url}/internal/ctrader/refresh",
                json={"grantId": grant_id},
                headers={"x-internal-key": self._internal_api_key},
            ) as resp:
                body = await resp.json()
                if resp.status != 200:
                    raise RuntimeError(
                        f"Token refresh failed for grant {grant_id}: "
                        f"HTTP {resp.status} body={body}"
                    )

            access_token = body.get("access_token")
            if not access_token:
                raise RuntimeError(
                    f"Token refresh failed for grant {grant_id}: "
                    f"missing access_token"
                )

            expires_at_raw = body.get("expires_at", time.time() + 2_628_000)
            if isinstance(expires_at_raw, str):
                dt = datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                expires_at = dt.timestamp()
            else:
                expires_at = float(expires_at_raw)

            self._cache[grant_id] = {
                "access_token": access_token,
                "expires_at": expires_at,
            }
            logger.info("Refreshed token for grant %s via Appwrite", grant_id)
            return access_token

    def clear_cache(self) -> None:
        self._cache.clear()
