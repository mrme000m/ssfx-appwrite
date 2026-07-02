"""Data models for the Gold Quantitative Analysis Engine."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from market_data_service.models import TimeFrame

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
#  Core snapshot model — returned by the engine on every refresh
# ═══════════════════════════════════════════════════════════════════════════════


class GoldQuantSnapshot(BaseModel):
    """Complete quantitative snapshot for XAUUSD at a point in time."""

    symbol: str = "XAUUSD"
    timestamp_ms: int = Field(..., description="Unix epoch ms when snapshot was computed")

    # Price
    bid: float = 0.0
    ask: float = 0.0
    spread: float = 0.0

    # Multi-timeframe confluence
    mtf: MultiTimeframeConfluence = Field(default_factory=lambda: MultiTimeframeConfluence())

    # Order flow / tick volume
    order_flow: OrderFlowMetrics = Field(default_factory=lambda: OrderFlowMetrics())

    # Structural levels
    key_levels: KeyLevels = Field(default_factory=lambda: KeyLevels())

    # Agent decisions
    decision: AgentDecision = Field(default_factory=lambda: AgentDecision())

    # Human / LLM prompt
    agent_prompt: str = ""

    class Config:
        arbitrary_types_allowed = True


# ═══════════════════════════════════════════════════════════════════════════════
#  Multi-timeframe
# ═══════════════════════════════════════════════════════════════════════════════


class TimeframeReading(BaseModel):
    """Indicator reading for a single timeframe."""

    timeframe: str = ""
    direction: str = "neutral"  # bullish / bearish / neutral
    score: float = 0.0  # -1.0 to +1.0
    indicators: list[dict[str, Any]] = Field(default_factory=list)
    regime: str = "ranging"  # uptrend / downtrend / ranging / breakout


class MultiTimeframeConfluence(BaseModel):
    """Aggregated multi-timeframe analysis."""

    readings: list[TimeframeReading] = Field(default_factory=list)
    overall_direction: str = "neutral"
    confidence: float = 0.0  # 0.0 to 1.0
    bull_count: int = 0
    bear_count: int = 0
    neutral_count: int = 0
    factors: dict[str, float] = Field(default_factory=dict)

    @property
    def is_strongly_bullish(self) -> bool:
        return self.overall_direction == "STRONGLY_BULLISH"

    @property
    def is_bullish(self) -> bool:
        return self.overall_direction in ("BULLISH", "STRONGLY_BULLISH")

    @property
    def is_strongly_bearish(self) -> bool:
        return self.overall_direction == "STRONGLY_BEARISH"

    @property
    def is_bearish(self) -> bool:
        return self.overall_direction in ("BEARISH", "STRONGLY_BEARISH")

    @property
    def is_neutral(self) -> bool:
        return self.overall_direction == "NEUTRAL"


# ═══════════════════════════════════════════════════════════════════════════════
#  Order flow / tick volume
# ═══════════════════════════════════════════════════════════════════════════════


class VolumeProfileLevel(BaseModel):
    """Single price level in a volume profile."""

    price: float = 0.0
    total_volume: float = 0.0
    bull_volume: float = 0.0
    bear_volume: float = 0.0
    delta: float = 0.0


class OrderFlowMetrics(BaseModel):
    """Real-time tick-volume and order-flow state."""

    # Tick delta
    tick_delta: float = 0.0  # Σ(ask_volume) − Σ(bid_volume) in current window
    cumulative_delta: float = 0.0  # Running total
    delta_regime: str = "neutral"  # bullish / bearish / neutral
    delta_z_score: float = 0.0  # How many std-devs from mean

    # Volume profile
    poc: float | None = None  # Point of Control
    vah: float | None = None  # Value Area High (70%)
    val: float | None = None  # Value Area Low (70%)
    profile: list[VolumeProfileLevel] = Field(default_factory=list)

    # Order book
    book_imbalance: float = 0.0  # (bid_depth − ask_depth) / (bid_depth + ask_depth)
    bid_depth: float = 0.0
    ask_depth: float = 0.0

    # Flow events
    last_event: str = "none"  # sweep_reject_bullish / sweep_reject_bearish / absorption / exhaustion / none
    last_event_price: float | None = None
    last_event_ms: int | None = None

    @property
    def is_bullish_flow(self) -> bool:
        return self.delta_regime == "bullish" and self.book_imbalance > 0.1

    @property
    def is_bearish_flow(self) -> bool:
        return self.delta_regime == "bearish" and self.book_imbalance < -0.1


# ═══════════════════════════════════════════════════════════════════════════════
#  Key structural levels
# ═══════════════════════════════════════════════════════════════════════════════


class StructuralLevel(BaseModel):
    """A detected support/resistance/key level."""

    price: float = 0.0
    level_type: str = ""  # support / resistance / poc / vah / val / fvg / order_block / swing_high / swing_low / fib
    strength: float = 0.0  # 0.0 to 1.0
    timeframe: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class FairValueGap(BaseModel):
    """Three-candle FVG structure."""

    top: float = 0.0
    bottom: float = 0.0
    fvg_type: str = "bullish"  # bullish / bearish
    timeframe: str = ""
    strength: float = 0.0


class OrderBlock(BaseModel):
    """Order block detected before impulsive move."""

    high: float = 0.0
    low: float = 0.0
    ob_type: str = "bullish"  # bullish / bearish
    timeframe: str = ""
    strength: float = 0.0
    mitigated: bool = False


class KeyLevels(BaseModel):
    """All detected structural levels."""

    support: list[StructuralLevel] = Field(default_factory=list)
    resistance: list[StructuralLevel] = Field(default_factory=list)
    fvgs: list[FairValueGap] = Field(default_factory=list)
    order_blocks: list[OrderBlock] = Field(default_factory=list)
    swing_highs: list[StructuralLevel] = Field(default_factory=list)
    swing_lows: list[StructuralLevel] = Field(default_factory=list)
    fib_levels: list[StructuralLevel] = Field(default_factory=list)

    def nearest_support(self, price: float) -> StructuralLevel | None:
        candidates = [s for s in self.support if s.price <= price]
        return max(candidates, key=lambda x: x.price) if candidates else None

    def nearest_resistance(self, price: float) -> StructuralLevel | None:
        candidates = [r for r in self.resistance if r.price >= price]
        return min(candidates, key=lambda x: x.price) if candidates else None

    def nearest_fvg(self, price: float) -> FairValueGap | None:
        if not self.fvgs:
            return None
        return min(self.fvgs, key=lambda f: abs(((f.top + f.bottom) / 2) - price))


# ═══════════════════════════════════════════════════════════════════════════════
#  Agent decisions
# ═══════════════════════════════════════════════════════════════════════════════


class EntryDecision(BaseModel):
    """Decision for a specific entry type."""

    verdict: str = "WAIT"  # ENTER / REJECT / WAIT
    confidence: float = 0.0  # 0.0 to 1.0
    reasons: list[str] = Field(default_factory=list)
    suggested_action: str = ""
    risk_reward_estimate: float | None = None


class AgentDecision(BaseModel):
    """All agent-facing decisions derived from the snapshot."""

    short_entry: EntryDecision = Field(default_factory=lambda: EntryDecision(verdict="WAIT"))
    long_entry: EntryDecision = Field(default_factory=lambda: EntryDecision(verdict="WAIT"))
    limit_order: EntryDecision = Field(default_factory=lambda: EntryDecision(verdict="WAIT"))

    # Lifecycle phase guidance
    phase_guidance: dict[str, str] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════════
#  Internal computation state (not persisted)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class TickWindowState:
    """Ephemeral state for tick-window computations."""

    symbol_id: int = 0
    window_size: int = 1000
    ticks: list[dict[str, Any]] = field(default_factory=list)
    cumulative_delta: float = 0.0
    volume_profile: dict[float, VolumeProfileLevel] = field(default_factory=dict)
    delta_history: list[float] = field(default_factory=list)
    last_sweep_ms: int | None = None
    last_sweep_price: float | None = None
    last_sweep_type: str = ""

    def add_tick(self, tick: dict[str, Any]) -> None:
        """Append a tick and maintain window size."""
        self.ticks.append(tick)
        if len(self.ticks) > self.window_size:
            self.ticks.pop(0)
        self._update_delta(tick)
        self._update_profile(tick)

    def _update_delta(self, tick: dict[str, Any]) -> None:
        bid_vol = tick.get("bid_volume", 0.0) or 0.0
        ask_vol = tick.get("ask_volume", 0.0) or 0.0
        delta = ask_vol - bid_vol
        self.cumulative_delta += delta
        self.delta_history.append(delta)
        if len(self.delta_history) > self.window_size:
            self.delta_history.pop(0)

    def _update_profile(self, tick: dict[str, Any]) -> None:
        price = float(tick.get("mid", 0.0) or ((tick.get("bid", 0.0) + tick.get("ask", 0.0)) / 2))
        if price == 0:
            return
        # Round to a sensible grid for gold (e.g., $0.10)
        grid = round(price * 10) / 10
        vol = (tick.get("bid_volume", 0.0) or 0.0) + (tick.get("ask_volume", 0.0) or 0.0)
        bid_vol = tick.get("bid_volume", 0.0) or 0.0
        ask_vol = tick.get("ask_volume", 0.0) or 0.0

        lvl = self.volume_profile.get(grid)
        if lvl is None:
            lvl = VolumeProfileLevel(price=grid)
            self.volume_profile[grid] = lvl
        lvl.total_volume += vol
        # Approximate bull/bear classification by tick direction vs previous
        if len(self.ticks) >= 2:
            prev_mid = (self.ticks[-2].get("bid", price) + self.ticks[-2].get("ask", price)) / 2
            if price > prev_mid:
                lvl.bull_volume += ask_vol
                lvl.bear_volume += bid_vol
            else:
                lvl.bull_volume += bid_vol
                lvl.bear_volume += ask_vol
        else:
            lvl.bull_volume += ask_vol
            lvl.bear_volume += bid_vol
        lvl.delta = lvl.bull_volume - lvl.bear_volume

    def compute_poc(self) -> float | None:
        if not self.volume_profile:
            return None
        return max(self.volume_profile.values(), key=lambda x: x.total_volume).price

    def compute_value_area(self, percentile: float = 0.70) -> tuple[float | None, float | None]:
        """Return (VAL, VAH) enclosing `percentile` of total volume."""
        if not self.volume_profile:
            return None, None
        sorted_levels = sorted(self.volume_profile.values(), key=lambda x: x.price)
        total = sum(l.total_volume for l in sorted_levels)
        target = total * percentile
        # Expand outward from POC
        poc_idx = max(range(len(sorted_levels)), key=lambda i: sorted_levels[i].total_volume)
        low_idx = high_idx = poc_idx
        cum = sorted_levels[poc_idx].total_volume
        while cum < target and (low_idx > 0 or high_idx < len(sorted_levels) - 1):
            vol_below = sorted_levels[low_idx - 1].total_volume if low_idx > 0 else 0
            vol_above = sorted_levels[high_idx + 1].total_volume if high_idx < len(sorted_levels) - 1 else 0
            if vol_above >= vol_below and high_idx < len(sorted_levels) - 1:
                high_idx += 1
                cum += vol_above
            elif low_idx > 0:
                low_idx -= 1
                cum += vol_below
            else:
                break
        return sorted_levels[low_idx].price, sorted_levels[high_idx].price

    def delta_stats(self) -> tuple[float, float]:
        """Return (mean, std) of delta history."""
        if not self.delta_history:
            return 0.0, 1.0
        import numpy as np

        arr = np.array(self.delta_history, dtype=float)
        return float(np.mean(arr)), float(np.std(arr) or 1.0)

    @property
    def tick_count(self) -> int:
        return len(self.ticks)
