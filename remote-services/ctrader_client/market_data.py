"""Market Data Manager — subscriptions, domain events, price normalisation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ── Domain events ──────────────────────────────────────────────────────────


@dataclass(slots=True)
class SpotTick:
    """A single spot price tick."""

    symbol_id: int
    symbol_name: str
    bid: float
    ask: float
    timestamp_ms: int


@dataclass(slots=True)
class BarClose:
    """A completed OHLCV bar (trendbar)."""

    symbol_id: int
    symbol_name: str
    period: str  # "M1", "H1", etc.
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp_ms: int


@dataclass(slots=True)
class DepthUpdate:
    """Order book depth change."""

    symbol_id: int
    new_quotes: list[Any]
    deleted_ids: list[int]


@dataclass(slots=True)
class ExecutionEvent:
    """Typed wrapper for cTrader execution events.

    Covers: order accepted/filled/cancelled/rejected,
    position created/closed, trade record, balance update.
    """

    event_type: str  # e.g. "ORDER_ACCEPTED", "ORDER_FILLED"
    order_id: int | None = None
    position_id: int | None = None
    symbol_id: int | None = None
    volume: float = 0.0
    price: float = 0.0
    error_code: str | None = None
    raw: Any = None  # original protobuf message for advanced use


# ── Symbol metadata ────────────────────────────────────────────────────────


@dataclass(slots=True)
class SymbolInfo:
    """Cached symbol metadata.

    Volume fields (lot_size, min_volume, step_volume) are stored in real
    base-currency units (e.g. 100_000 for EURUSD). The cTrader API encodes
    these in 1/100th of a unit (cents); the /100 conversion is done in
    enrich_symbols() at ingestion time.
    """

    name: str
    digits: int
    pip_size: float
    min_volume: int = 0       # min volume in base currency units (real)
    step_volume: int = 0      # volume step in base currency units (real)
    lot_size: int = 100_000   # 1 lot in base currency units (real, Forex default)


# ── Market Data Manager ────────────────────────────────────────────────────


class MarketDataManager:
    """Manages market data subscriptions and normalises raw Protobuf events.

    Converts integer prices to floats, decodes trendbar deltas, and publishes
    domain events to the provided queues.
    """

    def __init__(
        self,
        protocol: Any,  # CTraderProtocolClient
        tick_bus: Any,  # asyncio.Queue
        bar_bus: Any,  # asyncio.Queue
        depth_bus: Any,  # asyncio.Queue
    ):
        self._proto = protocol
        self._tick_bus = tick_bus
        self._bar_bus = bar_bus
        self._depth_bus = depth_bus
        self._symbols: dict[int, SymbolInfo] = {}
        self._subscribed_spots: set[int] = set()
        self._subscribed_bars: dict[int, set[str]] = {}  # symbol_id → periods
        self._subscribed_depth: set[int] = set()
        self._last_ticks: dict[int, SpotTick] = {}

        # Register protocol handlers
        self._proto.subscribe(
            _payload_type("ProtoOASpotEvent"), self._on_spot_event
        )
        self._proto.subscribe(
            _payload_type("ProtoOADepthEvent"), self._on_depth_event
        )
        self._proto.subscribe(
            _payload_type("ProtoOASymbolChangedEvent"), self._on_symbol_changed
        )

    def register_symbol(self, symbol_id: int, name: str, digits: int = 5, pip_size: float | None = None) -> None:
        """Register symbol metadata for price conversion."""
        if pip_size is None:
            pip_size = 10 ** (-digits)
        self._symbols[symbol_id] = SymbolInfo(name, digits, pip_size)

    def register_symbols(self, symbols: dict[int, dict]) -> None:
        """Register multiple symbols at once.

        Args:
            symbols: {symbol_id: {"name": str, "digits": int, "pip_size": float}}
        """
        for sid, info in symbols.items():
            self.register_symbol(
                sid,
                info["name"],
                info.get("digits", 5),
                info.get("pip_size"),
            )

    async def enrich_symbols(self, symbol_ids: list[int], account_id: int) -> None:
        """Fetch full symbol details from API and update SymbolInfo.

        ProtoOASymbolsListRes only returns ProtoOALightSymbol (no digits).
        ProtoOASymbolByIdReq accepts multiple symbolIds (repeated field) and
        returns full ProtoOASymbol with digits, volumes, pip info, etc.
        """
        from ctrader_open_api.messages.OpenApiMessages_pb2 import (
            ProtoOASymbolByIdReq,
        )

        req = ProtoOASymbolByIdReq()
        req.ctidTraderAccountId = account_id
        req.symbolId.extend(symbol_ids)

        res = await (await self._proto.send(req))
        for sym in res.symbol:  # list of ProtoOASymbol
            digits = sym.digits
            pip_size = 10 ** (-digits)
            existing = self._symbols.get(sym.symbolId)
            name = existing.name if existing else str(sym.symbolId)
            # ProtoOASymbol volume fields are in 1/100th of a unit (cents).
            # Convert to real base-currency units for internal use.
            raw_lot_size = getattr(sym, "lotSize", 0) or 0
            raw_min_vol = getattr(sym, "minVolume", 0) or 0
            raw_step_vol = getattr(sym, "stepVolume", 0) or 0
            self._symbols[sym.symbolId] = SymbolInfo(
                name=name,
                digits=digits,
                pip_size=pip_size,
                min_volume=raw_min_vol // 100 if raw_min_vol else 0,
                step_volume=raw_step_vol // 100 if raw_step_vol else 0,
                lot_size=raw_lot_size // 100 if raw_lot_size else 100_000,
            )

    # ── Price conversion ───────────────────────────────────────────────────

    @staticmethod
    def price_from_relative(raw: int, digits: int) -> float:
        """Convert relative price integer to float.

        cTrader prices are transmitted as integer × 10⁻⁵.
        """
        return round(raw / 1e5, digits)

    @staticmethod
    def volume_from_api(raw: int) -> float:
        """Convert cTrader API volume to base currency units.

        Volume is in 0.01 of a unit per the API spec:
        "e.g. 1000 in protocol means 10.00 units".
        """
        return raw / 100

    # ── Subscriptions ──────────────────────────────────────────────────────

    def get_last_tick(self, symbol_id: int) -> SpotTick | None:
        """Return the most recent spot tick for a symbol, if any."""
        return self._last_ticks.get(symbol_id)

    def get_last_price(self, symbol_id: int, side: str = "ask") -> float | None:
        """Return the most recent price for a symbol (bid or ask)."""
        tick = self._last_ticks.get(symbol_id)
        if tick is None:
            return None
        return tick.ask if side.lower() == "ask" else tick.bid

    async def subscribe_spots(self, account_id: int, symbol_ids: list[int]) -> None:
        """Subscribe to spot quotes for the given symbols."""
        new_ids = [s for s in symbol_ids if s not in self._subscribed_spots]
        if not new_ids:
            return

        req = _import("ProtoOASubscribeSpotsReq")()
        req.ctidTraderAccountId = account_id
        req.symbolId.extend(new_ids)
        req.subscribeToSpotTimestamp = True
        await (await self._proto.send(req))
        self._subscribed_spots.update(new_ids)
        logger.info("Subscribed to spots: %s", new_ids)

    async def subscribe_live_bars(
        self, account_id: int, symbol_id: int, period: str
    ) -> None:
        """Subscribe to live trendbars. Requires spot subscription first."""
        # Spots must be active first (API requirement)
        await self.subscribe_spots(account_id, [symbol_id])

        req = _import("ProtoOASubscribeLiveTrendbarReq")()
        req.ctidTraderAccountId = account_id
        req.symbolId = symbol_id
        req.period = _import("ProtoOATrendbarPeriod").Value(period)
        await (await self._proto.send(req))
        self._subscribed_bars.setdefault(symbol_id, set()).add(period)
        logger.info("Subscribed to live bars: %s %s", symbol_id, period)

    async def subscribe_depth(self, account_id: int, symbol_id: int) -> None:
        """Subscribe to depth-of-market (Level II) quotes."""
        req = _import("ProtoOASubscribeDepthQuotesReq")()
        req.ctidTraderAccountId = account_id
        req.symbolId.append(symbol_id)
        await (await self._proto.send(req))
        self._subscribed_depth.add(symbol_id)
        logger.info("Subscribed to depth: %s", symbol_id)

    async def unsubscribe_spots(self, account_id: int, symbol_ids: list[int]) -> None:
        """Unsubscribe from spot quotes for the given symbols."""
        ids = [s for s in symbol_ids if s in self._subscribed_spots]
        if not ids:
            return
        req = _import("ProtoOAUnsubscribeSpotsReq")()
        req.ctidTraderAccountId = account_id
        req.symbolId.extend(ids)
        await (await self._proto.send(req))
        self._subscribed_spots.difference_update(ids)
        logger.info("Unsubscribed from spots: %s", ids)

    async def unsubscribe_live_bars(
        self, account_id: int, symbol_id: int, period: str
    ) -> None:
        """Unsubscribe from live trendbars."""
        req = _import("ProtoOAUnsubscribeLiveTrendbarReq")()
        req.ctidTraderAccountId = account_id
        req.symbolId = symbol_id
        req.period = _import("ProtoOATrendbarPeriod").Value(period)
        await (await self._proto.send(req))
        self._subscribed_bars.get(symbol_id, set()).discard(period)
        if symbol_id in self._subscribed_bars and not self._subscribed_bars[symbol_id]:
            self._subscribed_bars.pop(symbol_id, None)
        logger.info("Unsubscribed from live bars: %s %s", symbol_id, period)

    async def unsubscribe_depth(self, account_id: int, symbol_id: int) -> None:
        """Unsubscribe from depth-of-market quotes."""
        req = _import("ProtoOAUnsubscribeDepthQuotesReq")()
        req.ctidTraderAccountId = account_id
        req.symbolId.append(symbol_id)
        await (await self._proto.send(req))
        self._subscribed_depth.discard(symbol_id)
        logger.info("Unsubscribed from depth: %s", symbol_id)

    async def get_historical_trendbars(
        self,
        account_id: int,
        symbol_id: int,
        period: str,
        from_timestamp: int = 0,
        to_timestamp: int = 0,
        count: int = 0,
    ) -> Any:
        """Request historical trendbars. Returns the resolved response.

        Args:
            from_timestamp: Unix ms to search from (0 = from beginning).
            to_timestamp: Unix ms to search to (0 = until now).
            count: Limit number of bars returned (back from to_timestamp).
        """
        req = _import("ProtoOAGetTrendbarsReq")()
        req.ctidTraderAccountId = account_id
        req.symbolId = symbol_id
        req.period = _import("ProtoOATrendbarPeriod").Value(period)
        req.fromTimestamp = from_timestamp
        req.toTimestamp = to_timestamp
        if count:
            req.count = count
        return await (await self._proto.send(req))

    async def get_historical_ticks(
        self,
        account_id: int,
        symbol_id: int,
        from_timestamp: int,
        to_timestamp: int,
    ) -> Any:
        """Request historical tick data. Max 1 week window."""
        req = _import("ProtoOAGetTickDataReq")()
        req.ctidTraderAccountId = account_id
        req.symbolId = symbol_id
        req.from_ = from_timestamp
        req.to = to_timestamp
        return await (await self._proto.send(req))

    async def get_symbols(self, account_id: int) -> Any:
        """Request the full symbol list."""
        req = _import("ProtoOASymbolsListReq")()
        req.ctidTraderAccountId = account_id
        return await (await self._proto.send(req))

    # ── Event handlers ─────────────────────────────────────────────────────

    async def _on_spot_event(self, msg: Any) -> None:
        """Handle ProtoOASpotEvent."""
        sym = self._symbols.get(msg.symbolId)
        digits = sym.digits if sym else 5
        name = sym.name if sym else str(msg.symbolId)

        # Spot tick
        if msg.HasField("bid") and msg.HasField("ask"):
            tick = SpotTick(
                symbol_id=msg.symbolId,
                symbol_name=name,
                bid=self.price_from_relative(msg.bid, digits),
                ask=self.price_from_relative(msg.ask, digits),
                timestamp_ms=getattr(msg, "timestamp", 0),
            )
            self._last_ticks[msg.symbolId] = tick
            await self._tick_bus.put(tick)

        # Embedded trendbars in spot event
        for tb in msg.trendbar:
            low = self.price_from_relative(tb.low, digits)
            bar = BarClose(
                symbol_id=msg.symbolId,
                symbol_name=name,
                period=_period_name(tb.period),
                open=self.price_from_relative(tb.low + tb.deltaOpen, digits),
                high=self.price_from_relative(tb.low + tb.deltaHigh, digits),
                low=low,
                close=self.price_from_relative(tb.low + tb.deltaClose, digits),
                volume=self.volume_from_api(tb.volume),
                timestamp_ms=getattr(tb, "utcTimestampInMinutes", 0) * 60_000,
            )
            await self._bar_bus.put(bar)

    async def _on_depth_event(self, msg: Any) -> None:
        """Handle ProtoOADepthEvent."""
        update = DepthUpdate(
            symbol_id=msg.symbolId,
            new_quotes=list(msg.newQuotes),
            deleted_ids=list(msg.deletedQuotes),
        )
        await self._depth_bus.put(update)

    async def _on_symbol_changed(self, msg: Any) -> None:
        """Handle ProtoOASymbolChangedEvent — update cached metadata."""
        symbol_id = getattr(msg, "symbolId", None)
        if symbol_id is None or symbol_id not in self._symbols:
            return
        info = self._symbols[symbol_id]
        # Update fields that may have changed
        digits = getattr(msg, "digits", info.digits)
        if digits != info.digits:
            info.digits = digits
            info.pip_size = 10 ** (-digits)
        raw_min_vol = getattr(msg, "minVolume", 0)
        raw_step_vol = getattr(msg, "stepVolume", 0)
        raw_lot_size = getattr(msg, "lotSize", 0)
        if raw_min_vol:
            info.min_volume = raw_min_vol // 100
        if raw_step_vol:
            info.step_volume = raw_step_vol // 100
        if raw_lot_size:
            info.lot_size = raw_lot_size // 100
        logger.info("Symbol %s metadata updated", info.name)

    # ── Re-subscription (called after reconnect) ───────────────────────────

    async def replay_subscriptions(self, account_id: int) -> None:
        """Re-subscribe to all active subscriptions after reconnect."""
        if self._subscribed_spots:
            await self.subscribe_spots(account_id, list(self._subscribed_spots))
        for symbol_id, periods in self._subscribed_bars.items():
            for period in periods:
                await self.subscribe_live_bars(account_id, symbol_id, period)
        for symbol_id in self._subscribed_depth:
            await self.subscribe_depth(account_id, symbol_id)


# ── Helpers ────────────────────────────────────────────────────────────────


def _payload_type(name: str) -> int:
    """Get the payloadType integer for a given message class name."""
    return _msg_class(name)().payloadType


_MSG_MESSAGES = None
_MSG_MODEL = None


def _msg_class(name: str) -> Any:
    """Dynamically import a cTrader message class."""
    global _MSG_MESSAGES, _MSG_MODEL
    if _MSG_MESSAGES is None:
        import importlib
        _MSG_MESSAGES = importlib.import_module(
            "ctrader_open_api.messages.OpenApiMessages_pb2"
        )
        _MSG_MODEL = importlib.import_module(
            "ctrader_open_api.messages.OpenApiModelMessages_pb2"
        )
    # ProtoOATrendbarPeriod lives in OpenApiModelMessages_pb2
    mod = _MSG_MODEL if name == "ProtoOATrendbarPeriod" else _MSG_MESSAGES
    return getattr(mod, name)


_import = _msg_class


def _period_name(period_value: int) -> str:
    """Convert ProtoOATrendbarPeriod enum value to string like 'M1', 'H1'."""
    return _msg_class("ProtoOATrendbarPeriod").Name(period_value)
