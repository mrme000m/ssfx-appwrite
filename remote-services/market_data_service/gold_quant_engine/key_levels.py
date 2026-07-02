"""Key Levels Detector — swing points, FVGs, order blocks, fibs for XAUUSD."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from .models import (
    FairValueGap,
    KeyLevels,
    OrderBlock,
    StructuralLevel,
)

logger = logging.getLogger(__name__)

# Detection parameters
SWING_LOOKBACK = 2  # bars on each side for local extrema
FVG_MIN_SIZE = 0.10  # minimum USD gap to count as FVG
OB_LOOKBACK = 5  # bars to look back for order block origin


class KeyLevelsDetector:
    """Detects structural levels from OHLCV bar series."""

    def __init__(
        self,
        swing_lookback: int = SWING_LOOKBACK,
        fvg_min_size: float = FVG_MIN_SIZE,
    ) -> None:
        self.swing_lookback = swing_lookback
        self.fvg_min_size = fvg_min_size

    # ── Public API ────────────────────────────────────────────────────────────

    def detect(self, bars: list[dict[str, Any]], timeframe: str = "H1") -> KeyLevels:
        """Run full detection pipeline on a bar series."""
        levels = KeyLevels()

        if len(bars) < 10:
            return levels

        levels.swing_highs = self._detect_swing_highs(bars, timeframe)
        levels.swing_lows = self._detect_swing_lows(bars, timeframe)
        levels.support = self._build_support(levels.swing_lows)
        levels.resistance = self._build_resistance(levels.swing_highs)
        levels.fvgs = self._detect_fvgs(bars, timeframe)
        levels.order_blocks = self._detect_order_blocks(bars, timeframe)
        levels.fib_levels = self._detect_fibonacci(bars, timeframe)

        return levels

    # ── Swing points ──────────────────────────────────────────────────────────

    def _detect_swing_highs(
        self, bars: list[dict[str, Any]], timeframe: str
    ) -> list[StructuralLevel]:
        highs = [b["high"] for b in bars]
        results: list[StructuralLevel] = []
        lb = self.swing_lookback
        for i in range(lb, len(highs) - lb):
            window = highs[i - lb : i + lb + 1]
            if highs[i] == max(window):
                results.append(
                    StructuralLevel(
                        price=highs[i],
                        level_type="swing_high",
                        strength=0.7,
                        timeframe=timeframe,
                        metadata={"index": i, "timestamp_ms": bars[i].get("timestamp_ms")},
                    )
                )
        return results

    def _detect_swing_lows(
        self, bars: list[dict[str, Any]], timeframe: str
    ) -> list[StructuralLevel]:
        lows = [b["low"] for b in bars]
        results: list[StructuralLevel] = []
        lb = self.swing_lookback
        for i in range(lb, len(lows) - lb):
            window = lows[i - lb : i + lb + 1]
            if lows[i] == min(window):
                results.append(
                    StructuralLevel(
                        price=lows[i],
                        level_type="swing_low",
                        strength=0.7,
                        timeframe=timeframe,
                        metadata={"index": i, "timestamp_ms": bars[i].get("timestamp_ms")},
                    )
                )
        return results

    def _build_support(self, swing_lows: list[StructuralLevel]) -> list[StructuralLevel]:
        # Group nearby levels to avoid duplicates
        return self._cluster_levels(swing_lows, cluster_width=1.0, level_type="support")

    def _build_resistance(self, swing_highs: list[StructuralLevel]) -> list[StructuralLevel]:
        return self._cluster_levels(swing_highs, cluster_width=1.0, level_type="resistance")

    def _cluster_levels(
        self,
        levels: list[StructuralLevel],
        cluster_width: float,
        level_type: str,
    ) -> list[StructuralLevel]:
        if not levels:
            return []
        sorted_levels = sorted(levels, key=lambda x: x.price)
        clusters: list[list[StructuralLevel]] = []
        current: list[StructuralLevel] = [sorted_levels[0]]
        for lvl in sorted_levels[1:]:
            if lvl.price - current[-1].price <= cluster_width:
                current.append(lvl)
            else:
                clusters.append(current)
                current = [lvl]
        clusters.append(current)

        result: list[StructuralLevel] = []
        for cluster in clusters:
            avg_price = round(sum(l.price for l in cluster) / len(cluster), 2)
            strength = min(1.0, 0.5 + 0.1 * len(cluster))
            result.append(
                StructuralLevel(
                    price=avg_price,
                    level_type=level_type,
                    strength=strength,
                    timeframe=cluster[0].timeframe,
                )
            )
        return result

    # ── Fair Value Gaps ───────────────────────────────────────────────────────

    def _detect_fvgs(
        self, bars: list[dict[str, Any]], timeframe: str
    ) -> list[FairValueGap]:
        """Detect three-candle FVG patterns."""
        fvgs: list[FairValueGap] = []
        for i in range(1, len(bars) - 1):
            left = bars[i - 1]
            mid = bars[i]
            right = bars[i + 1]

            # Bullish FVG: right low > left high (gap up)
            gap = right["low"] - left["high"]
            if gap >= self.fvg_min_size:
                fvgs.append(
                    FairValueGap(
                        top=right["low"],
                        bottom=left["high"],
                        fvg_type="bullish",
                        timeframe=timeframe,
                        strength=min(1.0, gap / 1.0),
                    )
                )

            # Bearish FVG: right high < left low (gap down)
            gap = left["low"] - right["high"]
            if gap >= self.fvg_min_size:
                fvgs.append(
                    FairValueGap(
                        top=left["low"],
                        bottom=right["high"],
                        fvg_type="bearish",
                        timeframe=timeframe,
                        strength=min(1.0, gap / 1.0),
                    )
                )

        return fvgs

    # ── Order Blocks ──────────────────────────────────────────────────────────

    def _detect_order_blocks(
        self, bars: list[dict[str, Any]], timeframe: str
    ) -> list[OrderBlock]:
        """Detect order blocks: last opposite candle before strong move."""
        obs: list[OrderBlock] = []
        for i in range(OB_LOOKBACK, len(bars) - 1):
            # Look for strong impulsive move after a pullback candle
            move_range = bars[i + 1]["high"] - bars[i + 1]["low"]
            avg_range = np.mean(
                [b["high"] - b["low"] for b in bars[i - 2 : i + 1]]
            )
            if avg_range == 0:
                continue

            is_impulsive = move_range > avg_range * 1.5
            if not is_impulsive:
                continue

            # The "origin" candle is the last opposite-colour candle before the move
            origin = bars[i]
            move = bars[i + 1]

            if move["close"] > move["open"]:  # bullish move
                # Find last bearish candle as origin
                for j in range(i, max(i - OB_LOOKBACK, -1), -1):
                    if bars[j]["close"] < bars[j]["open"]:
                        obs.append(
                            OrderBlock(
                                high=bars[j]["high"],
                                low=bars[j]["low"],
                                ob_type="bullish",
                                timeframe=timeframe,
                                strength=0.75,
                            )
                        )
                        break
            else:  # bearish move
                for j in range(i, max(i - OB_LOOKBACK, -1), -1):
                    if bars[j]["close"] > bars[j]["open"]:
                        obs.append(
                            OrderBlock(
                                high=bars[j]["high"],
                                low=bars[j]["low"],
                                ob_type="bearish",
                                timeframe=timeframe,
                                strength=0.75,
                            )
                        )
                        break

        # Deduplicate close OBs
        return self._dedup_obs(obs)

    def _dedup_obs(self, obs: list[OrderBlock]) -> list[OrderBlock]:
        if not obs:
            return obs
        obs.sort(key=lambda x: (x.high + x.low) / 2)
        result = [obs[0]]
        for ob in obs[1:]:
            last = result[-1]
            if abs((ob.high + ob.low) / 2 - (last.high + last.low) / 2) < 1.0:
                # Merge
                last.high = max(last.high, ob.high)
                last.low = min(last.low, ob.low)
                last.strength = max(last.strength, ob.strength)
            else:
                result.append(ob)
        return result

    # ── Fibonacci Retracements ────────────────────────────────────────────────

    def _detect_fibonacci(
        self, bars: list[dict[str, Any]], timeframe: str
    ) -> list[StructuralLevel]:
        """Draw fibs from most recent significant swing high to swing low."""
        if len(bars) < 20:
            return []

        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]

        recent_high_idx = max(range(len(highs) - 10, len(highs)), key=lambda i: highs[i])
        recent_low_idx = min(range(len(lows) - 10, len(lows)), key=lambda i: lows[i])

        if recent_high_idx <= recent_low_idx:
            return []

        swing_high = highs[recent_high_idx]
        swing_low = lows[recent_low_idx]
        diff = swing_high - swing_low
        if diff < 1.0:
            return []

        levels: list[StructuralLevel] = []
        for ratio in (0.382, 0.5, 0.618):
            price = round(swing_low + diff * ratio, 2)
            levels.append(
                StructuralLevel(
                    price=price,
                    level_type="fib",
                    strength=0.6,
                    timeframe=timeframe,
                    metadata={"ratio": ratio, "from": swing_low, "to": swing_high},
                )
            )
        return levels
