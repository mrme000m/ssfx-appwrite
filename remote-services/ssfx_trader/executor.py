"""Trade executor — bridges parsed signals to execution backends."""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
import typing
from datetime import UTC, datetime
from typing import Any

from ssfx_parser import OrderType, SignalStatus, SignalType, TradeSignal

from .agent_harness_client import AgentHarnessClient
from .backends.base import ExecutionBackend
from .config import (
    EntryUpdateAction,
    OrderHandling,
    PerAccountTradingConfig,
    SlStrategy,
    TpStrategy,
)
from .market_context import DataServiceClient, MarketContext
from .risk_monitor import RiskMonitor
from .stores.base import AccountStore, SignalStore
from .symbol_resolver import SymbolResolver
from .volume_resolver import VolumeResolver

if typing.TYPE_CHECKING:
    from market_data_service.signal_experience.updater import SignalExperienceUpdater

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
        slave_id: str,
        backend: ExecutionBackend,
        resolver: SymbolResolver,
        signal_store: SignalStore,
        account_store: AccountStore,
        trading: PerAccountTradingConfig | None = None,
        volume_resolver: VolumeResolver | None = None,
        market_context_client: DataServiceClient | None = None,
        experience_updater: "SignalExperienceUpdater | None" = None,
        risk_monitor: RiskMonitor | None = None,
        autonomy_enabled: bool | None = None,
    ):
        self._slave_id = slave_id
        self._backend = backend
        self._resolver = resolver
        self._signal_store = signal_store
        self._account_store = account_store
        self._trading = trading or PerAccountTradingConfig()
        self._volume_resolver = volume_resolver
        self._market_context_client = market_context_client
        self._experience_updater = experience_updater
        self._risk_monitor = risk_monitor
        self._agent_client = AgentHarnessClient()
        # autonomy_enabled must be explicitly set; no fallback to environment
        # This makes behavior predictable and prevents surprising fallback behavior
        self._autonomy_enabled = autonomy_enabled if autonomy_enabled is not None else False
        self._active_positions: dict[str, dict[str, Any]] = {}
        self._positions_lock = asyncio.Lock()
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
        # Keep risk monitor limits in sync when config is reloaded
        if self._risk_monitor is not None:
            self._risk_monitor._limits.max_daily_loss_pct = trading.max_daily_loss_pct
            self._risk_monitor._limits.max_drawdown_pct = trading.max_drawdown_pct
            self._risk_monitor._limits.max_open_risk_pct = trading.max_open_risk_pct
            self._risk_monitor._limits.panic_stop = trading.panic_stop
            self._risk_monitor._limits.risk_reset_utc_hour = trading.risk_reset_utc_hour
        logger.info("[%s] TradeExecutor config updated", self._slave_id)

    def set_autonomy_enabled(self, enabled: bool) -> None:
        """Runtime toggle for the autonomous lifecycle re-evaluation loop."""
        self._autonomy_enabled = enabled
        logger.info("[%s] Autonomy %s", self._slave_id, "enabled" if enabled else "disabled")

    async def get_account_summary(self) -> dict[str, float]:
        return await self._backend.get_account_summary()

    async def run_autonomy_cycle(self) -> list[dict[str, Any]]:
        """Proactively re-evaluate open XAUUSD positions even without new signals."""
        if not self._autonomy_enabled or self._market_context_client is None:
            return []

        results: list[dict[str, Any]] = []
        async with self._positions_lock:
            positions_snapshot = list(self._active_positions.items())
        for key, pos in positions_snapshot:
            original = pos.get("signal")
            if original is None or original.symbol != "XAUUSD":
                continue
            synthetic = TradeSignal(
                raw_text="autonomy check",
                signal_type=SignalType.RUNNING,
                symbol=original.symbol,
                direction=original.direction,
                chat_id=original.chat_id,
                message_id=original.message_id,
                reply_to_message_id=original.message_id,
            )
            try:
                result = await self._execute_follow_up_signal(synthetic)
                results.append({"key": key, "action": result.get("code"), "accepted": result.get("accepted")})
            except Exception as exc:
                logger.warning("[%s] Autonomy cycle failed for %s: %s", self._slave_id, key, exc)
        return results

    async def execute_signal(self, signal: TradeSignal) -> dict[str, Any]:
        try:
            self._check_circuit_recovery()
            await self.check_stale_positions()

            # Check market context for signal types that can open new positions or modify existing ones
            if signal.signal_type in (SignalType.NEW, SignalType.ENTRY_UPDATE):
                passed, code, reason, _context = await self._check_market_context(signal)
                if not passed:
                    self._account_store.mark_skipped(
                        self._slave_id,
                        signal.chat_id or "",
                        signal.message_id or 0,
                        reason=f"{code}: {reason}",
                    )
                    return {"accepted": False, "code": code, "reason": reason}

            if signal.signal_type == SignalType.NEW:
                return await self._execute_new_signal(signal)

            if self._update_buffer.is_enabled() and signal.signal_type in _AGGRESSION_SCORE:
                original = self._find_original_signal(signal)
                chat_id = original.chat_id if original else signal.chat_id
                message_id = original.message_id if original else signal.reply_to_message_id

                # Validate that we have valid IDs before buffering
                if chat_id is None or message_id is None:
                    logger.warning(
                        "[%s] Cannot buffer signal %s: missing chat_id or message_id. "
                        "Executing immediately instead.",
                        self._slave_id,
                        signal.message_id
                    )
                    return await self._execute_follow_up_signal(signal)

                key = f"{chat_id}:{message_id}"
                if original and key:
                    self._update_buffer.add(key, signal)
                    return {
                        "accepted": True,
                        "code": "buffered",
                        "reason": f"buffered for {self._trading.update_aggregation_ms}ms",
                    }

            return await self._execute_follow_up_signal(signal)
        except Exception as exc:
            logger.error("[%s] Error executing signal: %s", self._slave_id, exc, exc_info=True)
            self._account_store.update_execution(
                self._slave_id,
                signal.chat_id or "",
                signal.message_id or 0,
                signal_type=signal.signal_type.value,
                status="failed",
                error=str(exc),
            )
            return {"accepted": False, "code": "error", "reason": str(exc)}

    async def _execute_follow_up_signal(self, signal: TradeSignal) -> dict[str, Any]:
        original = self._find_original_signal(signal)
        signal, plan_result = await self._maybe_apply_lifecycle_plan(signal, original)
        plan = plan_result.get("plan", {}) if plan_result else {}
        plan_action = plan.get("action")

        # Agent lifecycle overrides
        if plan_action == "FULL_CLOSE":
            return await self._handle_close(signal, 100.0)
        if plan_action == "CANCEL":
            return await self._handle_cancel(signal)
        if plan_action == "MOVE_BREAKEVEN":
            return await self._handle_sl_to_entry(signal, new_sl=plan.get("new_sl"))
        if (
            signal.signal_type == SignalType.ENTRY_UPDATE
            and original is not None
            and (plan.get("new_sl") is not None or plan.get("new_tp") is not None)
        ):
            return await self._amend_position_sltp(signal, original, plan.get("new_sl"), plan.get("new_tp"))

        if signal.signal_type == SignalType.TP_HIT:
            result = await self._handle_tp_hit(signal)
        elif signal.signal_type == SignalType.SL_HIT:
            result = await self._handle_sl_hit(signal)
        elif signal.signal_type == SignalType.CLOSE_HALF:
            result = await self._handle_close(signal, self._trading.partial_close.on_close_half_pct)
        elif signal.signal_type == SignalType.CLOSE_PARTIAL:
            pct = signal.close_percentage or self._trading.update_actions.close_partial_override_pct or 50.0
            result = await self._handle_close(signal, pct)
        elif signal.signal_type == SignalType.CLOSE:
            result = await self._handle_close(signal, 100.0)
        elif signal.signal_type == SignalType.CANCEL:
            result = await self._handle_cancel(signal)
        elif signal.signal_type == SignalType.SL_TO_ENTRY:
            result = await self._handle_sl_to_entry(signal)
        elif signal.signal_type == SignalType.ENTRY_UPDATE:
            result = await self._handle_entry_update(signal)
        elif signal.signal_type == SignalType.RUNNING:
            return {"accepted": False, "code": "info", "reason": "status update only"}
        else:
            return {"accepted": False, "code": "ignore", "reason": f"unhandled signal type: {signal.signal_type}"}

        if result.get("accepted") and self._experience_updater is not None and original is not None:
            try:
                outcome, pips = self._derive_outcome_and_pips(signal, original)
                await asyncio.to_thread(
                    self._experience_updater.update_from_outcome,
                    original,
                    self._extract_author(original),
                    outcome,
                    pips,
                )
            except Exception as exc:
                logger.warning("[%s] Experience update failed: %s", self._slave_id, exc)

        return result

    def _extract_author(self, signal: TradeSignal) -> str | None:
        from market_data_service.signal_experience.classifier import extract_author

        return extract_author(signal.raw_text)

    async def _maybe_apply_lifecycle_plan(
        self, signal: TradeSignal, original: TradeSignal | None
    ) -> tuple[TradeSignal, dict[str, Any] | None]:
        if (
            not self._agent_client.is_lifecycle_enabled()
            or signal.symbol != "XAUUSD"
            or original is None
            or self._market_context_client is None
        ):
            return signal, None

        key = f"{original.chat_id}:{original.message_id}"
        pos = self._active_positions.get(key)
        if not pos:
            return signal, None

        quant = await self._market_context_client.get_gold_quant()
        plan_result = await self._agent_client.lifecycle_plan(
            position={
                "symbol": signal.symbol,
                "direction": original.direction.value if original.direction else None,
                "volume_lots": pos.get("volume_lots"),
                "entry_price": original.entry_price,
                "remaining_volume_lots": pos.get("remaining_volume_lots"),
            },
            signal_update=self._signal_to_dict(signal),
            quant_snapshot=quant,
        )
        if not plan_result:
            return signal, None

        plan = plan_result.get("plan", {})
        logger.info(
            "[%s] Agent lifecycle plan for %s: %s (%s)",
            self._slave_id,
            signal.signal_type.value,
            plan.get("action"),
            plan.get("reasoning", ""),
        )

        # Override partial-close percentage when the agent is confident
        if (
            plan.get("action") == "PARTIAL_CLOSE"
            and signal.signal_type in (SignalType.TP_HIT, SignalType.CLOSE_PARTIAL, SignalType.CLOSE_HALF)
        ):
            pct = plan.get("close_percentage")
            if pct is not None:
                signal.close_percentage = float(pct)

        return signal, plan_result

    @staticmethod
    def _signal_to_dict(signal: TradeSignal) -> dict[str, Any]:
        return {
            "signal_type": signal.signal_type.value,
            "direction": signal.direction.value if signal.direction else None,
            "symbol": signal.symbol,
            "order_type": signal.order_type.value if signal.order_type else None,
            "entry_price": signal.entry_price,
            "sl": signal.sl,
            "tp1": signal.tp1,
            "tp2": signal.tp2,
            "tp3": signal.tp3,
            "close_percentage": signal.close_percentage,
            "parse_confidence": signal.parse_confidence,
            "quality_score": signal.quality_score,
            "experience_action": signal.experience_action,
            "message_id": signal.message_id,
            "chat_id": signal.chat_id,
            "reply_to_message_id": signal.reply_to_message_id,
            "raw_text": signal.raw_text[:500],
        }

    def _derive_outcome_and_pips(self, signal: TradeSignal, original: TradeSignal) -> tuple[str, float | None]:
        from market_data_service.signal_experience.models import Outcome

        if signal.signal_type == SignalType.TP_HIT:
            pips = float(signal.profit_pips) if signal.profit_pips is not None else None
            return Outcome.TP, pips
        if signal.signal_type == SignalType.SL_HIT:
            pips = None
            if original.sl_float is not None and original.entry_price is not None:
                pip_size = self._pip_size_for(original.symbol)
                if pip_size:
                    pips = -abs(original.entry_price - original.sl_float) / pip_size
            return Outcome.SL, pips
        if signal.signal_type == SignalType.CANCEL:
            return Outcome.DELETED_PENDING, 0.0
        if signal.signal_type == SignalType.SL_TO_ENTRY:
            return Outcome.BE, 0.0
        if signal.signal_type in (SignalType.CLOSE, SignalType.CLOSE_HALF, SignalType.CLOSE_PARTIAL):
            pips = float(signal.profit_pips) if signal.profit_pips is not None else None
            if pips is not None:
                return (Outcome.CLOSE_PROFIT if pips > 0 else Outcome.CLOSE_LOSS), pips
            return Outcome.CLOSE_UNKNOWN, None
        return Outcome.OPEN, None

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
            logger.warning("[%s] %s", self._slave_id, reason)

        if not context.quality_ok():
            reason = "market data quality check failed"
            if mode == "strict":
                return False, "data_quality_failed", reason, context
            logger.warning("[%s] %s", self._slave_id, reason)

        pip_size = self._pip_size_for(signal.symbol)

        if signal.entry_price is not None and signal.sl_float is not None:
            sl_distance_pips = abs(signal.entry_price - signal.sl_float) / pip_size
            if (
                self._trading.max_sl_distance_pips is not None
                and sl_distance_pips > self._trading.max_sl_distance_pips
            ):
                reason = f"SL distance {sl_distance_pips:.1f} pips exceeds max {self._trading.max_sl_distance_pips}"
                return False, "sl_distance_exceeded", reason, context

        spread = context.spread
        if spread is not None and self._trading.max_spread_pips is not None:
            spread_pips = spread / pip_size
            if spread_pips > self._trading.max_spread_pips:
                reason = f"spread {spread_pips:.1f} pips exceeds max {self._trading.max_spread_pips}"
                if mode == "strict":
                    return False, "spread_too_high", reason, context
                logger.warning("[%s] %s", self._slave_id, reason)

        # Risk guardrails require account balance; leave to volume/risk resolver.
        return True, "", "", context

    def _pip_size_for(self, symbol: str | None) -> float:
        """Return the pip size for a symbol; defaults to 0.0001 if unknown."""
        if not symbol:
            return 0.0001
        symbol_id = self._resolver.get_symbol_id(symbol)
        if symbol_id is None:
            return 0.0001
        info = self._resolver.get_symbol_info(symbol_id)
        if info is None:
            return 0.0001
        return float(info.get("pip_size", 0.0001))

    def _estimate_open_risk_pct(
        self, signal: TradeSignal, volume_lots: float, equity: float
    ) -> float | None:
        """Estimate % of equity at risk if the position hits SL."""
        if equity <= 0 or signal.sl_float is None or volume_lots <= 0:
            return None
        entry = signal.entry_price
        if entry is None:
            return None
        symbol_id = self._resolver.get_symbol_id(signal.symbol or "")
        lot_size = 100_000.0
        if symbol_id is not None:
            info = self._resolver.get_symbol_info(symbol_id)
            if info is not None:
                lot_size = float(info.get("lot_size", 100_000.0))
        risk_amount = abs(entry - signal.sl_float) * volume_lots * lot_size
        return (risk_amount / equity) * 100.0

    async def _execute_new_signal(self, signal: TradeSignal) -> dict[str, Any]:
        if not signal.symbol or not signal.direction:
            return {"accepted": False, "code": "no_symbol_direction", "reason": "missing symbol or direction"}

        # Market context check is now handled in execute_signal method
        # for both NEW and ENTRY_UPDATE signal types

        symbol_id = self._resolver.get_symbol_id(signal.symbol)
        if symbol_id is None:
            return {"accepted": False, "code": "symbol_not_found", "reason": f"symbol {signal.symbol} not found"}

        if self._api_circuit_open:
            return {"accepted": False, "code": "circuit_breaker_open", "reason": "API circuit breaker is open"}

        async with self._positions_lock:
            allowed, code, reason = self._can_open_new_position_locked(signal)
        if not allowed:
            return {"accepted": False, "code": code, "reason": reason}

        volume_lots = self._trading.default_volume
        if self._volume_resolver is not None:
            try:
                volume_lots = await self._volume_resolver.resolve_volume(signal)
            except Exception as exc:
                logger.error("[%s] Volume resolution failed, rejecting signal: %s", self._slave_id, exc)
                return {
                    "accepted": False,
                    "code": "volume_resolution_error",
                    "reason": f"volume resolution failed: {exc}",
                }

        volume_lots *= max(0.0, min(2.0, signal.volume_multiplier or 1.0))

        # Risk check now that volume is resolved so max_open_risk_pct can be enforced.
        if self._risk_monitor is not None:
            try:
                summary = await self.get_account_summary()
                equity = summary.get("equity", 0.0)
                open_risk_pct = self._estimate_open_risk_pct(signal, volume_lots, equity)
                allowed, reason = self._risk_monitor.check_new_signal(equity, open_risk_pct=open_risk_pct)
                if not allowed:
                    self._account_store.mark_skipped(
                        self._slave_id,
                        signal.chat_id or "",
                        signal.message_id or 0,
                        f"risk:{reason}",
                    )
                    logger.warning("[%s] New signal blocked by risk check: %s", self._slave_id, reason)
                    return {"accepted": False, "code": "risk_kill_switch", "reason": reason}
            except Exception as exc:
                logger.error("[%s] Risk check failed, rejecting signal: %s", self._slave_id, exc)
                return {
                    "accepted": False,
                    "code": "risk_check_error",
                    "reason": f"risk check failed: {exc}",
                }

        order_type = signal.order_type or OrderType.MARKET
        if self._trading.order_handling == OrderHandling.MARKET_ONLY:
            order_type = OrderType.MARKET
        elif self._trading.order_handling == OrderHandling.LIMIT_ONLY:
            if order_type != OrderType.LIMIT:
                return {"accepted": False, "code": "limit_only", "reason": "order_handling is limit_only"}

        # ── AI agent entry decision (XAUUSD only, with deterministic fallback) ──
        agent_decision = None
        if (
            self._agent_client.is_entry_enabled()
            and signal.symbol == "XAUUSD"
            and self._market_context_client is not None
        ):
            quant = await self._market_context_client.get_gold_quant()
            if quant is not None:
                agent_result = await self._agent_client.entry_decision(
                    signal=self._signal_to_dict(signal),
                    quant_snapshot=quant,
                    experience={
                        "quality_score": signal.quality_score,
                        "quality_factors": signal.quality_factors,
                        "experience_action": signal.experience_action,
                    },
                    open_positions=[{"symbol": p["signal"].symbol, "direction": p["signal"].direction.value} for p in self._active_positions.values()],
                )
                if not agent_result:
                    logger.error("[%s] Entry agent returned no decision; rejecting signal", self._slave_id)
                    return {
                        "accepted": False,
                        "code": "agent_unavailable",
                        "reason": "entry agent unavailable or returned no decision",
                    }
                agent_decision = agent_result.get("decision", {})
                logger.info(
                    "[%s] Agent entry decision: %s (confidence %.2f) — %s",
                    self._slave_id,
                    agent_decision.get("action"),
                    agent_decision.get("confidence", 0.0),
                    agent_decision.get("suggested_action", ""),
                )
                action = agent_decision.get("action")
                if action == "REJECT":
                    reason = f"agent_reject: {'; '.join(agent_decision.get('reasons', []))}"
                    self._account_store.mark_skipped(
                        self._slave_id,
                        signal.chat_id or "",
                        signal.message_id or 0,
                        reason=reason,
                    )
                    return {"accepted": False, "code": "agent_reject", "reason": reason}
                if action in ("WAIT", "HOLD"):
                    reason = f"agent_wait: {'; '.join(agent_decision.get('reasons', []))}"
                    self._account_store.mark_skipped(
                        self._slave_id,
                        signal.chat_id or "",
                        signal.message_id or 0,
                        reason=reason,
                    )
                    return {"accepted": False, "code": "agent_wait", "reason": reason}
                if action == "MODIFY":
                    if agent_decision.get("limit_price"):
                        order_type = OrderType.LIMIT
                        signal.order_type = OrderType.LIMIT
                        signal.entry_price = agent_decision["limit_price"]
                    if agent_decision.get("sl") is not None:
                        signal.sl = agent_decision["sl"]
                    if agent_decision.get("tp1") is not None:
                        signal.tp1 = agent_decision["tp1"]
                    if agent_decision.get("tp2") is not None:
                        signal.tp2 = agent_decision["tp2"]
                    if agent_decision.get("tp3") is not None:
                        signal.tp3 = agent_decision["tp3"]
                    volume_multiplier = float(agent_decision.get("size_multiplier", 1.0))
                    volume_lots *= max(0.0, min(2.0, volume_multiplier))

                    # Re-validate modified signal against market context and risk before executing.
                    passed, code, reason, _context = await self._check_market_context(signal)
                    if not passed:
                        self._account_store.mark_skipped(
                            self._slave_id,
                            signal.chat_id or "",
                            signal.message_id or 0,
                            reason=f"modify_{code}: {reason}",
                        )
                        return {"accepted": False, "code": f"modify_{code}", "reason": reason}
                    try:
                        summary = await self.get_account_summary()
                        equity = summary.get("equity", 0.0)
                        open_risk_pct = self._estimate_open_risk_pct(signal, volume_lots, equity)
                        allowed, reason = self._risk_monitor.check_new_signal(equity, open_risk_pct=open_risk_pct)
                        if not allowed:
                            self._account_store.mark_skipped(
                                self._slave_id,
                                signal.chat_id or "",
                                signal.message_id or 0,
                                f"modify_risk:{reason}",
                            )
                            return {"accepted": False, "code": "modify_risk_kill_switch", "reason": reason}
                    except Exception as exc:
                        logger.error("[%s] Post-modify risk check failed, rejecting signal: %s", self._slave_id, exc)
                        return {
                            "accepted": False,
                            "code": "modify_risk_check_error",
                            "reason": f"post-modify risk check failed: {exc}",
                        }

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
                self._slave_id,
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
                self._slave_id,
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
            self._slave_id,
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

        # Re-check limits under the lock before committing the position to memory.
        # If the window changed, close/cancel the newly opened order and reject.
        async with self._positions_lock:
            allowed, code, reason = self._can_open_new_position_locked(signal)
            if allowed:
                self._active_positions[key] = {
                    "signal": signal,
                    "order_id": order_id,
                    "position_id": position_id,
                    "symbol_id": symbol_id,
                    "volume_lots": volume_lots,
                    "remaining_volume_lots": volume_lots,
                }

        if not allowed:
            logger.warning(
                "[%s] Position limits changed after order fill; closing %s/order %s (%s)",
                self._slave_id,
                position_id,
                order_id,
                reason,
            )
            if position_id:
                try:
                    await self._backend.close_position(position_id, volume_lots=None)
                except Exception as exc:
                    logger.error("[%s] Failed to close rejected position %s: %s", self._slave_id, position_id, exc)
            elif order_id:
                try:
                    await self._backend.cancel_order(order_id)
                except Exception as exc:
                    logger.error("[%s] Failed to cancel rejected order %s: %s", self._slave_id, order_id, exc)
            return {"accepted": False, "code": code, "reason": reason}

        if (
            self._trading.sl_strategy == SlStrategy.TRAILING_AT_BREAKEVEN
            and position_id
            and signal.entry_price is not None
        ):
            try:
                await self._backend.amend_position_sltp(position_id, signal.entry_price, None)
            except Exception as exc:
                logger.warning("[%s] Could not move SL to breakeven: %s", self._slave_id, exc)

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
            "agent_decision": agent_decision,
        }
        self._signal_store.record_trade(signal, final_result)
        logger.info("[%s] Executed signal: %s", self._slave_id, final_result["reason"])
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
        return await self._close_position(pos, volume_pct=close_pct, reason=f"TP{tp_num} hit", signal_type=signal.signal_type.value, code=f"tp{tp_num}_hit")

    async def _handle_sl_hit(self, signal: TradeSignal) -> dict[str, Any]:
        original = self._find_original_signal(signal)
        if original is None:
            return {"accepted": False, "code": "no_original", "reason": "could not find original signal"}

        key = f"{original.chat_id}:{original.message_id}"
        pos = self._active_positions.get(key)
        if pos is None:
            return {"accepted": False, "code": "no_position", "reason": "no active position for SL hit"}

        return await self._close_position(pos, volume_pct=100.0, reason="SL hit", signal_type=signal.signal_type.value, code="sl_hit")

    async def _handle_close(self, signal: TradeSignal, close_pct: float) -> dict[str, Any]:
        original = self._find_original_signal(signal)
        if original is None:
            return {"accepted": False, "code": "no_original", "reason": "could not find original signal"}

        key = f"{original.chat_id}:{original.message_id}"
        pos = self._active_positions.get(key)
        if pos is None:
            return {"accepted": False, "code": "no_position", "reason": "no active position"}

        return await self._close_position(pos, volume_pct=close_pct, reason="close signal", signal_type=signal.signal_type.value, code="closed")

    async def _handle_cancel(self, signal: TradeSignal) -> dict[str, Any]:
        original = self._find_original_signal(signal)
        if original is None:
            return {"accepted": False, "code": "no_original", "reason": "could not find original signal"}

        key = f"{original.chat_id}:{original.message_id}"
        pos = self._active_positions.get(key)
        position_id = pos.get("position_id") if pos else None
        order_id = pos.get("order_id") if pos else None

        # If the position has been filled, close it; otherwise cancel the pending order.
        if position_id:
            close_result = await self._close_position(
                pos, volume_pct=100.0, reason="cancel signal", signal_type=signal.signal_type.value, code="cancelled"
            )
            if not close_result.get("accepted"):
                return {
                    "accepted": False,
                    "code": "close_failed",
                    "reason": f"could not close filled position: {close_result.get('reason', 'unknown')}",
                }
        elif order_id:
            try:
                await self._backend.cancel_order(order_id)
            except Exception as exc:
                logger.warning("[%s] Cancel order failed: %s", self._slave_id, exc)

            # For pending orders the position record is removed here because _close_position was not called.
            self._signal_store.update_signal_status(
                original.chat_id or "", original.message_id or 0, SignalStatus.CLOSED.value
            )
            self._account_store.update_execution(
                self._slave_id,
                original.chat_id or "",
                original.message_id or 0,
                signal_type=signal.signal_type.value,
                status="closed",
            )
            self._active_positions.pop(key, None)

        return {"accepted": True, "code": "cancelled", "reason": "order cancelled"}

    async def _handle_sl_to_entry(self, signal: TradeSignal, new_sl: float | None = None) -> dict[str, Any]:
        original = self._find_original_signal(signal)
        if original is None:
            return {"accepted": False, "code": "no_original", "reason": "could not find original signal"}

        key = f"{original.chat_id}:{original.message_id}"
        async with self._positions_lock:
            pos = self._active_positions.get(key)
            if pos is None:
                return {"accepted": False, "code": "no_position", "reason": "no active position"}
            position_id = pos.get("position_id")
        entry_price = original.entry_price
        if new_sl is None and entry_price is None:
            return {"accepted": False, "code": "no_entry", "reason": "no entry price to set SL to"}

        target_sl = new_sl if new_sl is not None else entry_price
        if position_id:
            try:
                await self._backend.amend_position_sltp(position_id, target_sl, None)
                return {"accepted": True, "code": "sl_to_entry", "reason": f"SL moved to {target_sl}"}
            except Exception as exc:
                return {"accepted": False, "code": "amend_failed", "reason": str(exc)}
        return {"accepted": False, "code": "no_position_id", "reason": "no position ID to amend"}

    async def _amend_position_sltp(
        self,
        signal: TradeSignal,
        original: TradeSignal,
        new_sl: float | None,
        new_tp: float | None,
    ) -> dict[str, Any]:
        """Apply agent-suggested SL/TP amendments to an open position."""
        key = f"{original.chat_id}:{original.message_id}"
        async with self._positions_lock:
            pos = self._active_positions.get(key)
            if pos is None:
                return {"accepted": False, "code": "no_position", "reason": "no active position"}
            position_id = pos.get("position_id")
        if position_id is None:
            return {"accepted": False, "code": "no_position_id", "reason": "no position ID to amend"}

        try:
            await self._backend.amend_position_sltp(position_id, new_sl, new_tp)
            if new_sl is not None:
                original.sl = new_sl
            if new_tp is not None:
                original.tp1 = new_tp
            return {
                "accepted": True,
                "code": "sltp_amended",
                "reason": f"SL/TP amended to SL={new_sl} TP={new_tp}",
            }
        except Exception as exc:
            return {"accepted": False, "code": "amend_failed", "reason": str(exc)}

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
            close_result = await self._close_position(pos, volume_pct=100.0, reason="entry update close-and-reopen", signal_type=signal.signal_type.value, code="entry_update_close")
            if not close_result.get("accepted"):
                return close_result
            new_signal = original.model_copy(update={"entry_price": signal.entry_price})
            return await self._execute_new_signal(new_signal)

        return {"accepted": False, "code": "unknown_action", "reason": f"unknown entry_update_action {action}"}

    async def _close_position(
        self,
        pos: dict[str, Any],
        volume_pct: float,
        reason: str,
        signal_type: str | None = None,
        code: str = "position_closed",
    ) -> dict[str, Any]:
        async with self._positions_lock:
            position_id = pos.get("position_id")
            original_volume = pos.get("volume_lots", 0.0)
            remaining_volume = pos.get("remaining_volume_lots", original_volume)

            if position_id is None:
                return {"accepted": False, "code": "no_position_id", "reason": "no position ID"}

            closed_pnl: float | None = None
            try:
                close_volume = None
                if volume_pct < 100.0:
                    close_volume = original_volume * (volume_pct / 100.0)

                close_result = await self._backend.close_position(position_id, volume_lots=close_volume)
                closed_pnl = close_result.get("realized_pnl") if isinstance(close_result, dict) else None

                closed_volume = original_volume * (volume_pct / 100.0) if volume_pct < 100.0 else remaining_volume
                pos["remaining_volume_lots"] = max(0.0, remaining_volume - closed_volume)

                signal = pos.get("signal")
                if volume_pct >= 100.0 and signal:
                    self._signal_store.update_signal_status(
                        signal.chat_id or "", signal.message_id or 0, SignalStatus.CLOSED.value
                    )
                    self._account_store.update_execution(
                        self._slave_id,
                        signal.chat_id or "",
                        signal.message_id or 0,
                        signal_type=signal_type or signal.signal_type.value,
                        status="closed",
                    )
                    self._active_positions.pop(f"{signal.chat_id}:{signal.message_id}", None)
                elif signal:
                    self._account_store.update_execution(
                        self._slave_id,
                        signal.chat_id or "",
                        signal.message_id or 0,
                        signal_type=signal_type or signal.signal_type.value,
                        status="executed",
                        volume=pos["remaining_volume_lots"],
                    )
            except Exception as exc:
                return {"accepted": False, "code": "close_failed", "reason": str(exc)}

        # Feed realized PnL back to the risk monitor so daily loss counters stay accurate.
        # Done outside the position lock because it may call the backend for account summary.
        if self._risk_monitor is not None and closed_pnl is not None:
            try:
                summary = await self.get_account_summary()
                self._risk_monitor.record_closed_pnl(float(closed_pnl), summary.get("equity", 0.0))
            except Exception as exc:
                logger.warning("[%s] Failed to record closed PnL: %s", self._slave_id, exc)

        return {"accepted": True, "code": code, "reason": reason}

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
                self._slave_id,
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

        async with self._positions_lock:
            positions_snapshot = list(self._active_positions.items())

        for key, pos in positions_snapshot:
            signal = pos.get("signal")
            if signal is None or signal.timestamp_ms is None:
                continue
            elapsed_ms = now_ms - signal.timestamp_ms
            if elapsed_ms < timeout_ms:
                continue

            if self._trading.close_stale_positions:
                logger.warning("[%s] Position %s stale; closing", self._slave_id, key)
                await self._close_position(pos, volume_pct=100.0, reason="stale position timeout", signal_type="stale_timeout", code="stale_closed")
                cleaned.append(key)
            else:
                logger.warning("[%s] Position %s stale (close_stale_positions disabled)", self._slave_id, key)

        return cleaned

    def _can_open_new_position_locked(
        self, signal: TradeSignal
    ) -> tuple[bool, str, str]:
        """Check position limits while holding ``_positions_lock``."""
        active_count = len(self._active_positions)
        if active_count >= self._trading.max_positions:
            return False, "max_positions", f"max {self._trading.max_positions} positions reached"

        sym_active = self._count_active_positions_for_symbol(signal.symbol)
        if sym_active >= self._trading.max_positions_per_symbol:
            return False, "max_positions_per_symbol", f"max {self._trading.max_positions_per_symbol} {signal.symbol} positions reached"

        if not self._trading.allow_same_symbol_add:
            if self._has_same_symbol_direction(signal.symbol, signal.direction):
                return False, "same_symbol_direction", f"already have {signal.direction.value} {signal.symbol}"

        if not self._trading.allow_opposite_direction:
            if self._has_opposite_direction(signal.symbol, signal.direction):
                return False, "opposite_direction", f"opposite {signal.symbol} position already open"

        return True, "", ""

    def _check_circuit_recovery(self) -> None:
        if self._api_circuit_open and self._circuit_opened_at > 0:
            elapsed = time.time() - self._circuit_opened_at
            if elapsed > _CIRCUIT_RESET_SECONDS:
                self._api_circuit_open = False
                self._consecutive_api_failures = 0
                logger.info("[%s] Circuit breaker auto-recovered after %.0fs", self._slave_id, elapsed)

    @property
    def active_position_count(self) -> int:
        return len(self._active_positions)

    async def reconcile_positions(self) -> None:
        try:
            broker_position_ids = await self._backend.get_open_position_ids()
        except Exception as exc:
            logger.warning("[%s] Could not fetch open positions for reconciliation: %s", self._slave_id, exc)
            return

        async with self._positions_lock:
            stale_keys = [
                key
                for key, pos in self._active_positions.items()
                if pos.get("position_id") is not None and pos["position_id"] not in broker_position_ids
            ]
            for key in stale_keys:
                pos = self._active_positions.pop(key)
                signal = pos.get("signal")
                if signal:
                    logger.info("[%s] Position %s closed externally; reconciling", self._slave_id, key)
                    self._signal_store.update_signal_status(
                        signal.chat_id or "", signal.message_id or 0, SignalStatus.CLOSED.value
                    )

    async def rebuild_state(self) -> None:
        active = self._signal_store.get_active_signals()
        recovered: dict[str, dict[str, Any]] = {}
        for signal in active:
            key = f"{signal.chat_id}:{signal.message_id}"
            symbol_id = None
            if signal.symbol:
                symbol_id = self._resolver.get_symbol_id(signal.symbol)

            volume_lots = self._trading.volume_value
            exec_record = self._account_store.get_execution(
                self._slave_id, signal.chat_id or "", signal.message_id or 0
            )
            if exec_record and exec_record.volume is not None:
                volume_lots = exec_record.volume

            recovered[key] = {
                "signal": signal,
                "order_id": signal.order_id,
                "position_id": signal.position_id,
                "symbol_id": symbol_id,
                "volume_lots": volume_lots,
                "remaining_volume_lots": volume_lots,
            }
        async with self._positions_lock:
            self._active_positions.update(recovered)
        if recovered:
            logger.info("[%s] Recovered %d active positions from store", self._slave_id, len(recovered))
