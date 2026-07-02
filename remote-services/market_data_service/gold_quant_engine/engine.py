"""Gold Quantitative Analysis Engine — orchestrator module."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .confidence import ConfidenceScorer
from .context_builder import AgentContextBuilder
from .key_levels import KeyLevelsDetector
from .models import GoldQuantSnapshot, TickWindowState
from .multi_timeframe import MultiTimeframeEngine as MtfEngine
from .order_flow import OrderFlowEngine
from .tick_volume import TickVolumeAnalyzer

logger = logging.getLogger(__name__)

DEFAULT_GOLD_SYMBOL = "XAUUSD"
DEFAULT_GOLD_SYMBOL_ID = 1  # cTrader symbol ID for gold


class GoldQuantEngine:
    """
    Orchestrates all gold quantitative analysis subsystems:
      - Tick Volume Analyzer (delta, profile)
      - Order Flow Engine (sweeps, absorption, exhaustion)
      - Key Levels Detector (S/R, FVG, OB, Fibs)
      - Multi-Timeframe Confluence Engine (M15, H1, H4, D1)
      - Confidence Scorer (short/long/limit decisions)
      - Agent Context Builder (prompt-ready output)
    """

    def __init__(
        self,
        symbol: str = DEFAULT_GOLD_SYMBOL,
        symbol_id: int = DEFAULT_GOLD_SYMBOL_ID,
        tick_window_size: int = 1000,
        bar_lookback: int = 200,
        timeframes: list[str] | None = None,
        min_confluence_tfs: int = 3,
    ) -> None:
        self.symbol = symbol
        self.symbol_id = symbol_id
        self.bar_lookback = bar_lookback
        self.timeframes = timeframes or ["M15", "H1", "H4"]
        self.min_confluence_tfs = min_confluence_tfs

        # Subsystems
        self.tick_analyzer = TickVolumeAnalyzer(
            symbol_id=symbol_id, window_size=tick_window_size
        )
        self.flow_engine = OrderFlowEngine()
        self.levels_detector = KeyLevelsDetector()
        self.mtf_engine = MtfEngine(min_confluence_tfs=min_confluence_tfs)
        self.confidence_scorer = ConfidenceScorer()
        self.context_builder = AgentContextBuilder()

        # Cached bar data per timeframe (populated externally)
        self._bars_cache: dict[str, list[dict[str, Any]]] = {}
        self._latest_price: dict[str, float] = {"bid": 0.0, "ask": 0.0}
        self._latest_ts: int = 0

    # ── Ingestion ─────────────────────────────────────────────────────────────

    def ingest_tick(self, tick: dict[str, Any]) -> None:
        """Feed a tick into the real-time analyzers."""
        self.tick_analyzer.ingest_tick(tick)
        self.flow_engine.on_tick(tick)
        self._latest_price["bid"] = tick.get("bid", self._latest_price["bid"])
        self._latest_price["ask"] = tick.get("ask", self._latest_price["ask"])
        self._latest_ts = tick.get("timestamp_ms", self._latest_ts)

    def ingest_depth(self, depth: dict[str, Any]) -> None:
        """Feed an order-book depth update."""
        self.flow_engine.on_depth(depth)

    def set_bars(self, timeframe: str, bars: list[dict[str, Any]]) -> None:
        """Set cached OHLCV bars for a timeframe."""
        self._bars_cache[timeframe] = bars

    # ── Snapshot computation ──────────────────────────────────────────────────

    async def get_snapshot(self) -> GoldQuantSnapshot:
        """Compute full quantitative snapshot."""
        return await asyncio.to_thread(self._compute_snapshot_sync)

    def get_snapshot_sync(self) -> GoldQuantSnapshot:
        """Synchronous version for callers already in a sync context."""
        return self._compute_snapshot_sync()

    def _compute_snapshot_sync(self) -> GoldQuantSnapshot:
        # 1. Order flow metrics from tick window
        flow = self.tick_analyzer.get_metrics()

        # Inject last detected event from flow engine
        flow.last_event = self.flow_engine.last_event
        flow.last_event_price = self.flow_engine.last_event_price
        flow.last_event_ms = self.flow_engine.last_event_ms

        # 2. Multi-timeframe readings
        readings = []
        for tf in self.timeframes:
            bars = self._bars_cache.get(tf, [])
            if len(bars) >= 50:
                # Inject order-flow score (from M15 delta only, scaled)
                of_score = 0.0
                if tf == "M15":
                    of_score = flow.delta_z_score * 0.1
                reading = self.mtf_engine.score_timeframe(bars, tf, order_flow_score=of_score)
                readings.append(reading)

        mtf = self.mtf_engine.compute(readings)

        # 3. Key levels from the highest available timeframe (prefer H4, then H1)
        levels_tf = "H1"
        for preferred in ["H4", "H1", "M15"]:
            if preferred in self._bars_cache and len(self._bars_cache[preferred]) >= 20:
                levels_tf = preferred
                break
        levels = self.levels_detector.detect(
            self._bars_cache.get(levels_tf, []), timeframe=levels_tf
        )

        # 4. Decisions
        mid = (self._latest_price["bid"] + self._latest_price["ask"]) / 2
        decision = self.confidence_scorer.evaluate(mtf, flow, levels, mid)

        # 5. Build snapshot
        snapshot = self.context_builder.build_snapshot(
            symbol=self.symbol,
            timestamp_ms=self._latest_ts or 0,
            bid=self._latest_price["bid"],
            ask=self._latest_price["ask"],
            mtf=mtf,
            flow=flow,
            levels=levels,
            decision=decision,
        )

        return snapshot

    # ── Utility ───────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Clear all internal state."""
        self.tick_analyzer.reset()
        self.flow_engine.reset()
        self._bars_cache.clear()
        self._latest_price = {"bid": 0.0, "ask": 0.0}
        self._latest_ts = 0
