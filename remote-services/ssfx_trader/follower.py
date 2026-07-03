"""Per-account follower — consumes structured signals and executes trades."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ssfx_parser import SignalType, TradeSignal

from .config import AccountConfig
from .executor import TradeExecutor
from .stores.base import AccountStore, SignalStore

logger = logging.getLogger(__name__)


def _json_log(level: str, event_name: str, **payload: Any) -> None:
    body = {
        "level": level,
        "event": event_name,
        "ts": datetime.now(UTC).isoformat(),
        **payload,
    }
    print(json.dumps(body, default=str), flush=True)


class AccountFollower:
    """Consumes structured signals and executes trades on one account."""

    def __init__(
        self,
        config: AccountConfig,
        signal_store: SignalStore,
        account_store: AccountStore,
        executor: TradeExecutor,
        config_provider: Callable[[], AccountConfig],
        autonomy_enabled: bool = False,
    ):
        self._config = config
        self._signal_store = signal_store
        self._account_store = account_store
        self._executor = executor
        self._config_provider = config_provider
        self._autonomy_enabled = autonomy_enabled
        self._shutdown = asyncio.Event()
        self._watch_task: asyncio.Task | None = None
        self._signal_queue: asyncio.Queue[TradeSignal] = asyncio.Queue()
        self._config_reload_interval_sec = 60.0
        self._stale_check_interval_sec = 60.0
        self._reconcile_interval_sec = 120.0
        self._autonomy_interval_sec = 60.0

    async def start(self) -> None:
        if not self._config.enabled:
            _json_log("warn", "follower_disabled", name=self._config.name)
            return

        await self._executor.rebuild_state()
        await self._backfill()

        self._watch_task = asyncio.create_task(self._watch_loop())

        _json_log("info", "follower_ready", name=self._config.name, message="Watching for new signals...")

        await self._shutdown.wait()

        if self._watch_task and not self._watch_task.done():
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass

        _json_log("info", "follower_stopped", name=self._config.name)

    def stop(self) -> None:
        self._shutdown.set()

    async def on_signal(self, signal: TradeSignal) -> None:
        """Public entry point used by the webhook server to enqueue a signal."""
        if self._shutdown.is_set():
            return
        await self._signal_queue.put(signal)

    async def _backfill(self) -> None:
        pending = self._signal_store.get_pending_entry_signals()
        if not pending:
            _json_log("info", "backfill_empty", name=self._config.name)
            return

        _json_log("info", "backfill_start", name=self._config.name, count=len(pending))
        for signal in pending:
            if self._shutdown.is_set():
                break
            await self._process_signal(signal)
        _json_log("info", "backfill_complete", name=self._config.name)

    async def _watch_loop(self) -> None:
        last_config_reload = 0.0
        last_reconcile = 0.0
        last_autonomy = 0.0
        while not self._shutdown.is_set():
            try:
                signal = await asyncio.wait_for(
                    self._signal_queue.get(),
                    timeout=self._stale_check_interval_sec,
                )
                await self._process_signal(signal)
            except asyncio.TimeoutError:
                try:
                    await self._executor.check_stale_positions()
                except Exception as exc:
                    logger.warning("[%s] Stale-position check failed: %s", self._config.name, exc)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("[%s] Signal consume error: %s", self._config.name, exc)
                _json_log("error", "signal_consume_error", name=self._config.name, error=str(exc))
                if not self._shutdown.is_set():
                    await asyncio.sleep(5)

            now = asyncio.get_event_loop().time()
            if now - last_config_reload >= self._config_reload_interval_sec:
                last_config_reload = now
                try:
                    self._reload_config()
                except Exception as exc:
                    logger.warning("[%s] Config reload failed: %s", self._config.name, exc)

            if now - last_reconcile >= self._reconcile_interval_sec:
                last_reconcile = now
                try:
                    await self._executor.reconcile_positions()
                except Exception as exc:
                    logger.warning("[%s] Position reconciliation failed: %s", self._config.name, exc)

            if now - last_autonomy >= self._autonomy_interval_sec:
                last_autonomy = now
                try:
                    autonomy_results = await self._executor.run_autonomy_cycle()
                    if autonomy_results:
                        _json_log("info", "autonomy_cycle", name=self._config.name, results=autonomy_results)
                except Exception as exc:
                    logger.warning("[%s] Autonomy cycle failed: %s", self._config.name, exc)

    def _reload_config(self) -> None:
        fc = self._config_provider()
        self._config = fc
        self._executor.update_config(fc.trading)
        self._executor.set_autonomy_enabled(self._autonomy_enabled)
        _json_log(
            "info",
            "config_reloaded",
            name=self._config.name,
            max_positions=fc.trading.max_positions,
            position_timeout_minutes=fc.trading.position_timeout_minutes,
        )

    async def _process_signal(self, signal: TradeSignal) -> None:
        chat_id = signal.chat_id or ""
        message_id = signal.message_id or 0

        if self._account_store.has_execution(self._config.name, chat_id, message_id):
            return

        if signal.signal_type == SignalType.IGNORE:
            self._account_store.mark_skipped(self._config.name, chat_id, message_id, "IGNORE signal")
            return

        if signal.parse_confidence < self._config.trading.min_parse_confidence:
            self._account_store.mark_skipped(
                self._config.name, chat_id, message_id, f"low confidence {signal.parse_confidence:.2f}"
            )
            _json_log(
                "debug",
                "signal_skipped",
                name=self._config.name,
                message_id=message_id,
                reason="low_confidence",
                confidence=signal.parse_confidence,
            )
            return

        if signal.experience_action == "block":
            self._account_store.mark_skipped(
                self._config.name, chat_id, message_id, f"experience block score={signal.quality_score:.2f}"
            )
            _json_log(
                "info",
                "signal_blocked",
                name=self._config.name,
                message_id=message_id,
                quality_score=signal.quality_score,
                factors=signal.quality_factors,
            )
            return

        if signal.experience_action == "reduce":
            signal.volume_multiplier = 0.5
            _json_log(
                "info",
                "signal_reduced",
                name=self._config.name,
                message_id=message_id,
                quality_score=signal.quality_score,
                factors=signal.quality_factors,
            )

        if signal.signal_type == SignalType.NEW:
            if not self._config.allows_symbol(signal.symbol):
                self._account_store.mark_skipped(
                    self._config.name, chat_id, message_id, f"symbol {signal.symbol} not in filter"
                )
                _json_log(
                    "debug",
                    "signal_skipped",
                    name=self._config.name,
                    message_id=message_id,
                    reason="symbol_filter",
                    symbol=signal.symbol,
                )
                return

        _json_log(
            "info",
            "signal_received",
            name=self._config.name,
            message_id=message_id,
            signal_type=signal.signal_type,
            direction=signal.direction,
            symbol=signal.symbol,
            entry=signal.entry_price,
            confidence=signal.parse_confidence,
            parser=signal.parser_used,
        )

        result = await self._executor.execute_signal(signal)

        _json_log(
            "info" if result.get("accepted") else "warn",
            "trade_executed" if result.get("accepted") else "trade_rejected",
            name=self._config.name,
            message_id=message_id,
            code=result.get("code"),
            reason=result.get("reason"),
        )
