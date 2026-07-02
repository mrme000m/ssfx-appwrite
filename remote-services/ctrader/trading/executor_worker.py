"""Wrap TradeExecutor per (grant_id, ctid) and route execution events."""
from __future__ import annotations

import logging
from typing import Any

from ctrader_client.market_data import ExecutionEvent
from ssfx_parser import Direction, OrderType, SignalStatus, SignalType, TradeSignal
from ssfx_trader.backends.ctrader import CTraderBackend
from ssfx_trader.config import AccountConfig
from ssfx_trader.executor import TradeExecutor
from ssfx_trader.symbol_resolver import SymbolResolver

from .event_relay import EventRelay
from .models import ExecutionResponse, SignalRequest
from .session_manager import SessionManager

logger = logging.getLogger(__name__)


class ExecutorWorker:
    """One worker per account; executes signals and captures events."""

    def __init__(
        self,
        account_config: AccountConfig,
        session_manager: SessionManager,
        event_relay: EventRelay,
        data_service_base_url: str = "",
        data_service_api_key: str = "",
    ):
        self._cfg = account_config
        self._session_manager = session_manager
        self._event_relay = event_relay
        self._data_service_base_url = data_service_base_url
        self._data_service_api_key = data_service_api_key
        self._executor: TradeExecutor | None = None

    async def _ensure_executor(self) -> TradeExecutor:
        if self._executor:
            return self._executor

        session = await self._session_manager.get_session(
            self._cfg.ctrader.grant_id,
            self._cfg.ctrader.account_id,
            use_live=self._cfg.use_live,
        )
        backend = CTraderBackend(
            account_name=self._cfg.name,
            ctrader_config=self._cfg.ctrader,
            session=session,
        )
        await backend.connect()

        # Forward cTrader execution events to Appwrite.
        if session:
            session.event_bus.subscribe(
                ExecutionEvent,
                self._on_execution_event,
            )

        self._executor = TradeExecutor(
            follower_id=self._cfg.name,
            backend=backend,
            resolver=SymbolResolver(),
            signal_store=_NoOpSignalStore(),
            account_store=_NoOpAccountStore(),
            trading=self._cfg.trading,
            market_context_client=None,  # optional: wire DataServiceClient
        )
        return self._executor

    async def _on_execution_event(self, event: ExecutionEvent) -> None:
        try:
            await self._event_relay.emit_execution_event(
                self._cfg.ctrader.grant_id,
                self._cfg.ctrader.account_id,
                event,
            )
        except Exception as exc:
            logger.warning("Execution event relay failed: %s", exc)

    async def execute(self, request: SignalRequest) -> ExecutionResponse:
        try:
            executor = await self._ensure_executor()
        except Exception as exc:
            logger.error("Failed to initialize executor for %s: %s", self._cfg.name, exc)
            return ExecutionResponse(
                status="error",
                follower_id=self._cfg.name,
                message=f"Session setup failed: {exc}",
            )

        try:
            signal = self._to_trade_signal(request)
            result = await executor.execute_signal(signal)

            await self._event_relay.emit(
                request.grant_id,
                request.ctid_trader_account_id,
                "signal_processed",
                {
                    "signal_type": request.signal_type,
                    "symbol": request.symbol,
                    "direction": request.direction,
                    "result": result,
                },
            )
            return ExecutionResponse(
                status="ok",
                follower_id=self._cfg.name,
                message="Signal processed",
                details=result,
            )
        except Exception as exc:
            logger.exception("Execution failed for %s: %s", self._cfg.name, exc)
            await self._event_relay.emit(
                request.grant_id,
                request.ctid_trader_account_id,
                "execution_error",
                {"error": str(exc), "signal_type": request.signal_type},
            )
            return ExecutionResponse(
                status="error",
                follower_id=self._cfg.name,
                message=str(exc),
            )

    def _to_trade_signal(self, request: SignalRequest) -> TradeSignal:
        return TradeSignal(
            chat_id="ctrader-trading",
            message_id=0,
            reply_to_message_id=request.reply_to_message_id,
            timestamp_ms=0,
            raw_text=request.raw_text or f"{request.direction} {request.symbol}",
            symbol=request.symbol,
            direction=Direction(request.direction.upper()) if request.direction else None,
            signal_type=SignalType(request.signal_type),
            order_type=OrderType(request.order_type.upper()) if request.order_type else None,
            entry_price=request.entry_price,
            sl=request.sl,
            tp1=request.tp1,
            tp2=request.tp2,
            tp3=request.tp3,
            tp_hit_number=request.tp_hit_number,
            close_percentage=request.close_percentage,
            status=SignalStatus.PENDING,
            parser_used="ctrader-trading",
        )


class _NoOpSignalStore:
    async def save_signal(self, signal: TradeSignal) -> None:
        pass

    async def update_signal_status(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def record_trade(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def update_signal_entry(self, *args: Any, **kwargs: Any) -> None:
        pass

    def get_signal(self, *args: Any, **kwargs: Any) -> TradeSignal | None:
        return None

    def get_active_signals(self, *args: Any, **kwargs: Any) -> list[TradeSignal]:
        return []


class _NoOpAccountStore:
    def log_execution(self, *args: Any, **kwargs: Any) -> None:
        pass

    def update_execution(self, *args: Any, **kwargs: Any) -> None:
        pass

    def mark_skipped(self, *args: Any, **kwargs: Any) -> None:
        pass

    def get_execution(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        return None

    def list_recent_executions(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []
