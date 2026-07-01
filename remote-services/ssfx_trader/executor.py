"""Trade executor — bridges parsed signals to execution backends."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import UTC, datetime
from typing import Any

from ssfx_parser import OrderType, SignalStatus, SignalType, TradeSignal

from .backends.base import ExecutionBackend
from .config import (
    EntryUpdateAction,
    OrderHandling,
    PerAccountTradingConfig,
    SlStrategy,
    TpStrategy,
)
from .market_context import DataServiceClient, MarketContext
from .stores.base import AccountStore, SignalStore
from .symbol_resolver import SymbolResolver
from .volume_resolver import VolumeResolver

logger = logging.getLogger(__name__)

_AGGRESSION_SCORE: dict[SignalType, int] = {
    SignalType.CANCEL: 100,
    SignalType.SL_HIT: 95,
    SignalType.CLOSE: 90,
    SignalType.CLOSE_HALF: 70,
    SignalType.CLOSE_PARTIAL: 65,
    SignalType.TP_HIT: 50,
    SignalType.SL_TO_ENTRY: 30,
    SignalType.RISK_HIT: 25,
    SignalType.RUNNING: 10,
    SignalType.ENTRY_UPDATE: 5,
}

_CIRCUIT_BREAKER_FAILURES = 5
_CIRCUIT_RESET_SECONDS = 300


async def _retry_async(
    coro_fn,
    max_retries: int = 2,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
):
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await coro_fn()
        except Exception as exc:
            last_exc = exc
            if attempt >= max_retries:
                break
            delay = min(base_delay * (2**attempt), max_delay)
            delay = delay * (0.5 + random.random())
            logger.warning("Retry attempt %d/%d after %.2fs: %s", attempt + 1, max_retries, delay, exc)
            await asyncio.sleep(delay)
    raise last_exc


def _signal_close_pct(signal: TradeSignal, trading: PerAccountTradingConfig) -> float:
    if signal.signal_type == SignalType.CLOSE:
        return 100.0
    if signal.signal_type == SignalType.CLOSE_HALF:
        return signal.close_percentage or trading.partial_close.on_close_half_pct
    if signal.signal_type == SignalType.CLOSE_PARTIAL:
        return signal.close_percentage or 50.0
    if signal.signal_type == SignalType.TP_HIT and signal.tp_hit_number:
        return trading.partial_close.get_for_tp(signal.tp_hit_number)
    if signal.signal_type in (SignalType.SL_HIT, SignalType.CANCEL):
        return 100.0
    return 0.0


class _UpdateBuffer:
    def __init__(self, aggregation_ms: int | None, executor: "TradeExecutor"):
        self._aggregation_ms = aggregation_ms
        self._executor = executor
        self._buffer: dict[str, list[TradeSignal]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def is_enabled(self) -> bool:
        return self._aggregation_ms is not None and self._aggregation_ms > 0

    def has_pending(self) -> bool:
        return bool(self._buffer)

    async def flush_all(self) -> None:
        keys = list(self._buffer.keys())
        for key in keys:
            if key in self._tasks:
                self._tasks[key].cancel()
                self._tasks.pop(key, None)
            await self._flush(key)

    def add(self, key: str, signal: TradeSignal) -> None:
        if key not in self._buffer:
            self._buffer[key] = []
        self._buffer[key].append(signal)
        if key in self._tasks:
            self._tasks[key].cancel()
        self._tasks[key] = asyncio.create_task(self._flush_after(key))

    async def _flush_after(self, key: str) -> None:
        await asyncio.sleep(self._aggregation_ms / 1000.0)
        await self._flush(key)

    async def _flush(self, key: str) -> None:
        signals = self._buffer.pop(key, [])
        self._tasks.pop(key, None)
        if not signals:
            return
        trading = self._executor._trading

        def _score(s: TradeSignal) -> tuple[float, int]:
            return (_signal_close_pct(s, trading), _AGGRESSION_SCORE.get(s.signal_type, 0))

        chosen = max(signals, key=_score)
        await self._executor._execute_follow_up_signal(chosen)


class TradeExecutor:
    """Executes trading signals on an execution backend and manages open positions."""

    def __init__(
        self,
        follower_id: str,
        backend: ExecutionBackend,
        resolver: SymbolResolver,
        signal_store: SignalStore,
        account_store: AccountStore,
        trading: PerAccountTradingConfig | None = None,
        volume_resolver: VolumeResolver | None = None,
        market_context_client: DataServiceClient | None = None,
    ):
        self._follower_id = follower_id
        self._backend = backend
        self._resolver = resolver
        self._signal_store = signal_store
        self._account_store = account_store
        self._trading = trading or PerAccountTradingConfig()
        self._volume_resolver = volume_resolver
        self._market_context_client = market_context_client
        self._active_positions: dict[str, dict[str, Any]] = {}
        self._update_buffer = _UpdateBuffer(self._trading.update_aggregation_ms, self)
        self._consecutive_api_failures = 0
        self._api_circuit_open = False
        self._circuit_opened_at: float = 0.0

    def update_config(self, trading: PerAccountTradingConfig) -> None:
        if self._update_buffer.has_pending():
            asyncio.create_task(self._update_buffer.flush_all())
        self._trading = trading
        self._update_buffer = _UpdateBuffer(self._trading.update_aggregation_ms, self)
        if self._volume_resolver is not None:
            self._volume_resolver.update_config(trading)
        logger.info("[%s] TradeExecutor config updated", self._follower_id)

    async def execute_signal(self, signal: TradeSignal) -> dict[str, Any]:
        try:
            self._check_circuit_recovery()
            await self.check_stale_positions()

            if signal.signal_type == SignalType.NEW:
                return await self._execute_new_signal(signal)

            if self._update_buffer.is_enabled() and signal.signal_type in _AGGRESSION_SCORE:
                original = self._find_original_signal(signal)
                key = (
                    f"{original.chat_id}:{original.message_id}"
                    if original
                    else f"{signal.chat_id}:{signal.reply_to_message_id}"
                )
                if original and key:
                    self._update_buffer.add(key, signal)
                    return {
                        "accepted": True,
                        "code": "buffered",
                        "reason": f"buffered for {self._trading.update_aggregation_ms}ms",
                    }

            return await self._execute_follow_up_signal(signal)
        except Exception as exc:
            logger.error("[%s] Error executing signal: %s", self._follower_id, exc, exc_info=True)
            self._account_store.update_execution(
                self._follower_id,
                signal.chat_id or "",
                signal.message_id or 0,
                signal_type=signal.signal_type.value,
                status="failed",
                error=str(exc),
            )
            return {"accepted": False, "code": "error", "reason": str(exc)}

    async def _execute_follow_up_signal(self, signal: TradeSignal) -> dict[str, Any]:
        if signal.signal_type == SignalType.TP_HIT:
            return await self._handle_tp_hit(signal)
        if signal.signal_type == SignalType.SL_HIT:
            return await self._handle_sl_hit(signal)
        if signal.signal_type == SignalType.CLOSE_HALF:
            return await self._handle_close(signal, self._trading.partial_close.on_close_half_pct)
        if signal.signal_type == SignalType.CLOSE_PARTIAL:
            pct = signal.close_percentage or self._trading.update_actions.close_partial_override_pct or 50.0
            return await self._handle_close(signal, pct)
        if signal.signal_type == SignalType.CLOSE:
            return await self._handle_close(signal, 100.0)
        if signal.signal_type == SignalType.CANCEL:
            return await self._handle_cancel(signal)
        if signal.signal_type == SignalType.SL_TO_ENTRY:
            return await self._handle_sl_to_entry(signal)
        if signal.signal_type == SignalType.ENTRY_UPDATE:
            return await self._handle_entry_update(signal)
        if signal.signal_type == SignalType.RUNNING:
            return {"accepted": False, "code": "info", "reason": "status update only"}
        return {"accepted": False, "code": "ignore", "reason": f"unhandled signal type: {signal.signal_type}"}

    async def _check_market_context(
        self, signal: TradeSignal
    ) -> tuple[bool, str, str, MarketContext | None]:
        """Return (passed, code, reason, context)."""
        if self._market_context_client is None:
            return True, "", "", None

        mode = self._trading.market_context_mode
        if mode == "ignore":
            return True, "", "", None

        context = await self._market_context_client.get_context(signal.symbol or "")
        if context.error:
            if mode == "strict":
                return False, "market_context_unavailable", context.error, context
            return True, "market_context_warn", context.error, context

        if context.is_stale(30.0):
            reason = "market data is stale"
            if mode == "strict":
                return False, "stale_market_data", reason, context
            logger.warning("[%s] %s", self._follower_id, reason)

        if not context.quality_ok():
            reason = "market data quality check failed"
            if mode == "strict":
                return False, "data_quality_failed", reason, context
            logger.warning("[%s] %s", self._follower_id, reason)

        if signal.entry_price is not None and signal.sl_float is not None:
            sl_distance = abs(signal.entry_price - signal.sl_float)
            if (
                self._trading.max_sl_distance_pips is not None
                and sl_distance > self._trading.max_sl_distance_pips
            ):
                reason = f"SL distance {sl_distance:.1f} pips exceeds max {self._trading.max_sl_distance_pips}"
                return False, "sl_distance_exceeded", reason, context

        spread = context.spread
        if spread is not None and self._trading.max_spread_pips is not None:
            spread_pips = spread
            if spread_pips > self._trading.max_spread_pips:
                reason = f"spread {spread_pips:.1f} pips exceeds max {self._trading.max_spread_pips}"
                if mode == "strict":
                    return False, "spread_too_high", reason, context
                logger.warning("[%s] %s", self._follower_id, reason)

        # Risk guardrails require account balance; leave to volume/risk resolver.
        return True, "", "", context

    async def _execute_new_signal(self, signal: TradeSignal) -> dict[str, Any]:
        if not signal.symbol or not signal.direction:
            return {"accepted": False, "code": "no_symbol_direction", "reason": "missing symbol or direction"}

        passed, code, reason, _context = await self._check_market_context(signal)
        if not passed:
            self._account_store.mark_skipped(
                self._follower_id,
                signal.chat_id or "",
                signal.message_id or 0,
                reason=f"{code}: {reason}",
            )
            return {"accepted": False, "code": code, "reason": reason}

        symbol_id = self._resolver.get_symbol_id(signal.symbol)
        if symbol_id is None:
            return {"accepted": False, "code": "symbol_not_found", "reason": f"symbol {signal.symbol} not found"}

        if self._api_circuit_open:
            return {"accepted": False, "code": "circuit_breaker_open", "reason": "API circuit breaker is open"}

        active_count = len(self._active_positions)
        if active_count >= self._trading.max_positions:
            return {"accepted": False, "code": "max_positions", "reason": f"max {self._trading.max_positions} positions reached"}

        sym_active = self._count_active_positions_for_symbol(signal.symbol)
        if sym_active >= self._trading.max_positions_per_symbol:
            return {"accepted": False, "code": "max_positions_per_symbol", "reason": f"max {self._trading.max_positions_per_symbol} {signal.symbol} positions reached"}

        if not self._trading.allow_same_symbol_add:
            if self._has_same_symbol_direction(signal.symbol, signal.direction):
                return {"accepted": False, "code": "same_symbol_direction", "reason": f"already have {signal.direction.value} {signal.symbol}"}

        if not self._trading.allow_opposite_direction:
            if self._has_opposite_direction(signal.symbol, signal.direction):
                return {"accepted": False, "code": "opposite_direction", "reason": f"opposite {signal.symbol} position already open"}

        volume_lots = self._trading.default_volume
        if self._volume_resolver is not None:
            try:
                volume_lots = await self._volume_resolver.resolve_volume(signal)
            except Exception as exc:
                logger.warning("[%s] Volume resolution failed: %s", self._follower_id, exc)

        order_type = signal.order_type or OrderType.MARKET
        if self._trading.order_handling == OrderHandling.MARKET_ONLY:
            order_type = OrderType.MARKET
        elif self._trading.order_handling == OrderHandling.LIMIT_ONLY:
            if order_type != OrderType.LIMIT:
                return {"accepted": False, "code": "limit_only", "reason": "order_handling is limit_only"}

        sl = signal.sl_float
        if self._trading.sl_strategy == SlStrategy.NO_SL:
            sl = None

        tp = signal.tp1
        if self._trading.tp_strategy == TpStrategy.NO_TP:
            tp = None

        try:
            result = await _retry_async(
                lambda: self._backend.open_position(signal, volume_lots),
                max_retries=2,
                base_delay=1.0,
                max_delay=5.0,
            )
        except Exception as exc:
            self._consecutive_api_failures += 1
            if self._consecutive_api_failures >= _CIRCUIT_BREAKER_FAILURES:
                self._api_circuit_open = True
                self._circuit_opened_at = time.time()
            error_msg = f"Backend error after retries: {exc}"
            self._account_store.update_execution(
                self._follower_id,
                signal.chat_id or "",
                signal.message_id or 0,
                signal_type=signal.signal_type.value,
                status="rejected",
                error=error_msg,
            )
            return {"accepted": False, "code": "api_error", "reason": error_msg}

        if not result.get("accepted"):
            error_msg = result.get("error", "backend rejected open")
            self._account_store.update_execution(
                self._follower_id,
                signal.chat_id or "",
                signal.message_id or 0,
                signal_type=signal.signal_type.value,
                status="rejected",
                error=error_msg,
            )
            return {"accepted": False, "code": "api_error", "reason": error_msg}

        self._consecutive_api_failures = 0
        self._api_circuit_open = False

        order_id = result.get("order_id")
        position_id = result.get("position_id")
        executed_price = result.get("executed_price")

        self._signal_store.update_signal_status(
            signal.chat_id or "",
            signal.message_id or 0,
            SignalStatus.EXECUTED.value,
            order_id=order_id,
            position_id=position_id,
            executed_price=executed_price,
            volume=volume_lots,
        )

        self._account_store.update_execution(
            self._follower_id,
            signal.chat_id or "",
            signal.message_id or 0,
            signal_type=signal.signal_type.value,
            status="executed",
            order_id=order_id,
            position_id=position_id,
            executed_price=executed_price,
            volume=volume_lots,
            original_volume_lots=volume_lots,
        )

        key = f"{signal.chat_id}:{signal.message_id}"
        self._active_positions[key] = {
            "signal": signal,
            "order_id": order_id,
            "position_id": position_id,
            "symbol_id": symbol_id,
            "volume_lots": volume_lots,
            "remaining_volume_lots": volume_lots,
        }

        if (
            self._trading.sl_strategy == SlStrategy.TRAILING_AT_BREAKEVEN
            and position_id
            and signal.entry_price is not None
        ):
            try:
                await self._backend.amend_position_sltp(position_id, stop_loss=signal.entry_price)
            except Exception as exc:
                logger.warning("[%s] Could not move SL to breakeven: %s", self._follower_id, exc)

        final_result = {
            "accepted": True,
            "code": "order_placed",
            "reason": f"{signal.direction.value} {signal.symbol} {volume_lots} lots",
            "execution": {
                "order_id": order_id,
                "position_id": position_id,
                "order_type": order_type.value,
                "volume": volume_lots,
                "sl": sl,
                "tp": tp,
            },
        }
        self._signal_store.record_trade(signal, final_result)
        logger.info("[%s] Executed signal: %s", self._follower_id, final_result["reason"])
        return final_result

    async def _handle_tp_hit(self, signal: TradeSignal) -> dict[str, Any]:
        tp_num = signal.tp_hit_number
        if tp_num is None:
            return {"accepted": False, "code": "no_tp_number", "reason": "could not determine which TP was hit"}

        original = self._find_original_signal(signal)
        if original is None:
            return {"accepted": False, "code": "no_original", "reason": "could not find original signal for TP hit"}

        key = f"{original.chat_id}:{original.message_id}"
        pos = self._active_positions.get(key)
        if pos is None:
            return {"accepted": False, "code": "no_position", "reason": "no active position for this signal"}

        close_pct = signal.close_percentage if signal.close_percentage is not None else self._trading.partial_close.get_for_tp(tp_num)
        return await self._close_position(pos, volume_pct=close_pct, reason=f"TP{tp_num} hit")

    async def _handle_sl_hit(self, signal: TradeSignal) -> dict[str, Any]:
        original = self._find_original_signal(signal)
        if original:
            self._signal_store.update_signal_status(
                original.chat_id or "", original.message_id or 0, SignalStatus.CLOSED.value
            )
            self._account_store.update_execution(
                self._follower_id,
                original.chat_id or "",
                original.message_id or 0,
                signal_type=signal.signal_type.value,
                status="closed",
            )
            key = f"{original.chat_id}:{original.message_id}"
            self._active_positions.pop(key, None)
        return {"accepted": True, "code": "sl_hit", "reason": "position closed by SL"}

    async def _handle_close(self, signal: TradeSignal, close_pct: float) -> dict[str, Any]:
        original = self._find_original_signal(signal)
        if original is None:
            return {"accepted": False, "code": "no_original", "reason": "could not find original signal"}

        key = f"{original.chat_id}:{original.message_id}"
        pos = self._active_positions.get(key)
        if pos is None:
            return {"accepted": False, "code": "no_position", "reason": "no active position"}

        return await self._close_position(pos, volume_pct=close_pct, reason="close signal")

    async def _handle_cancel(self, signal: TradeSignal) -> dict[str, Any]:
        original = self._find_original_signal(signal)
        if original is None:
            return {"accepted": False, "code": "no_original", "reason": "could not find original signal"}

        key = f"{original.chat_id}:{original.message_id}"
        pos = self._active_positions.pop(key, None)
        order_id = pos.get("order_id") if pos else None
        if order_id:
            try:
                await self._backend.cancel_order(order_id)
            except Exception as exc:
                logger.warning("[%s] Cancel order failed: %s", self._follower_id, exc)

        self._signal_store.update_signal_status(
            original.chat_id or "", original.message_id or 0, SignalStatus.CLOSED.value
        )
        self._account_store.update_execution(
            self._follower_id,
            original.chat_id or "",
            original.message_id or 0,
            signal_type=signal.signal_type.value,
            status="closed",
        )
        return {"accepted": True, "code": "cancelled", "reason": "order cancelled"}

    async def _handle_sl_to_entry(self, signal: TradeSignal) -> dict[str, Any]:
        original = self._find_original_signal(signal)
        if original is None:
            return {"accepted": False, "code": "no_original", "reason": "could not find original signal"}

        key = f"{original.chat_id}:{original.message_id}"
        pos = self._active_positions.get(key)
        if pos is None:
            return {"accepted": False, "code": "no_position", "reason": "no active position"}

        position_id = pos.get("position_id")
        entry_price = original.entry_price
        if entry_price is None:
            return {"accepted": False, "code": "no_entry", "reason": "no entry price to set SL to"}

        if position_id:
            try:
                await self._backend.amend_position_sltp(position_id, stop_loss=entry_price)
                return {"accepted": True, "code": "sl_to_entry", "reason": f"SL moved to {entry_price}"}
            except Exception as exc:
                return {"accepted": False, "code": "amend_failed", "reason": str(exc)}
        return {"accepted": False, "code": "no_position_id", "reason": "no position ID to amend"}

    async def _handle_entry_update(self, signal: TradeSignal) -> dict[str, Any]:
        original = self._find_original_signal(signal)
        if original is None:
            return {"accepted": False, "code": "no_original", "reason": "could not find original signal"}

        if signal.entry_price is None:
            return {"accepted": False, "code": "no_entry", "reason": "no entry price in update message"}

        action = self._trading.update_actions.entry_update_action

        if action == EntryUpdateAction.IGNORE:
            original.entry_price = signal.entry_price
            self._signal_store.update_signal_entry(
                original.chat_id or "", original.message_id or 0, signal.entry_price
            )
            return {"accepted": True, "code": "entry_updated", "reason": f"entry updated to {signal.entry_price}"}

        key = f"{original.chat_id}:{original.message_id}"
        pos = self._active_positions.get(key)

        if action == EntryUpdateAction.AMEND_PENDING:
            order_id = pos.get("order_id") if pos else None
            position_id = pos.get("position_id") if pos else None
            if order_id and not position_id:
                try:
                    await self._backend.amend_position_sltp(order_id, stop_loss=None, take_profit=None)
                    original.entry_price = signal.entry_price
                    self._signal_store.update_signal_entry(
                        original.chat_id or "", original.message_id or 0, signal.entry_price
                    )
                    return {"accepted": True, "code": "entry_amended", "reason": f"pending order amended to {signal.entry_price}"}
                except Exception as exc:
                    return {"accepted": False, "code": "amend_failed", "reason": str(exc)}
            original.entry_price = signal.entry_price
            self._signal_store.update_signal_entry(
                original.chat_id or "", original.message_id or 0, signal.entry_price
            )
            return {"accepted": True, "code": "entry_updated", "reason": f"entry updated to {signal.entry_price}"}

        if action == EntryUpdateAction.CLOSE_AND_REOPEN:
            if pos is None:
                return {"accepted": False, "code": "no_position", "reason": "no active position to close"}
            close_result = await self._close_position(pos, volume_pct=100.0, reason="entry update close-and-reopen")
            if not close_result.get("accepted"):
                return close_result
            new_signal = original.model_copy(update={"entry_price": signal.entry_price})
            return await self._execute_new_signal(new_signal)

        return {"accepted": False, "code": "unknown_action", "reason": f"unknown entry_update_action {action}"}

    async def _close_position(self, pos: dict[str, Any], volume_pct: float, reason: str) -> dict[str, Any]:
        position_id = pos.get("position_id")
        original_volume = pos.get("volume_lots", 0.0)
        remaining_volume = pos.get("remaining_volume_lots", original_volume)

        if position_id is None:
            return {"accepted": False, "code": "no_position_id", "reason": "no position ID"}

        try:
            close_volume = None
            if volume_pct < 100.0:
                close_volume = original_volume * (volume_pct / 100.0)

            await self._backend.close_position(position_id, volume_lots=close_volume)

            closed_volume = original_volume * (volume_pct / 100.0) if volume_pct < 100.0 else remaining_volume
            pos["remaining_volume_lots"] = max(0.0, remaining_volume - closed_volume)

            signal = pos.get("signal")
            if volume_pct >= 100.0 and signal:
                self._signal_store.update_signal_status(
                    signal.chat_id or "", signal.message_id or 0, SignalStatus.CLOSED.value
                )
                self._account_store.update_execution(
                    self._follower_id,
                    signal.chat_id or "",
                    signal.message_id or 0,
                    signal_type=signal.signal_type.value,
                    status="closed",
                )
                self._active_positions.pop(f"{signal.chat_id}:{signal.message_id}", None)
            elif signal:
                self._account_store.update_execution(
                    self._follower_id,
                    signal.chat_id or "",
                    signal.message_id or 0,
                    signal_type=signal.signal_type.value,
                    status="executed",
                    volume=pos["remaining_volume_lots"],
                )

            return {"accepted": True, "code": "position_closed", "reason": reason}
        except Exception as exc:
            return {"accepted": False, "code": "close_failed", "reason": str(exc)}

    def _find_original_signal(self, signal: TradeSignal) -> TradeSignal | None:
        if signal.reply_to_message_id and signal.chat_id:
            original = self._signal_store.get_signal(signal.chat_id, signal.reply_to_message_id)
            if original:
                return original

        if signal.signal_type == SignalType.ENTRY_UPDATE and signal.entry_price is not None:
            candidates = self._signal_store.get_active_signals(signal.chat_id)
            for orig in reversed(candidates):
                if orig.signal_type == SignalType.NEW and orig.entry_price == signal.entry_price:
                    return orig

        fallback = self._fallback_original_signal(signal)
        if fallback is not None and not self._signals_compatible(signal, fallback):
            logger.warning(
                "[%s] Rejecting fallback original-signal match: symbols differ (%s vs %s)",
                self._follower_id,
                signal.symbol,
                fallback.symbol,
            )
            return None
        return fallback

    def _fallback_original_signal(self, signal: TradeSignal) -> TradeSignal | None:
        if signal.chat_id:
            for key, pos in reversed(list(self._active_positions.items())):
                if key.startswith(f"{signal.chat_id}:"):
                    sig = pos.get("signal")
                    if sig and sig.signal_type == SignalType.NEW:
                        return sig

        active = self._signal_store.get_active_signals(signal.chat_id)
        if active:
            return active[-1]
        return None

    def _signals_compatible(self, incoming: TradeSignal, original: TradeSignal) -> bool:
        if incoming.symbol and original.symbol:
            return incoming.symbol.upper() == original.symbol.upper()
        return True

    def _count_active_positions_for_symbol(self, symbol: str) -> int:
        target = symbol.upper()
        return sum(
            1
            for pos in self._active_positions.values()
            if pos.get("signal") and pos["signal"].symbol and pos["signal"].symbol.upper() == target
        )

    def _has_same_symbol_direction(self, symbol: str, direction: Any) -> bool:
        target = symbol.upper()
        dir_value = direction.value if hasattr(direction, "value") else str(direction)
        for pos in self._active_positions.values():
            sig = pos.get("signal")
            if sig and sig.symbol and sig.symbol.upper() == target:
                sig_dir = sig.direction.value if hasattr(sig.direction, "value") else str(sig.direction)
                if sig_dir == dir_value:
                    return True
        return False

    def _has_opposite_direction(self, symbol: str, direction: Any) -> bool:
        target = symbol.upper()
        dir_value = direction.value if hasattr(direction, "value") else str(direction)
        opposite = "SELL" if dir_value == "BUY" else "BUY"
        for pos in self._active_positions.values():
            sig = pos.get("signal")
            if sig and sig.symbol and sig.symbol.upper() == target:
                sig_dir = sig.direction.value if hasattr(sig.direction, "value") else str(sig.direction)
                if sig_dir == opposite:
                    return True
        return False

    async def check_stale_positions(self) -> list[str]:
        timeout_min = self._trading.position_timeout_minutes
        if timeout_min is None or timeout_min <= 0:
            return []

        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        timeout_ms = timeout_min * 60 * 1000
        cleaned: list[str] = []

        for key, pos in list(self._active_positions.items()):
            signal = pos.get("signal")
            if signal is None or signal.timestamp_ms is None:
                continue
            elapsed_ms = now_ms - signal.timestamp_ms
            if elapsed_ms < timeout_ms:
                continue

            if self._trading.close_stale_positions:
                logger.warning("[%s] Position %s stale; closing", self._follower_id, key)
                await self._close_position(pos, volume_pct=100.0, reason="stale position timeout")
                cleaned.append(key)
            else:
                logger.warning("[%s] Position %s stale (close_stale_positions disabled)", self._follower_id, key)

        return cleaned

    def _check_circuit_recovery(self) -> None:
        if self._api_circuit_open and self._circuit_opened_at > 0:
            elapsed = time.time() - self._circuit_opened_at
            if elapsed > _CIRCUIT_RESET_SECONDS:
                self._api_circuit_open = False
                self._consecutive_api_failures = 0
                logger.info("[%s] Circuit breaker auto-recovered after %.0fs", self._follower_id, elapsed)

    @property
    def active_position_count(self) -> int:
        return len(self._active_positions)

    async def reconcile_positions(self) -> None:
        try:
            broker_position_ids = await self._backend.get_open_position_ids()
        except Exception as exc:
            logger.warning("[%s] Could not fetch open positions for reconciliation: %s", self._follower_id, exc)
            return

        stale_keys = []
        for key, pos in self._active_positions.items():
            position_id = pos.get("position_id")
            if position_id is not None and position_id not in broker_position_ids:
                stale_keys.append(key)

        for key in stale_keys:
            pos = self._active_positions.pop(key)
            signal = pos.get("signal")
            if signal:
                logger.info("[%s] Position %s closed externally; reconciling", self._follower_id, key)
                self._signal_store.update_signal_status(
                    signal.chat_id or "", signal.message_id or 0, SignalStatus.CLOSED.value
                )

    async def rebuild_state(self) -> None:
        active = self._signal_store.get_active_signals()
        for signal in active:
            key = f"{signal.chat_id}:{signal.message_id}"
            symbol_id = None
            if signal.symbol:
                symbol_id = self._resolver.get_symbol_id(signal.symbol)

            volume_lots = self._trading.volume_value
            exec_record = self._account_store.get_execution(
                self._follower_id, signal.chat_id or "", signal.message_id or 0
            )
            if exec_record and exec_record.volume is not None:
                volume_lots = exec_record.volume

            self._active_positions[key] = {
                "signal": signal,
                "order_id": signal.order_id,
                "position_id": signal.position_id,
                "symbol_id": symbol_id,
                "volume_lots": volume_lots,
                "remaining_volume_lots": volume_lots,
            }
        if self._active_positions:
            logger.info("[%s] Recovered %d active positions from store", self._follower_id, len(self._active_positions))
