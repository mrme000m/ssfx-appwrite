"""Client that consumes the existing dataservice and normalizes events."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from .models import ContextSnapshot, SpotTick

logger = logging.getLogger(__name__)


class DataServiceClient:
    """Polls the existing SSFX dataservice and yields normalized market events."""

    def __init__(self, base_url: str, api_key: str = "", poll_interval_ms: int = 250):
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self._poll_interval = poll_interval_ms / 1000.0
        self._session: aiohttp.ClientSession | None = None
        self._symbols: set[str] = set()

    async def __aenter__(self) -> "DataServiceClient":
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    async def subscribe(self, symbol: str) -> None:
        """Tell the dataservice to keep this symbol active."""
        if not self._session:
            raise RuntimeError("Client not started")
        symbol = symbol.upper()
        if symbol in self._symbols:
            return
        try:
            async with self._session.post(
                f"{self._base}/feed/subscribe",
                json={"symbol": symbol},
                headers=self._headers(),
            ) as resp:
                if resp.status < 400:
                    logger.info("Subscribed dataservice to %s", symbol)
                else:
                    logger.warning("Failed to subscribe %s: HTTP %s (will poll public data anyway)", symbol, resp.status)
        except Exception as exc:
            logger.warning("Could not subscribe %s: %s (will poll public data anyway)", symbol, exc)
        # The public /data/{symbol} endpoint is always available; add the symbol
        # to the poll set even when the admin-only subscribe call fails.
        self._symbols.add(symbol)

    async def unsubscribe(self, symbol: str) -> None:
        symbol = symbol.upper()
        if symbol not in self._symbols:
            return
        if self._session:
            try:
                await self._session.post(
                    f"{self._base}/feed/unsubscribe",
                    json={"symbol": symbol},
                    headers=self._headers(),
                )
            except Exception as exc:
                logger.warning("Could not unsubscribe %s: %s", symbol, exc)
        self._symbols.discard(symbol)

    async def fetch_price(self, symbol: str) -> dict[str, Any]:
        if not self._session:
            raise RuntimeError("Client not started")
        async with self._session.get(
            f"{self._base}/data/{symbol.upper()}",
            headers=self._headers(),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def fetch_context(self, symbol: str) -> dict[str, Any]:
        if not self._session:
            raise RuntimeError("Client not started")
        async with self._session.get(
            f"{self._base}/context/{symbol.upper()}",
            headers=self._headers(),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def fetch_quality(self, symbol: str) -> dict[str, Any]:
        if not self._session:
            raise RuntimeError("Client not started")
        async with self._session.get(
            f"{self._base}/quality/{symbol.upper()}",
            headers=self._headers(),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def poll_loop(self, queue: asyncio.Queue[SpotTick | ContextSnapshot]) -> None:
        """Background task that polls subscribed symbols and puts events on the queue."""
        if not self._session:
            raise RuntimeError("Client not started")
        while True:
            try:
                for symbol in list(self._symbols):
                    try:
                        price = await self.fetch_price(symbol)
                        tick = self._normalize_price(symbol, price)
                        if tick:
                            await queue.put(tick)
                    except Exception as exc:
                        logger.debug("Price poll failed for %s: %s", symbol, exc)
                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Poll loop error: %s", exc)
                await asyncio.sleep(self._poll_interval)

    def _normalize_price(self, symbol: str, data: dict[str, Any]) -> SpotTick | None:
        bid = data.get("bid") or data.get("close") or data.get("price")
        ask = data.get("ask")
        if bid is None:
            return None
        if ask is None and data.get("spread"):
            ask = bid + data["spread"]
        if ask is None:
            return None
        spread = data.get("spread", ask - bid)
        ts = data.get("timestamp_ms") or data.get("timestamp")
        if ts is None:
            import time as _time
            ts = int(_time.time() * 1000)
        return SpotTick(
            symbol_id=data.get("symbol_id", 0),
            symbol_name=symbol.upper(),
            bid=float(bid),
            ask=float(ask),
            spread=float(spread),
            timestamp_ms=int(ts),
        )

    async def get_context_snapshot(self, symbol: str) -> ContextSnapshot:
        async def _ctx() -> dict[str, Any]:
            try:
                return await self.fetch_context(symbol)
            except Exception as exc:
                logger.debug("Context fetch failed for %s: %s", symbol, exc)
                return {}

        async def _quality() -> dict[str, Any]:
            try:
                return await self.fetch_quality(symbol)
            except Exception as exc:
                logger.debug("Quality fetch failed for %s: %s", symbol, exc)
                return {}

        price, context, quality = await asyncio.gather(
            self.fetch_price(symbol),
            _ctx(),
            _quality(),
        )
        import time as _time
        return ContextSnapshot(
            symbol_name=symbol.upper(),
            price=price,
            context=context,
            quality=quality,
            timestamp_ms=int(_time.time() * 1000),
        )
