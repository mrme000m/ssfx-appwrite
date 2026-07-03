"""No-op signal store — logs warnings so operators know signal history is
not persisted, but execution continues for webhooks and dashboard API.

Planned replacement: Appwrite-native signal store for history persistence
and duplicate-after-restart protection.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from ssfx_parser import RawMessage, TradeSignal

logger = logging.getLogger(__name__)


class NoOpSignalStore:
    """Signal store that logs warnings and returns empty results.

    Webhooks are still processed and slaves still execute trades;
    only signal history and analytics queries are disabled.
    """

    def __init__(self) -> None:
        logger.warning("NoOpSignalStore active — signal history will NOT be persisted")

    def _warn(self, method: str) -> None:
        logger.debug("NoOpSignalStore.%s called (no-op)", method)

    def save_raw_message(self, msg: RawMessage) -> bool:
        self._warn("save_raw_message")
        return True

    def get_today_messages(self, chat_id: str | None = None) -> list[RawMessage]:
        self._warn("get_today_messages")
        return []

    def save_signal(self, signal: TradeSignal) -> bool:
        self._warn("save_signal")
        return True

    def get_signal(self, chat_id: str, message_id: int) -> TradeSignal | None:
        self._warn("get_signal")
        return None

    def get_active_signals(self, chat_id: str | None = None) -> list[TradeSignal]:
        self._warn("get_active_signals")
        return []

    def get_pending_entry_signals(self) -> list[TradeSignal]:
        self._warn("get_pending_entry_signals")
        return []

    def update_signal_status(
        self,
        chat_id: str,
        message_id: int,
        status: str,
        order_id: int | None = None,
        position_id: int | None = None,
        executed_price: float | None = None,
        error: str | None = None,
        volume: float | None = None,
    ) -> None:
        self._warn("update_signal_status")

    def update_signal_entry(self, chat_id: str, message_id: int, entry_price: float) -> None:
        self._warn("update_signal_entry")

    def record_trade(self, signal: TradeSignal, result: dict[str, Any]) -> None:
        self._warn("record_trade")

    def list_signals(self, limit: int = 50) -> list[TradeSignal]:
        self._warn("list_signals")
        return []

    def list_trades(self, limit: int = 50) -> list[dict[str, Any]]:
        self._warn("list_trades")
        return []

    async def watch_signals(self) -> AsyncIterator[TradeSignal]:
        self._warn("watch_signals")
        if False:
            yield  # type: ignore[unreachable]  # makes this an async generator
