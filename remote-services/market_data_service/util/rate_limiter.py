"""Async byte-rate limiter for InfluxDB free-tier write throttling."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Iterator
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ByteRateLimiter:
    """Token-bucket style limiter that controls bytes per second.

    The limiter is intentionally permissive: it allows bursts up to ``burst``
    bytes and then throttles to ``rate`` bytes/second on average. Negative
    rates disable throttling entirely.
    """

    def __init__(self, rate: float, burst: float) -> None:
        """Initialize the limiter.

        Args:
            rate: Sustained bytes per second. <=0 disables limiting.
            burst: Maximum bytes allowed in a single burst.
        """
        self.rate = max(0.0, float(rate))
        self.burst = max(0.0, float(burst))
        self._tokens = self.burst
        self._last_update = time.monotonic()
        self._lock = asyncio.Lock()

    def _replenish(self) -> None:
        """Add tokens based on elapsed time since last check."""
        if self.rate <= 0:
            return
        now = time.monotonic()
        elapsed = now - self._last_update
        self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
        self._last_update = now

    async def acquire(self, bytes_needed: int) -> None:
        """Acquire permission to send ``bytes_needed`` bytes.

        Sleeps asynchronously if the bucket does not have enough tokens.
        """
        if self.rate <= 0 or bytes_needed <= 0:
            return

        async with self._lock:
            self._replenish()
            if self._tokens >= bytes_needed:
                self._tokens -= bytes_needed
                return

            deficit = bytes_needed - self._tokens
            wait_seconds = deficit / self.rate
            self._tokens = 0.0
            self._last_update = time.monotonic()

        logger.debug(
            "Throttling InfluxDB write: waiting %.3fs for %d bytes",
            wait_seconds,
            bytes_needed,
        )
        await asyncio.sleep(wait_seconds)

        async with self._lock:
            self._replenish()
            self._tokens -= bytes_needed

    async def wrap_iter(self, iterator: Iterator[T] | AsyncIterator[T], item_size: int) -> AsyncIterator[T]:
        """Throttle an iterator by ``item_size`` bytes per item."""
        if isinstance(iterator, AsyncIterator):
            async for item in iterator:
                await self.acquire(item_size)
                yield item
        else:
            for item in iterator:
                await self.acquire(item_size)
                yield item
