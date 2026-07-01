"""Real cTrader Open API execution backend."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ctrader_client import CTraderSession, OrderRequest, TransportType
from ssfx_parser import TradeSignal

from ..config import DEFAULT_CTRADER_BROKER_URL, CTraderConfig

logger = logging.getLogger(__name__)


def _extract_execution_ids(response: Any) -> tuple[int | None, int | None]:
    """Extract order_id and position_id from a cTrader execution response."""
    order_id: int | None = None
    position_id: int | None = None

    order = getattr(response, "order", None)
    if order is not None:
        order_id = getattr(order, "orderId", None)
    if order_id is None:
        order_id = getattr(response, "orderId", None)

    position = getattr(response, "position", None)
    if position is not None:
        position_id = getattr(position, "positionId", None)
    if position_id is None:
        position_id = getattr(response, "positionId", None)

    return order_id, position_id


def _extract_executed_price(response: Any) -> float | None:
    """Extract the fill/execution price from a cTrader response."""
    position = getattr(response, "position", None)
    if position is not None:
        price = getattr(position, "tradeData", None)
        if price is not None:
            return getattr(price, "entryPrice", None)
    order = getattr(response, "order", None)
    if order is not None:
        return getattr(order, "requestedPrice", None)
    return None


class CTraderBackend:
    """Real cTrader execution backend using the ctrader_client package."""

    def __init__(
        self,
        account_name: str,
        ctrader_config: CTraderConfig,
        session: CTraderSession | None = None,
    ):
        self._account_name = account_name
        self._cfg = ctrader_config
        self._session: CTraderSession | None = session
        self._owns_session = session is None
        self._connected = False
        self._position_symbols: dict[int, int] = {}

    async def connect(self) -> None:
        if self._connected:
            return

        if self._session is not None and self._session._transport.is_connected:
            self._connected = True
            logger.info("[%s] Using provided cTrader session", self._account_name)
            return

        kwargs: dict[str, Any] = {
            "client_id": self._cfg.client_id,
            "client_secret": self._cfg.client_secret,
            "access_token": "",
            "refresh_token": "",
            "account_id": self._cfg.account_id,
            "use_live": self._cfg.host_type == "live",
            "transport_type": TransportType.TCP,
        }

        grant_id = self._cfg.grant_id
        broker_url = self._cfg.broker_url or (
            DEFAULT_CTRADER_BROKER_URL if grant_id else ""
        )
        if broker_url and grant_id:
            kwargs["broker_url"] = broker_url
            kwargs["grant_id"] = grant_id
            logger.info(
                "[%s] Connecting to cTrader using broker-delegated OAuth (%s)",
                self._account_name,
                broker_url,
            )
        else:
            logger.info(
                "[%s] Connecting to cTrader using direct OAuth", self._account_name
            )

        self._session = CTraderSession(**kwargs)
        await self._session.start()
        self._connected = True
        logger.info("[%s] cTrader session started", self._account_name)

    async def close(self) -> None:
        if self._session is not None and self._owns_session:
            await self._session.stop()
        self._session = None
        self._connected = False

    async def get_account_summary(self) -> dict[str, float]:
        if self._session is None:
            return {"balance": 0.0, "equity": 0.0}
        trader = await self._session.protocol.get_trader(self._session.account_id)

        nested = getattr(trader, "trader", None)
        if nested is not None:
            nested_balance = getattr(nested, "balance", None)
            if isinstance(nested_balance, (int, float)) and not isinstance(nested_balance, bool):
                trader = nested

        raw_balance = getattr(trader, "balance", 0.0)
        raw_equity = getattr(trader, "equity", raw_balance)
        money_digits = getattr(trader, "moneyDigits", 0)
        divisor = 10 ** money_digits if money_digits else 100.0

        def _normalize(value: Any) -> float:
            if isinstance(value, int) and not isinstance(value, bool):
                return value / divisor
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0

        return {"balance": _normalize(raw_balance), "equity": _normalize(raw_equity)}

    async def open_position(
        self, signal: TradeSignal, volume_lots: float
    ) -> dict[str, Any]:
        if self._session is None:
            return {"accepted": False, "error": "not connected"}
        if not signal.symbol or not signal.direction:
            return {"accepted": False, "error": "missing symbol or direction"}

        symbol_id = self._resolve_symbol_id(signal.symbol)
        if symbol_id is None:
            return {"accepted": False, "error": f"symbol {signal.symbol} not found"}

        order_type = signal.order_type.value if signal.order_type else "MARKET"

        # cTrader MARKET orders cannot carry SL/TP directly; attach after fill.
        sl = signal.sl_float
        tp = signal.tp1
        market_needs_sltp_amend = order_type == "MARKET" and (sl is not None or tp is not None)

        req = OrderRequest(
            account_id=self._session.account_id,
            symbol_id=symbol_id,
            order_type=order_type,
            trade_side=signal.direction.value,
            volume_lots=volume_lots,
            stop_loss=sl if not market_needs_sltp_amend else None,
            take_profit=tp if not market_needs_sltp_amend else None,
            client_order_id=f"ssfx_{signal.chat_id}_{signal.message_id}",
            label="SSFX",
            comment=f"TG msg {signal.message_id}",
        )

        if order_type in ("LIMIT", "STOP", "STOP_LIMIT") and signal.entry_price is not None:
            if order_type == "LIMIT":
                req.limit_price = signal.entry_price
            elif order_type == "STOP":
                req.stop_price = signal.entry_price
            else:  # STOP_LIMIT
                req.stop_price = signal.entry_price
                req.limit_price = signal.entry_price

        try:
            fut = await self._session.execution.submit(req)
            response = await fut
        except Exception as exc:
            logger.error("[%s] Order submission failed: %s", self._account_name, exc)
            return {"accepted": False, "error": str(exc)}

        error_code = getattr(response, "errorCode", None)
        if error_code:
            return {"accepted": False, "error": f"cTrader error: {error_code}"}

        order_id, position_id = _extract_execution_ids(response)
        executed_price = _extract_executed_price(response)
        if position_id is not None:
            self._position_symbols[position_id] = symbol_id

        if market_needs_sltp_amend and position_id:
            try:
                await self._amend_position_sltp_with_retry(position_id, sl, tp)
            except Exception as exc:
                logger.warning(
                    "[%s] Could not attach SL/TP to market position %d: %s",
                    self._account_name,
                    position_id,
                    exc,
                )

        return {
            "accepted": True,
            "order_id": order_id,
            "position_id": position_id,
            "executed_price": executed_price,
            "volume_lots": volume_lots,
        }

    async def _amend_position_sltp_with_retry(
        self,
        position_id: int,
        stop_loss: float | None,
        take_profit: float | None,
        max_retries: int = 5,
        base_delay: float = 0.3,
    ) -> None:
        last_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                await self._session.execution.amend_position_sltp(
                    self._session.account_id,
                    position_id,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                )
                logger.info(
                    "[%s] Attached SL/TP to market position %d (SL=%s, TP=%s)",
                    self._account_name,
                    position_id,
                    stop_loss,
                    take_profit,
                )
                return
            except Exception as exc:
                last_exc = exc
                if "POSITION_NOT_OPEN" in str(exc) and attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    await asyncio.sleep(delay)
                    continue
                raise
        if last_exc:
            raise last_exc

    async def close_position(
        self, position_id: int, volume_lots: float | None
    ) -> dict[str, Any]:
        if self._session is None:
            return {"accepted": False, "error": "not connected"}
        symbol_id = self._position_symbols.get(position_id)
        if symbol_id is None:
            return {"accepted": False, "error": "symbol_id unknown for position"}
        try:
            fut = await self._session.execution.close_position(
                self._session.account_id,
                position_id,
                symbol_id=symbol_id,
                volume_lots=volume_lots,
            )
            await fut
            if volume_lots is None:
                self._position_symbols.pop(position_id, None)
            return {"accepted": True, "closed_volume": volume_lots}
        except Exception as exc:
            logger.error("[%s] Close position failed: %s", self._account_name, exc)
            return {"accepted": False, "error": str(exc)}

    async def amend_position_sltp(
        self, position_id: int, stop_loss: float | None, take_profit: float | None
    ) -> dict[str, Any]:
        if self._session is None:
            return {"accepted": False, "error": "not connected"}
        try:
            await self._session.execution.amend_position_sltp(
                self._session.account_id,
                position_id,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )
            return {"accepted": True}
        except Exception as exc:
            logger.error("[%s] Amend SL/TP failed: %s", self._account_name, exc)
            return {"accepted": False, "error": str(exc)}

    async def cancel_order(self, order_id: int) -> dict[str, Any]:
        if self._session is None:
            return {"accepted": False, "error": "not connected"}
        try:
            fut = await self._session.execution.cancel(
                self._session.account_id, order_id
            )
            await fut
            return {"accepted": True}
        except Exception as exc:
            logger.error("[%s] Cancel order failed: %s", self._account_name, exc)
            return {"accepted": False, "error": str(exc)}

    async def get_open_position_ids(self) -> set[int]:
        if self._session is None:
            return set()
        try:
            rec = await self._session.protocol.reconcile(self._session.account_id)
            return {p.positionId for p in getattr(rec, "position", [])}
        except Exception as exc:
            logger.warning("[%s] Reconcile failed: %s", self._account_name, exc)
            return set()

    def _resolve_symbol_id(self, symbol: str) -> int | None:
        """Resolve a symbol name to cTrader symbolId using the loaded symbol map."""
        if self._session is None:
            return None
        market_data = self._session.market_data
        upper = symbol.upper()

        # Try direct match on registered symbols.
        for sid, info in getattr(market_data, "_symbols", {}).items():
            if getattr(info, "name", "").upper() == upper:
                return sid

        # Try aliases.
        aliases = {
            "XAUUSD": "XAUUSD",
            "GOLD": "XAUUSD",
            "BTCUSD": "BTCUSD",
            "BTC": "BTCUSD",
            "ETHUSD": "ETHUSD",
            "ETH": "ETHUSD",
        }
        target = aliases.get(upper, upper)
        for sid, info in getattr(market_data, "_symbols", {}).items():
            if getattr(info, "name", "").upper() == target:
                return sid

        return None
