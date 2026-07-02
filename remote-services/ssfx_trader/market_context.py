"""Client for fetching market context from DataService."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class MarketContext:
    """Aggregated market context for a symbol."""

    symbol: str
    timestamp_ms: int
    tick: dict[str, Any] | None = None
    bars: dict[str, dict[str, Any]] = None  # type: ignore[assignment]
    indicators: list[dict[str, Any]] = None  # type: ignore[assignment]
    signals: list[dict[str, Any]] = None  # type: ignore[assignment]
    quality: list[dict[str, Any]] = None  # type: ignore[assignment]
    error: str | None = None

    def __post_init__(self) -> None:
        if self.bars is None:
            self.bars = {}
        if self.indicators is None:
            self.indicators = []
        if self.signals is None:
            self.signals = []
        if self.quality is None:
            self.quality = []

    @property
    def latest_price(self) -> float | None:
        if self.tick:
            return self.tick.get("bid") or self.tick.get("ask") or self.tick.get("last")
        return None

    @property
    def spread(self) -> float | None:
        if not self.tick:
            return None
        bid = self.tick.get("bid")
        ask = self.tick.get("ask")
        if bid is not None and ask is not None:
            return float(ask) - float(bid)
        return None

    def is_stale(self, threshold_seconds: float = 30.0) -> bool:
        import time

        if not self.timestamp_ms:
            return True
        return (time.time() * 1000 - self.timestamp_ms) > (threshold_seconds * 1000)

    def quality_ok(self) -> bool:
        if not self.quality:
            return True
        for report in self.quality:
            if report.get("is_healthy") is False:
                return False
        return True


class DataServiceClient:
    """HTTP client for DataService market context API."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 10.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def get_context(self, symbol: str) -> MarketContext:
        import time

        url = f"{self.base_url}/api/v1/context/{symbol}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers=self._headers())
                response.raise_for_status()
                payload = response.json()
                return MarketContext(
                    symbol=payload.get("symbol", symbol),
                    timestamp_ms=payload.get("timestamp_ms", int(time.time() * 1000)),
                    tick=payload.get("tick"),
                    bars=payload.get("bars", {}),
                    indicators=payload.get("indicators", []),
                    signals=payload.get("signals", []),
                    quality=payload.get("quality", []),
                )
        except httpx.HTTPStatusError as exc:
            error = f"DataService HTTP {exc.response.status_code}: {exc.response.text}"
            logger.warning(error)
            return MarketContext(symbol=symbol, timestamp_ms=0, error=error)
        except Exception as exc:
            error = f"DataService unreachable: {exc}"
            logger.warning(error)
            return MarketContext(symbol=symbol, timestamp_ms=0, error=error)

    async def get_gold_quant(self) -> dict[str, Any] | None:
        url = f"{self.base_url}/api/v1/gold/quant"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers=self._headers())
                response.raise_for_status()
                return response.json()
        except Exception as exc:
            logger.warning("Failed to fetch gold quant snapshot: %s", exc)
            return None
