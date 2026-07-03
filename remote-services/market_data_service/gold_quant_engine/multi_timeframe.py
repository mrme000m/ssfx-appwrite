"""Multi-Timeframe Confluence Engine — aggregates ≥3 timeframe readings."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from .models import MultiTimeframeConfluence, TimeframeReading

logger = logging.getLogger(__name__)


# Scoring weights per indicator type
INDICATOR_WEIGHTS = {
    "ema_alignment": 0.25,
    "rsi_extreme": 0.15,
    "macd_histogram": 0.10,
    "volume_delta": 0.15,
    "structure_break": 0.25,
    "key_level_proximity": 0.10,
}

# Timeframe preference order for gold
TIMEFRAME_PRIORITY = ["M15", "H1", "H4", "D1"]


class MultiTimeframeEngine:
    """Computes confluence scores across M15, H1, H4, (optionally D1)."""

    def __init__(self, min_confluence_tfs: int = 3) -> None:
        self.min_confluence_tfs = min_confluence_tfs

    def compute(
        self,
        readings: list[TimeframeReading],
    ) -> MultiTimeframeConfluence:
        """
        Given per-timeframe readings, compute overall confluence.
        Requires at least `min_confluence_tfs` readings.
        """
        result = MultiTimeframeConfluence(readings=readings)

        if len(readings) < self.min_confluence_tfs:
            result.overall_direction = "NEUTRAL"
            result.confidence = 0.0
            result.reasons = [f"Insufficient data: {len(readings)} TFs (need {self.min_confluence_tfs})"]
            return result

        # Count directions
        bull_count = sum(1 for r in readings if r.score > 0.30)
        bear_count = sum(1 for r in readings if r.score < -0.30)
        neutral_count = len(readings) - bull_count - bear_count

        result.bull_count = bull_count
        result.bear_count = bear_count
        result.neutral_count = neutral_count
        result.factors = {r.timeframe: r.score for r in readings}

        # Determine overall direction
        if bull_count >= 3:
            result.overall_direction = "STRONGLY_BULLISH"
            result.confidence = min(1.0, 0.60 + 0.08 * bull_count)
        elif bull_count >= 2 and bear_count == 0:
            result.overall_direction = "BULLISH"
            result.confidence = 0.55
        elif bear_count >= 3:
            result.overall_direction = "STRONGLY_BEARISH"
            result.confidence = min(1.0, 0.60 + 0.08 * bear_count)
        elif bear_count >= 2 and bull_count == 0:
            result.overall_direction = "BEARISH"
            result.confidence = 0.55
        else:
            result.overall_direction = "NEUTRAL"
            result.confidence = max(0.0, 0.40 - 0.05 * (bull_count + bear_count))

        return result

    @staticmethod
    def score_timeframe(
        bars: list[dict[str, Any]],
        timeframe: str,
        order_flow_score: float = 0.0,
    ) -> TimeframeReading:
        """
        Compute a single timeframe reading from OHLCV bars + optional order-flow score.
        Returns a TimeframeReading with direction, score, indicators list, regime.
        """
        if len(bars) < 50:
            return TimeframeReading(timeframe=timeframe, direction="neutral", score=0.0)

        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]

        score = 0.0
        indicators: list[dict[str, Any]] = []
        regime = "ranging"

        import numpy as np

        # 1. EMA alignment
        ema10 = _ema(np.array(closes), 10)
        ema20 = _ema(np.array(closes), 20)
        ema50 = _ema(np.array(closes), 50)
        if len(ema10) >= 3 and len(ema20) >= 3 and len(ema50) >= 3:
            if ema10[-1] > ema20[-1] > ema50[-1]:
                score += INDICATOR_WEIGHTS["ema_alignment"]
                indicators.append({"name": "EMA10>20>50", "value": "bullish", "weight": INDICATOR_WEIGHTS["ema_alignment"]})
                regime = "uptrend"
            elif ema10[-1] < ema20[-1] < ema50[-1]:
                score -= INDICATOR_WEIGHTS["ema_alignment"]
                indicators.append({"name": "EMA10<20<50", "value": "bearish", "weight": -INDICATOR_WEIGHTS["ema_alignment"]})
                regime = "downtrend"
            else:
                regime = "ranging"

            # Crossover detection
            if len(ema10) > 1 and len(ema20) > 1:
                if ema10[-2] <= ema20[-2] and ema10[-1] > ema20[-1]:
                    score += 0.10
                    indicators.append({"name": "EMA10_cross_up", "value": True, "weight": 0.10})
                elif ema10[-2] >= ema20[-2] and ema10[-1] < ema20[-1]:
                    score -= 0.10
                    indicators.append({"name": "EMA10_cross_down", "value": True, "weight": -0.10})

        # 2. RSI
        rsi = _rsi(np.array(closes), 14)
        if rsi is not None:
            if rsi < 30:
                score += INDICATOR_WEIGHTS["rsi_extreme"]
                indicators.append({"name": "RSI14", "value": rsi, "signal": "oversold", "weight": INDICATOR_WEIGHTS["rsi_extreme"]})
            elif rsi > 70:
                score -= INDICATOR_WEIGHTS["rsi_extreme"]
                indicators.append({"name": "RSI14", "value": rsi, "signal": "overbought", "weight": -INDICATOR_WEIGHTS["rsi_extreme"]})

        # 3. MACD
        macd = _macd(np.array(closes))
        if macd:
            if macd["histogram"] > 0:
                score += INDICATOR_WEIGHTS["macd_histogram"]
                indicators.append({"name": "MACD_hist", "value": macd["histogram"], "signal": "positive", "weight": INDICATOR_WEIGHTS["macd_histogram"]})
            else:
                score -= INDICATOR_WEIGHTS["macd_histogram"]
                indicators.append({"name": "MACD_hist", "value": macd["histogram"], "signal": "negative", "weight": -INDICATOR_WEIGHTS["macd_histogram"]})

        # 4. Structure break (simple: close beyond recent high/low)
        if len(closes) >= 10:
            recent_high = max(highs[-10:])
            recent_low = min(lows[-10:])
            if closes[-1] > recent_high:
                score += INDICATOR_WEIGHTS["structure_break"]
                indicators.append({"name": "BOS", "value": "bullish", "weight": INDICATOR_WEIGHTS["structure_break"]})
                regime = "breakout"
            elif closes[-1] < recent_low:
                score -= INDICATOR_WEIGHTS["structure_break"]
                indicators.append({"name": "BOS", "value": "bearish", "weight": -INDICATOR_WEIGHTS["structure_break"]})
                regime = "breakout"

        # 5. Order flow score injection
        if abs(order_flow_score) > 0.1:
            score += order_flow_score * INDICATOR_WEIGHTS["volume_delta"]
            indicators.append({"name": "order_flow", "value": order_flow_score, "weight": order_flow_score * INDICATOR_WEIGHTS["volume_delta"]})

        # Clamp
        score = max(-1.0, min(1.0, score))

        direction = "neutral"
        if score > 0.30:
            direction = "bullish"
        elif score < -0.30:
            direction = "bearish"

        return TimeframeReading(
            timeframe=timeframe,
            direction=direction,
            score=round(score, 4),
            indicators=indicators,
            regime=regime,
        )


# ── Indicator math helpers ───────────────────────────────────────────────────


def _ema(data: np.ndarray, period: int) -> np.ndarray:
    k = 2.0 / (period + 1)
    ema = np.zeros_like(data, dtype=float)
    ema[period - 1] = float(np.mean(data[:period]))
    for i in range(period, len(data)):
        ema[i] = data[i] * k + ema[i - 1] * (1 - k)
    return ema


def _rsi(data: np.ndarray, period: int = 14) -> float | None:
    if len(data) < period + 1:
        return None
    deltas = np.diff(data)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    return float(100.0 - 100.0 / (1.0 + avg_gain / avg_loss))


def _macd(data: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, float] | None:
    if len(data) < slow + signal:
        return None
    ema_fast = _ema(data, fast)
    ema_slow = _ema(data, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    histogram = macd_line[-1] - signal_line[-1]
    return {
        "macd": float(macd_line[-1]),
        "signal": float(signal_line[-1]),
        "histogram": float(histogram),
    }
