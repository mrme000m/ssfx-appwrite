"""Autonomous signal generator driven by the gold-quant engine + entry agent.

This module implements a true autonomy loop: it periodically polls the Data
Service gold-quant snapshot, asks the EntryDecisionAgent for confirmation, and
injects synthetic NEW XAUUSD signals into all slaves when both the engine
and the agent return high-confidence ENTER verdicts.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from ssfx_parser import Direction, OrderType, SignalStatus, SignalType, TradeSignal
from ssfx_trader.agent_harness_client import AgentHarnessClient

logger = logging.getLogger(__name__)


@dataclass
class GoldQuantGeneratorConfig:
    enabled: bool = False
    interval_sec: float = 60.0
    min_quant_confidence: float = 0.75
    min_agent_confidence: float = 0.65
    symbol: str = "XAUUSD"
    default_sl_pips: float = 50.0
    default_tp_pips: float = 100.0
    default_volume: float = 0.01


class GoldQuantSignalGenerator:
    """Poll gold-quant snapshot and inject synthetic XAUUSD entry signals."""

    def __init__(
        self,
        config: GoldQuantGeneratorConfig,
        data_service_base_url: str,
        data_service_api_key: str | None,
        agent_harness_base_url: str,
        slaves: dict[str, Any],
    ):
        self._config = config
        self._data_url = data_service_base_url.rstrip("/")
        self._data_api_key = data_service_api_key
        self._agent_client = AgentHarnessClient(base_url=agent_harness_base_url)
        self._slaves = slaves
        self._shutdown = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if not self._config.enabled:
            logger.info("Gold-quant signal generator is disabled")
            return
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "Gold-quant signal generator started (every %.0fs, quant_conf >= %.2f, agent_conf >= %.2f)",
            self._config.interval_sec,
            self._config.min_quant_confidence,
            self._config.min_agent_confidence,
        )

    async def stop(self) -> None:
        self._shutdown.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Gold-quant signal generator stopped")

    async def _run_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                await self._evaluate_once()
            except Exception as exc:
                logger.warning("Gold-quant signal generator cycle failed: %s", exc)
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=self._config.interval_sec)
            except asyncio.TimeoutError:
                continue

    async def _evaluate_once(self) -> None:
        snapshot = await self._fetch_snapshot()
        if not snapshot:
            return

        decision = snapshot.get("decision", {})
        long_entry = decision.get("long_entry", {})
        short_entry = decision.get("short_entry", {})

        direction: Direction | None = None
        quant_verdict: dict[str, Any] | None = None

        if self._is_enter(long_entry):
            direction = Direction.BUY
            quant_verdict = long_entry
        elif self._is_enter(short_entry):
            direction = Direction.SELL
            quant_verdict = short_entry
        else:
            return

        quant_conf = float(quant_verdict.get("confidence", 0.0))
        if quant_conf < self._config.min_quant_confidence:
            logger.info(
                "Gold-quant %s entry confidence %.2f below threshold %.2f; skipping",
                direction.value,
                quant_conf,
                self._config.min_quant_confidence,
            )
            return

        price = self._extract_price(snapshot)
        if price is None:
            logger.warning("Gold-quant snapshot has no usable price; skipping autonomous signal")
            return

        signal = self._build_synthetic_signal(direction, price)
        agent_result = await self._agent_client.entry_decision(
            signal={
                "signal_type": "NEW",
                "direction": direction.value,
                "symbol": self._config.symbol,
                "order_type": "MARKET",
                "entry_price": signal.entry_price,
                "sl": signal.sl,
                "tp1": signal.tp1,
                "tp2": signal.tp2,
                "tp3": signal.tp3,
                "close_percentage": None,
                "parse_confidence": 1.0,
                "quality_score": 0.8,
                "quality_factors": ["gold_quant_autonomy"],
                "experience_action": "none",
                "message_id": signal.message_id,
                "chat_id": signal.chat_id,
                "reply_to_message_id": None,
                "raw_text": signal.raw_text,
            },
            quant_snapshot=snapshot,
            experience={"quality_score": 0.8, "quality_factors": ["gold_quant_autonomy"], "experience_action": "none"},
            open_positions=[
                {"symbol": p["signal"].symbol, "direction": p["signal"].direction.value}
                for slave in self._slaves.values()
                for p in slave._executor._active_positions.values()
            ],
        )

        if not agent_result:
            logger.info("Entry agent did not return a decision; skipping autonomous %s signal", direction.value)
            return

        agent_decision = agent_result.get("decision", {})
        agent_action = agent_decision.get("action")
        agent_conf = float(agent_decision.get("confidence", 0.0))

        if agent_action == "MODIFY" and agent_decision.get("limit_price"):
            signal.order_type = OrderType.LIMIT
            signal.entry_price = float(agent_decision["limit_price"])
            # Re-derive SL/TP from agent if provided.
            if agent_decision.get("sl") is not None:
                signal.sl = agent_decision["sl"]
            if agent_decision.get("tp1") is not None:
                signal.tp1 = agent_decision["tp1"]
            if agent_decision.get("tp2") is not None:
                signal.tp2 = agent_decision["tp2"]
            if agent_decision.get("tp3") is not None:
                signal.tp3 = agent_decision["tp3"]
            logger.info(
                "Agent MODIFY → LIMIT for autonomous %s signal @ %.5f",
                direction.value,
                signal.entry_price,
            )
        elif agent_action != "ENTER":
            logger.info(
                "Entry agent %s autonomous %s signal (conf %.2f); skipping",
                agent_action,
                direction.value,
                agent_conf,
            )
            return

        if agent_conf < self._config.min_agent_confidence:
            logger.info(
                "Agent entry confidence %.2f below threshold %.2f; skipping autonomous %s signal",
                agent_conf,
                self._config.min_agent_confidence,
                direction.value,
            )
            return

        await self._inject_signal(signal, agent_decision)

    def _is_enter(self, verdict: dict[str, Any] | None) -> bool:
        if not verdict:
            return False
        return str(verdict.get("verdict", "")).upper() == "ENTER"

    def _extract_price(self, snapshot: dict[str, Any]) -> float | None:
        tick = snapshot.get("tick") or {}
        for key in ("last", "ask", "bid"):
            value = tick.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return float(value)
        mtf = snapshot.get("multi_timeframe", {})
        for tf in ("M15", "H1", "H4", "D1"):
            candle = mtf.get(tf)
            if isinstance(candle, dict):
                close = candle.get("close")
                if isinstance(close, (int, float)) and close > 0:
                    return float(close)
        return None

    def _build_synthetic_signal(self, direction: Direction, price: float) -> TradeSignal:
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        pip_size = 0.01  # XAUUSD
        sl = price - self._config.default_sl_pips * pip_size
        tp = price + self._config.default_tp_pips * pip_size
        if direction == Direction.SELL:
            sl, tp = tp, sl
        return TradeSignal(
            raw_text=f"AUTONOMOUS {direction.value} {self._config.symbol} @ {price}",
            direction=direction,
            symbol=self._config.symbol,
            signal_type=SignalType.NEW,
            entry_price=price,
            sl=sl,
            tp1=tp,
            chat_id="autonomous",
            message_id=now_ms,
            timestamp_ms=now_ms,
            status=SignalStatus.EMITTED,
            parse_confidence=1.0,
            parser_used="gold_quant_autonomy",
        )

    async def _inject_signal(self, signal: TradeSignal, agent_decision: dict[str, Any]) -> None:
        logger.info(
            "Injecting autonomous %s %s signal @ %.5f (agent_conf=%.2f)",
            signal.direction.value,
            signal.symbol,
            signal.entry_price,
            float(agent_decision.get("confidence", 0.0)),
        )
        for name, slave in self._slaves.items():
            try:
                if not await self._slave_can_receive_signal(slave, signal):
                    continue
                await slave.on_signal(signal)
            except Exception as exc:
                logger.warning("Failed to route autonomous signal to slave %s: %s", name, exc)

    async def _slave_can_receive_signal(self, slave: Any, signal: TradeSignal) -> bool:
        """Pre-check slave state before injecting an autonomous signal."""
        config = slave._config
        if not config.enabled:
            logger.debug("Skipping autonomous signal for disabled slave %s", config.name)
            return False
        if not config.allows_symbol(signal.symbol):
            logger.debug("Skipping autonomous signal for slave %s: symbol %s not allowed", config.name, signal.symbol)
            return False

        executor = slave._executor
        trading = executor._trading
        active = executor._active_positions

        if executor.active_position_count >= trading.max_positions:
            logger.debug("Skipping autonomous signal for slave %s: max positions reached", config.name)
            return False

        sym_count = sum(
            1 for p in active.values()
            if p.get("signal") and p["signal"].symbol and p["signal"].symbol.upper() == signal.symbol.upper()
        )
        if sym_count >= trading.max_positions_per_symbol:
            logger.debug("Skipping autonomous signal for slave %s: max %s positions reached", config.name, signal.symbol)
            return False

        dir_value = signal.direction.value if signal.direction else None
        if not trading.allow_same_symbol_add:
            for p in active.values():
                pos_signal = p.get("signal")
                if (
                    pos_signal
                    and pos_signal.symbol
                    and pos_signal.symbol.upper() == signal.symbol.upper()
                    and pos_signal.direction
                    and pos_signal.direction.value == dir_value
                ):
                    logger.debug("Skipping autonomous signal for slave %s: same symbol/direction open", config.name)
                    return False

        if not trading.allow_opposite_direction:
            opposite = "SELL" if dir_value == "BUY" else "BUY"
            for p in active.values():
                pos_signal = p.get("signal")
                if (
                    pos_signal
                    and pos_signal.symbol
                    and pos_signal.symbol.upper() == signal.symbol.upper()
                    and pos_signal.direction
                    and pos_signal.direction.value == opposite
                ):
                    logger.debug("Skipping autonomous signal for slave %s: opposite direction open", config.name)
                    return False

        risk_monitor = executor._risk_monitor
        if risk_monitor is not None:
            try:
                summary = await executor.get_account_summary()
                equity = summary.get("equity", 0.0)
                open_risk_pct = self._estimate_open_risk_pct(signal, trading.default_volume, equity)
                allowed, reason = risk_monitor.check_new_signal(equity, open_risk_pct=open_risk_pct)
                if not allowed:
                    logger.debug("Skipping autonomous signal for slave %s: risk kill-switch (%s)", config.name, reason)
                    return False
            except Exception as exc:
                logger.warning("Skipping autonomous signal for slave %s: risk check error %s", config.name, exc)
                return False

        return True

    @staticmethod
    def _estimate_open_risk_pct(signal: TradeSignal, volume_lots: float, equity: float) -> float | None:
        if equity <= 0 or signal.sl_float is None or volume_lots <= 0 or signal.entry_price is None:
            return None
        # Default lot size for XAUUSD; used for a conservative pre-check.
        lot_size = 100_000.0
        risk_amount = abs(signal.entry_price - signal.sl_float) * volume_lots * lot_size
        return (risk_amount / equity) * 100.0

    async def _fetch_snapshot(self) -> dict[str, Any] | None:
        url = f"{self._data_url}/api/v1/gold/quant"
        headers: dict[str, str] = {}
        if self._data_api_key:
            headers["Authorization"] = f"Bearer {self._data_api_key}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning("Failed to fetch gold-quant snapshot from %s: %s", url, exc)
            return None
