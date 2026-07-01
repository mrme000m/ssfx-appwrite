"""Token refresh client for the Appwrite auth broker."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


class TokenClient:
    """Refreshes short-lived cTrader access tokens per grant_id."""

    def __init__(self, broker_url: str, internal_api_key: str):
        self._broker = broker_url.rstrip("/")
        self._internal_api_key = internal_api_key
        self._cache: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, grant_id: str) -> asyncio.Lock:
        return self._locks.setdefault(grant_id, asyncio.Lock())

    async def refresh(self, grant_id: str, buffer_seconds: float = 300.0) -> str:
        async with self._lock(grant_id):
            cached = self._cache.get(grant_id)
            if cached and time.time() + buffer_seconds < cached["expires_at"]:
                return cached["access_token"]

            async with aiohttp.ClientSession() as session, session.post(
                f"{self._broker}/internal/ctrader/refresh",
                json={"grantId": grant_id},
                headers={"x-internal-key": self._internal_api_key},
            ) as resp:
                body = await resp.json()
                if resp.status != 200:
                    raise RuntimeError(f"Token refresh failed: HTTP {resp.status} body={body}")

            access_token = body.get("access_token")
            if not access_token:
                raise RuntimeError(f"Token refresh failed: missing access_token in body={body}")

            expires_at_raw = body.get("expires_at", time.time() + 2_628_000)
            if isinstance(expires_at_raw, str):
                from datetime import UTC, datetime

                # Handle ISO-8601 strings with or without explicit timezone info.
                dt = datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                expires_at = dt.timestamp()
            else:
                expires_at = float(expires_at_raw)
            self._cache[grant_id] = {"access_token": access_token, "expires_at": expires_at}
            logger.info("Refreshed token for grant %s expires_at=%s", grant_id, expires_at)
            return access_token
