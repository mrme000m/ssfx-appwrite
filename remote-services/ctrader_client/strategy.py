"""Strategy Interface — event-driven base class for trading strategies."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from .event_bus import AsyncEventBus, Signal
from .execution import ExecutionManager, OrderRequest
from .market_data import BarClose, SpotTick

logger = logging.getLogger(__name__)


class BaseStrategy(ABC):
    """Base class for event-driven trading strategies.

    Strategies register with the event bus and receive typed events
    (ticks, bars, signals). They delegate order execution to the
    ExecutionManager — they never interact directly with the protocol.

    Subclass and override the event methods you need::

        class MyStrategy(BaseStrategy):
            async def on_bar(self, bar: BarClose) -> None:
                if self.should_buy(bar):
                    await self.buy_market(bar.symbol_id, 0.01, sl=...)
    """

    def __init__(
        self,
        name: str,
        account_id: int,
        execution: ExecutionManager,
        bus: AsyncEventBus,
    ):
        self.name = name
        self.account_id = account_id
        self._exec = execution
        self._bus = bus

        # Subscribe to core events
        bus.subscribe(SpotTick, self._on_tick)
        bus.subscribe(BarClose, self._on_bar)
        bus.subscribe(Signal, self._on_signal)

    # ── Event handlers (override in subclass) ──────────────────────────────

    async def on_tick(self, tick: SpotTick) -> None:
        """Called on every spot tick. Override for tick-level strategies."""

    async def on_bar(self, bar: BarClose) -> None:
        """Called on every bar close. Override for bar-based strategies."""

    @abstractmethod
    async def on_signal(self, signal: Signal) -> None:
        """Called when a condition evaluates to True."""

    # ── Internal dispatch ──────────────────────────────────────────────────

    async def _on_tick(self, tick: SpotTick) -> None:
        try:
            await self.on_tick(tick)
        except Exception:
            logger.exception("Strategy %s: error in on_tick", self.name)

    async def _on_bar(self, bar: BarClose) -> None:
        try:
            await self.on_bar(bar)
        except Exception:
            logger.exception("Strategy %s: error in on_bar", self.name)

    async def _on_signal(self, signal: Signal) -> None:
        try:
            await self.on_signal(signal)
        except Exception:
            logger.exception("Strategy %s: error in on_signal", self.name)

    # ── Order helpers ──────────────────────────────────────────────────────

    async def buy_market(
        self,
        symbol_id: int,
        volume_lots: float,
        sl: float | None = None,
        tp: float | None = None,
    ) -> None:
        """Place a market buy order."""
        await self._exec.submit(OrderRequest(
            account_id=self.account_id,
            symbol_id=symbol_id,
            order_type="MARKET",
            trade_side="BUY",
            volume_lots=volume_lots,
            stop_loss=sl,
            take_profit=tp,
        ))

    async def sell_market(
        self,
        symbol_id: int,
        volume_lots: float,
        sl: float | None = None,
        tp: float | None = None,
    ) -> None:
        """Place a market sell order."""
        await self._exec.submit(OrderRequest(
            account_id=self.account_id,
            symbol_id=symbol_id,
            order_type="MARKET",
            trade_side="SELL",
            volume_lots=volume_lots,
            stop_loss=sl,
            take_profit=tp,
        ))

    async def buy_limit(
        self,
        symbol_id: int,
        volume_lots: float,
        price: float,
        sl: float | None = None,
        tp: float | None = None,
    ) -> None:
        """Place a buy limit order."""
        await self._exec.submit(OrderRequest(
            account_id=self.account_id,
            symbol_id=symbol_id,
            order_type="LIMIT",
            trade_side="BUY",
            volume_lots=volume_lots,
            limit_price=price,
            stop_loss=sl,
            take_profit=tp,
        ))

    async def sell_limit(
        self,
        symbol_id: int,
        volume_lots: float,
        price: float,
        sl: float | None = None,
        tp: float | None = None,
    ) -> None:
        """Place a sell limit order."""
        await self._exec.submit(OrderRequest(
            account_id=self.account_id,
            symbol_id=symbol_id,
            order_type="LIMIT",
            trade_side="SELL",
            volume_lots=volume_lots,
            limit_price=price,
            stop_loss=sl,
            take_profit=tp,
        ))

    async def cancel_order(self, order_id: int) -> None:
        """Cancel a pending order."""
        await self._exec.cancel(self.account_id, order_id)

    async def close_position(
        self, position_id: int, volume_lots: float | None = None
    ) -> None:
        """Close a position (None = full close)."""
        vol: float | None = None if volume_lots == 0.0 else volume_lots
        await self._exec.close_position(self.account_id, position_id, vol)
