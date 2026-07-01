"""Detect consecutive unannounced losing signals and pause symbols."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from .event_relay import EventRelay

logger = logging.getLogger(__name__)


class BadTradeDetector:
    """Tracks per-source/symbol/direction outcomes and warns on repeated unannounced losses.

    A signal source is considered to have an unannounced loss when a NEW signal
    for a symbol/direction is followed by another NEW signal for the same
    symbol/direction without an intervening TP, SL, CLOSE or CANCEL update.
    """

    def __init__(
        self,
        event_relay: EventRelay,
        threshold: int = 2,
        window_seconds: float = 86400.0,
    ):
        self._event_relay = event_relay
        self._threshold = threshold
        self._window_seconds = window_seconds
        self._history: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._disabled_symbols: set[str] = set()
        self._lock = asyncio.Lock()

    async def on_event(
        self,
        grant_id: str,
        ctid: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        if event_type != "signal_processed":
            return

        signal_type = payload.get("signal_type", "")
        symbol = (payload.get("symbol") or "").upper()
        direction = (payload.get("direction") or "").upper()
        source = payload.get("source", "default")
        if not symbol or not direction:
            return

        key = (source, symbol, direction)
        now = datetime.now(UTC).timestamp()

        async with self._lock:
            state = self._history.get(key)

            if signal_type == "NEW":
                if state and not state["resolved"]:
                    state["consecutive"] += 1
                    state["last_seen"] = now
                    if state["consecutive"] >= self._threshold:
                        await self._pause_symbol(grant_id, ctid, source, symbol, direction, state["consecutive"])
                else:
                    self._history[key] = {
                        "consecutive": 0,
                        "resolved": False,
                        "last_seen": now,
                    }

            elif signal_type in ("TP_HIT", "SL_HIT", "CLOSE", "CLOSE_HALF", "CLOSE_PARTIAL", "CANCEL"):
                if state:
                    state["resolved"] = True
                    state["last_seen"] = now

            self._prune_old(now)

    async def _pause_symbol(
        self,
        grant_id: str,
        ctid: int,
        source: str,
        symbol: str,
        direction: str,
        consecutive: int,
    ) -> None:
        if symbol in self._disabled_symbols:
            return
        self._disabled_symbols.add(symbol)
        logger.warning(
            "Bad-trade detector pausing %s (%s) after %d consecutive unannounced losses",
            symbol,
            direction,
            consecutive,
        )
        await self._event_relay.emit(
            grant_id,
            ctid,
            "bad_trade_warning",
            {
                "source": source,
                "symbol": symbol,
                "direction": direction,
                "consecutive_unannounced_losses": consecutive,
                "action": "symbol_paused",
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )

    def is_paused(self, symbol: str) -> bool:
        return symbol.upper() in self._disabled_symbols

    def _prune_old(self, now: float) -> None:
        cutoff = now - self._window_seconds
        stale = [k for k, v in self._history.items() if v["last_seen"] < cutoff]
        for k in stale:
            self._history.pop(k, None)
