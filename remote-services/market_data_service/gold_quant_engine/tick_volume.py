"""Tick Volume Analyzer — delta, cumulative delta, volume profile for XAUUSD."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from .models import OrderFlowMetrics, TickWindowState, VolumeProfileLevel

logger = logging.getLogger(__name__)

# Gold-specific defaults
DEFAULT_WINDOW_SIZE = 1000
DEFAULT_GRID_STEP = 0.10  # Gold price grid in USD


class TickVolumeAnalyzer:
    """Tracks tick-window state and computes volume-profile metrics."""

    def __init__(
        self,
        symbol_id: int = 0,
        window_size: int = DEFAULT_WINDOW_SIZE,
        grid_step: float = DEFAULT_GRID_STEP,
    ) -> None:
        self.symbol_id = symbol_id
        self.window_size = window_size
        self.grid_step = grid_step
        self.state = TickWindowState(symbol_id=symbol_id, window_size=window_size)

    # ── Public API ────────────────────────────────────────────────────────────

    def ingest_tick(self, tick: dict[str, Any]) -> None:
        """Feed a raw tick dict into the window."""
        self.state.add_tick(tick)

    def get_metrics(self) -> OrderFlowMetrics:
        """Compute current order-flow metrics from window state."""
        metrics = OrderFlowMetrics()

        # Delta
        metrics.cumulative_delta = self.state.cumulative_delta
        metrics.tick_delta = self.state.delta_history[-1] if self.state.delta_history else 0.0
        mean, std = self.state.delta_stats()
        metrics.delta_z_score = (metrics.tick_delta - mean) / std if std > 0 else 0.0
        metrics.delta_regime = self._classify_delta_regime(metrics.delta_z_score)

        # Volume profile
        metrics.poc = self.state.compute_poc()
        metrics.val, metrics.vah = self.state.compute_value_area(percentile=0.70)
        metrics.profile = self._build_profile_list()

        return metrics

    def reset(self) -> None:
        """Clear all state."""
        self.state = TickWindowState(
            symbol_id=self.symbol_id, window_size=self.window_size
        )

    # ── Classification helpers ────────────────────────────────────────────────

    def _classify_delta_regime(self, z: float) -> str:
        if z > 1.5:
            return "bullish"
        if z < -1.5:
            return "bearish"
        return "neutral"

    def _build_profile_list(self) -> list[VolumeProfileLevel]:
        """Return sorted list of VolumeProfileLevel for external use."""
        levels = list(self.state.volume_profile.values())
        levels.sort(key=lambda x: x.price)
        return levels
