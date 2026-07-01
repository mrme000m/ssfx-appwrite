"""Execution Manager — order submission, rate limiting, position management."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# cTrader API encodes volume in 1/100th of a base-currency unit.
# SymbolInfo stores lot_size in real units; multiply by this when sending to API.
_VOLUME_CENTS = 100

# Map short TIF codes to protobuf enum names accepted by ProtoOATimeInForce.Value().
_TIF_MAP: dict[str, str] = {
    "GTC": "GOOD_TILL_CANCEL",
    "DAY": "GOOD_TILL_DATE",
    "IOC": "IMMEDIATE_OR_CANCEL",
    "FOK": "FILL_OR_KILL",
    "MOO": "MARKET_ON_OPEN",
}


# ── Order Request ──────────────────────────────────────────────────────────


@dataclass
class OrderRequest:
    """A request to place, modify, or close an order."""

    account_id: int
    symbol_id: int
    order_type: str  # "MARKET" | "LIMIT" | "STOP" | "STOP_LIMIT"
    trade_side: str  # "BUY" | "SELL"
    volume_lots: float
    limit_price: float | None = None
    stop_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    client_order_id: str | None = None
    time_in_force: str | None = None  # "GTC" | "DAY" | "IOC" | "FOK" or full enum name
    label: str | None = None  # User-specified label (max 100 chars)
    comment: str | None = None  # User-specified comment (max 512 chars)


# ── Token Bucket Rate Limiter ─────────────────────────────────────────────


class TokenBucket:
    """Leaky-bucket rate limiter for async code."""

    def __init__(self, rate: float, capacity: float):
        """
        Args:
            rate: Tokens added per second.
            capacity: Maximum burst size.
        """
        self._rate = rate
        self._capacity = capacity
        self._tokens = capacity
        self._last_refill = time.monotonic()

    async def acquire(self) -> None:
        """Wait until a token is available, then consume it."""
        while True:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last_refill = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            await asyncio.sleep((1.0 - self._tokens) / self._rate)

    def try_acquire(self) -> bool:
        """Try to acquire a token without waiting."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


# ── Execution Manager ─────────────────────────────────────────────────────


class ExecutionManager:
    """Manages order submission, cancellation, and position closing.

    Enforces rate limits via token buckets and forwards requests through
    the protocol client. The demo limit is 500 orders/min (~8.33/s);
    we use a conservative 7/s. Non-historical requests are limited to 7/s.
    """

    # API limits: 500 orders/min => ~8.33/s; conservative at 7/s
    _order_limiter = TokenBucket(rate=7.0, capacity=10.0)
    # Non-historical requests: 50/s
    _request_limiter = TokenBucket(rate=45.0, capacity=50.0)

    def __init__(self, protocol: Any, market_data: Any = None):
        self._proto = protocol
        self._market_data = market_data
        self._active_orders: dict[int, dict] = {}  # order_id -> info

    async def submit(self, req: OrderRequest) -> Any:
        """Submit a new order. Returns the response Future."""
        await self._order_limiter.acquire()

        msg = self._build_new_order(req, self._market_data)
        fut = await self._proto.send(msg, client_msg_id=req.client_order_id)
        logger.info(
            "Order submitted: %s %s %.2f lots (symbol=%d)",
            req.trade_side,
            req.order_type,
            req.volume_lots,
            req.symbol_id,
        )
        return fut

    async def cancel(self, account_id: int, order_id: int) -> Any:
        """Cancel a pending order."""
        await self._order_limiter.acquire()

        msg = self._import("ProtoOACancelOrderReq")()
        msg.ctidTraderAccountId = account_id
        msg.orderId = order_id

        fut = await self._proto.send(msg)
        logger.info("Order cancelled: %d", order_id)
        return fut

    async def close_position(
        self,
        account_id: int,
        position_id: int,
        symbol_id: int,
        volume_lots: float | None = None,
    ) -> Any:
        """Close an existing position (partial or full).

        When volume_lots is None, queries the server for the position's
        current volume and closes it entirely.
        """
        await self._order_limiter.acquire()

        # Resolve volume if not provided
        if volume_lots is None:
            volume_lots = await self.get_position_volume(
                account_id, position_id, symbol_id
            )

        msg = self._import("ProtoOAClosePositionReq")()
        msg.ctidTraderAccountId = account_id
        msg.positionId = position_id
        lot_size = self._get_lot_size(symbol_id)
        msg.volume = int(volume_lots * lot_size * _VOLUME_CENTS)

        fut = await self._proto.send(msg)
        logger.info(
            "Position closed: id=%d lots=%.2f", position_id, volume_lots
        )
        return fut

    async def get_position_volume(
        self, account_id: int, position_id: int, symbol_id: int
    ) -> float:
        """Query the server for a position's volume via reconcile."""
        req = self._import("ProtoOAReconcileReq")()
        req.ctidTraderAccountId = account_id
        res = await (await self._proto.send(req))
        for pos in res.position:
            if pos.positionId == position_id:
                raw_volume = pos.tradeData.volume
                lot_size = self._get_lot_size(symbol_id)
                return raw_volume / (lot_size * _VOLUME_CENTS)
        raise ValueError(f"Position {position_id} not found on server")

    async def _get_position_volume(
        self, account_id: int, position_id: int, symbol_id: int
    ) -> float:
        """Internal alias for get_position_volume."""
        return await self.get_position_volume(account_id, position_id, symbol_id)

    async def amend_order(
        self,
        account_id: int,
        order_id: int,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        limit_price: float | None = None,
        stop_price: float | None = None,
    ) -> Any:
        """Amend SL/TP and/or price on an existing pending order.

        cTrader requires the order's existing price fields to be resent on
        amendment; passing only SL/TP for a LIMIT/STOP order is rejected.
        """
        await self._request_limiter.acquire()

        msg = self._import("ProtoOAAmendOrderReq")()
        msg.ctidTraderAccountId = account_id
        msg.orderId = order_id
        if stop_loss is not None:
            msg.stopLoss = stop_loss
        if take_profit is not None:
            msg.takeProfit = take_profit
        if limit_price is not None:
            msg.limitPrice = limit_price
        if stop_price is not None:
            msg.stopPrice = stop_price

        return await (await self._proto.send(msg))

    async def amend_order_price(
        self,
        account_id: int,
        order_id: int,
        limit_price: float | None = None,
        stop_price: float | None = None,
    ) -> Any:
        """Amend the price of a pending order.

        For LIMIT orders, set limit_price. For STOP orders, set stop_price.
        For STOP_LIMIT orders, set both.
        """
        await self._request_limiter.acquire()

        msg = self._import("ProtoOAAmendOrderReq")()
        msg.ctidTraderAccountId = account_id
        msg.orderId = order_id
        if limit_price is not None:
            msg.limitPrice = limit_price
        if stop_price is not None:
            msg.stopPrice = stop_price

        return await (await self._proto.send(msg))

    async def amend_position_sltp(
        self,
        account_id: int,
        position_id: int,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        trailing_stop_loss: bool | None = None,
    ) -> Any:
        """Amend SL/TP on an existing position.

        Uses ProtoOAAmendPositionSLTPReq (not ProtoOAAmendOrderReq which is
        for pending orders only).
        """
        await self._request_limiter.acquire()

        msg = self._import("ProtoOAAmendPositionSLTPReq")()
        msg.ctidTraderAccountId = account_id
        msg.positionId = position_id
        if stop_loss is not None:
            msg.stopLoss = stop_loss
        if take_profit is not None:
            msg.takeProfit = take_profit
        if trailing_stop_loss is not None:
            msg.trailingStopLoss = trailing_stop_loss

        return await (await self._proto.send(msg))

    def record_fill(
        self, order_id: int, symbol_id: int, lots: float, side: str
    ) -> None:
        """Track a filled order for position accounting."""
        self._active_orders[order_id] = {
            "symbol_id": symbol_id,
            "lots": lots,
            "side": side,
        }

    def record_close(self, order_id: int) -> None:
        """Remove a closed order from tracking."""
        self._active_orders.pop(order_id, None)

    async def get_expected_margin(
        self, account_id: int, symbol_id: int, volume_lots: float
    ) -> dict[str, float]:
        """Calculate expected margin for a potential order.

        Returns dict with 'buy_margin', 'sell_margin', 'money_digits'.
        """
        await self._request_limiter.acquire()

        lot_size = self._get_lot_size(symbol_id)
        volume = int(volume_lots * lot_size * _VOLUME_CENTS)

        res = await self._proto.get_expected_margin(account_id, symbol_id, volume)
        money_digits = getattr(res, "moneyDigits", 0) or 0
        divisor = 10 ** money_digits if money_digits else 100

        margins = getattr(res, "margin", [])
        result: dict[str, float] = {
            "money_digits": money_digits,
            "buy_margin": 0.0,
            "sell_margin": 0.0,
        }
        for m in margins:
            vol = getattr(m, "volume", 0)
            buy = getattr(m, "buyMargin", 0)
            sell = getattr(m, "sellMargin", 0)
            if vol == volume:
                result["buy_margin"] = buy / divisor
                result["sell_margin"] = sell / divisor
                break
        return result

    async def get_unrealized_pnl(self, account_id: int) -> float:
        """Get total unrealized P&L for all open positions."""
        await self._request_limiter.acquire()

        res = await self._proto.get_unrealized_pnl(account_id)
        money_digits = getattr(res, "moneyDigits", 0) or 0
        divisor = 10 ** money_digits if money_digits else 100
        pnl = getattr(res, "positionUnrealizedPnL", 0)
        return pnl / divisor

    def _get_lot_size(self, symbol_id: int) -> int:
        """Get the lot size for a symbol in base currency units.

        Falls back to 100,000 (standard Forex lot) if symbol info
        is not available. Crypto symbols (BTCUSD=100, ETHUSD=1000)
        are resolved from enriched symbol metadata.
        """
        if self._market_data is not None:
            info = self._market_data._symbols.get(symbol_id)
            if info and info.lot_size:
                return info.lot_size
        return 100_000

    @staticmethod
    def _build_new_order(req: OrderRequest, market_data: Any = None) -> Any:
        """Build a ProtoOANewOrderReq from an OrderRequest.

        Volume is sent in 1/100th of a base-currency unit per the cTrader API
        spec. SymbolInfo.lot_size is in real units, so we multiply by
        _VOLUME_CENTS (100) to convert to API volume.
        """
        from ctrader_open_api.messages.OpenApiMessages_pb2 import (
            ProtoOANewOrderReq,
        )
        from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
            ProtoOAOrderType,
            ProtoOATimeInForce,
            ProtoOATradeSide,
        )

        msg = ProtoOANewOrderReq()
        msg.ctidTraderAccountId = req.account_id
        msg.symbolId = req.symbol_id
        msg.orderType = ProtoOAOrderType.Value(req.order_type)
        msg.tradeSide = ProtoOATradeSide.Value(req.trade_side)

        lot_size = 100_000
        if market_data is not None:
            info = market_data._symbols.get(req.symbol_id)
            if info and info.lot_size:
                lot_size = info.lot_size
        msg.volume = int(req.volume_lots * lot_size * _VOLUME_CENTS)

        if req.limit_price is not None:
            msg.limitPrice = req.limit_price
        if req.stop_price is not None:
            msg.stopPrice = req.stop_price
        if req.stop_loss is not None:
            msg.stopLoss = req.stop_loss
        if req.take_profit is not None:
            msg.takeProfit = req.take_profit
        if req.time_in_force is not None:
            tif_name = _TIF_MAP.get(req.time_in_force.upper(), req.time_in_force)
            msg.timeInForce = ProtoOATimeInForce.Value(tif_name)
        elif req.order_type == "MARKET":
            msg.timeInForce = ProtoOATimeInForce.Value("IMMEDIATE_OR_CANCEL")
        if req.client_order_id is not None:
            msg.clientOrderId = req.client_order_id
        if req.label is not None:
            msg.label = req.label
        if req.comment is not None:
            msg.comment = req.comment

        return msg

    @staticmethod
    def _import(name: str) -> Any:
        import importlib

        mod = importlib.import_module(
            "ctrader_open_api.messages.OpenApiMessages_pb2"
        )
        return getattr(mod, name)
