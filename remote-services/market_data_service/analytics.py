"""Real-time market analytics engine — indicators, signals, structure detection."""

from __future__ import annotations

import contextlib
import logging

import numpy as np

from .database import db_manager
from .models import (
    MarketStructureInfo,
    OHLCVBar,
    TechnicalIndicator,
    TimeFrame,
    TradingSignal,
)

logger = logging.getLogger(__name__)


class AnalyticsEngine:
    """Computes technical indicators, trading signals, and market structure."""

    def __init__(self) -> None:
        self._series_cache: dict[tuple[int, TimeFrame], list[OHLCVBar]] = {}

    async def compute_sma(self, bars: list[OHLCVBar], period: int) -> float | None:
        if len(bars) < period:
            return None
        closes = np.array([b.close for b in bars[-period:]])
        return float(np.mean(closes))

    async def compute_ema(self, bars: list[OHLCVBar], period: int) -> float | None:
        if len(bars) < period:
            return None
        closes = np.array([b.close for b in bars])
        k = 2.0 / (period + 1)
        # Seed EMA with SMA of first `period` values, then iterate over remainder
        ema = float(np.mean(closes[:period]))
        for c in closes[period:]:
            ema = c * k + ema * (1 - k)
        return float(ema)

    async def compute_rsi(self, bars: list[OHLCVBar], period: int = 14) -> float | None:
        if len(bars) < period + 1:
            return None
        closes = np.array([b.close for b in bars[-(period + 1):]])
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        if avg_loss == 0:
            return 100.0
        return float(100.0 - 100.0 / (1.0 + avg_gain / avg_loss))

    async def compute_atr(self, bars: list[OHLCVBar], period: int = 14) -> float | None:
        if len(bars) < period + 1:
            return None
        recent = bars[-(period + 1):]
        highs = np.array([b.high for b in recent[1:]])
        lows = np.array([b.low for b in recent[1:]])
        prev_closes = np.array([b.close for b in recent[:-1]])
        tr = np.maximum(
            highs - lows,
            np.maximum(
                np.abs(highs - prev_closes),
                np.abs(lows - prev_closes),
            ),
        )
        return float(np.mean(tr))

    async def compute_macd(
        self, bars: list[OHLCVBar], fast: int = 12, slow: int = 26, signal: int = 9
    ) -> dict[str, float] | None:
        if len(bars) < slow + signal:
            return None
        closes = np.array([b.close for b in bars])
        ema_fast = self._ema_array(closes, fast)
        ema_slow = self._ema_array(closes, slow)
        macd_line = ema_fast - ema_slow
        signal_line = self._ema_array(macd_line, signal)
        histogram = macd_line[-1] - signal_line[-1]
        return {
            "macd": float(macd_line[-1]),
            "signal": float(signal_line[-1]),
            "histogram": float(histogram),
        }

    async def compute_bollinger(
        self, bars: list[OHLCVBar], period: int = 20, std_mult: float = 2.0
    ) -> dict[str, float] | None:
        if len(bars) < period:
            return None
        closes = np.array([b.close for b in bars[-period:]])
        sma = np.mean(closes)
        std = np.std(closes)
        return {
            "upper": float(sma + std_mult * std),
            "middle": float(sma),
            "lower": float(sma - std_mult * std),
        }

    async def compute_all_indicators(
        self, symbol_id: int, symbol_name: str, timeframe: TimeFrame, bars: list[OHLCVBar]
    ) -> list[TechnicalIndicator]:
        if not bars:
            return []
        ts = bars[0].timestamp_ms
        indicators: list[TechnicalIndicator] = []

        for period in [10, 20, 50]:
            val = await self.compute_sma(bars, period)
            if val is not None:
                indicators.append(TechnicalIndicator(
                    symbol_id=symbol_id,
                    symbol_name=symbol_name,
                    indicator=f"SMA{period}",
                    timeframe=timeframe,
                    period=period,
                    value=val,
                    timestamp_ms=ts,
                ))

        for period in [12, 26]:
            val = await self.compute_ema(bars, period)
            if val is not None:
                indicators.append(TechnicalIndicator(
                    symbol_id=symbol_id,
                    symbol_name=symbol_name,
                    indicator=f"EMA{period}",
                    timeframe=timeframe,
                    period=period,
                    value=val,
                    timestamp_ms=ts,
                ))

        val = await self.compute_rsi(bars, 14)
        if val is not None:
            indicators.append(TechnicalIndicator(
                symbol_id=symbol_id,
                symbol_name=symbol_name,
                indicator="RSI14",
                timeframe=timeframe,
                period=14,
                value=val,
                timestamp_ms=ts,
            ))

        val = await self.compute_atr(bars, 14)
        if val is not None:
            indicators.append(TechnicalIndicator(
                symbol_id=symbol_id,
                symbol_name=symbol_name,
                indicator="ATR14",
                timeframe=timeframe,
                period=14,
                value=val,
                timestamp_ms=ts,
            ))

        val = await self.compute_macd(bars)
        if val is not None:
            indicators.append(TechnicalIndicator(
                symbol_id=symbol_id,
                symbol_name=symbol_name,
                indicator="MACD",
                timeframe=timeframe,
                period=12,
                value=val,
                timestamp_ms=ts,
            ))

        val = await self.compute_bollinger(bars)
        if val is not None:
            indicators.append(TechnicalIndicator(
                symbol_id=symbol_id,
                symbol_name=symbol_name,
                indicator="BB20",
                timeframe=timeframe,
                period=20,
                value=val,
                timestamp_ms=ts,
            ))

        # Persist
        with contextlib.suppress(Exception):
            for ind in indicators:
                await db_manager.store_indicator(ind)

        return indicators

    # ── Trading signals ──────────────────────────────────────────────────────

    async def generate_signals(
        self, symbol_id: int, symbol_name: str, timeframe: TimeFrame, bars: list[OHLCVBar]
    ) -> list[TradingSignal]:
        if len(bars) < 50:
            return []
        signals: list[TradingSignal] = []
        ts = bars[0].timestamp_ms
        reasons: list[str] = []
        confidence = 0.0

        # SMA cross
        sma10 = await self.compute_sma(bars, 10)
        sma20 = await self.compute_sma(bars, 20)
        sma50 = await self.compute_sma(bars, 50)

        if sma10 and sma20 and sma50:
            if sma10 > sma20 > sma50:
                reasons.append("SMA alignment bullish (10>20>50)")
                confidence += 0.25
            elif sma10 < sma20 < sma50:
                reasons.append("SMA alignment bearish (10<20<50)")
                confidence -= 0.25

            # Golden/Death cross
            prev_sma10 = await self.compute_sma(bars[:-1], 10)
            prev_sma20 = await self.compute_sma(bars[:-1], 20)
            if prev_sma10 and prev_sma20:
                if prev_sma10 <= prev_sma20 and sma10 > sma20:
                    reasons.append("Golden cross (SMA10 crossed above SMA20)")
                    confidence += 0.30
                elif prev_sma10 >= prev_sma20 and sma10 < sma20:
                    reasons.append("Death cross (SMA10 crossed below SMA20)")
                    confidence -= 0.30

        # RSI
        rsi = await self.compute_rsi(bars, 14)
        if rsi is not None:
            if rsi < 30:
                reasons.append(f"RSI oversold ({rsi:.1f})")
                confidence += 0.20
            elif rsi > 70:
                reasons.append(f"RSI overbought ({rsi:.1f})")
                confidence -= 0.20

        # MACD
        macd = await self.compute_macd(bars)
        if macd:
            if macd["histogram"] > 0:
                reasons.append("MACD histogram positive")
                confidence += 0.15
            else:
                reasons.append("MACD histogram negative")
                confidence -= 0.15

        # Determine signal
        signal_type = "NEUTRAL"
        if confidence >= 0.5:
            signal_type = "BUY"
        elif confidence <= -0.5:
            signal_type = "SELL"

        abs_conf = min(abs(confidence), 1.0)
        if signal_type != "NEUTRAL" or reasons:
            signals.append(TradingSignal(
                symbol_id=symbol_id,
                symbol_name=symbol_name,
                direction=signal_type,
                strength=abs_conf,
                indicators=reasons,
                confidence=abs_conf,
                timestamp_ms=ts,
                timeframe=timeframe,
            ))

        for sig in signals:
            with contextlib.suppress(Exception):
                await db_manager.store_signal(sig)

        return signals

    # ── Market structure ─────────────────────────────────────────────────────

    async def detect_market_structure(
        self, symbol_id: int, symbol_name: str, timeframe: TimeFrame, bars: list[OHLCVBar]
    ) -> MarketStructureInfo | None:
        if len(bars) < 20:
            return None

        highs = [b.high for b in bars]
        lows = [b.low for b in bars]
        closes = [b.close for b in bars]

        # Swing highs/lows (simple local extrema, 2-bar lookback)
        swing_highs = []
        swing_lows = []
        for i in range(2, len(bars) - 2):
            if highs[i] == max(highs[i - 2:i + 3]):
                swing_highs.append({"price": highs[i], "index": i, "timestamp_ms": bars[i].timestamp_ms})
            if lows[i] == min(lows[i - 2:i + 3]):
                swing_lows.append({"price": lows[i], "index": i, "timestamp_ms": bars[i].timestamp_ms})

        # Support/Resistance from recent swing points
        support_levels = sorted({s["price"] for s in swing_lows[-5:]}, reverse=True)
        resistance_levels = sorted({s["price"] for s in swing_highs[-5:]})

        # Trend direction
        sma20 = await self.compute_sma(bars, 20)
        trend = "neutral"
        if sma20:
            if closes[-1] > sma20 * 1.001:
                trend = "bullish"
            elif closes[-1] < sma20 * 0.999:
                trend = "bearish"

        # Volatility regime
        atr = await self.compute_atr(bars, 14)
        vol_regime = "normal"
        if atr:
            avg_range = np.mean([b.range for b in bars[-20:]])
            if avg_range > 0:
                ratio = atr / avg_range
                if ratio > 1.5:
                    vol_regime = "high"
                elif ratio < 0.5:
                    vol_regime = "low"

        structure = MarketStructureInfo(
            symbol_id=symbol_id,
            symbol_name=symbol_name,
            timeframe=timeframe,
            swing_highs=swing_highs[-10:],
            swing_lows=swing_lows[-10:],
            support_levels=support_levels[:5],
            resistance_levels=resistance_levels[:5],
            trend=trend,
            volatility_regime=vol_regime,
            timestamp_ms=bars[0].timestamp_ms,
        )
        with contextlib.suppress(Exception):
            await db_manager.store_market_structure(structure)
        return structure

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _ema_array(data: np.ndarray, period: int) -> np.ndarray:
        k = 2.0 / (period + 1)
        ema = np.zeros_like(data)
        # Seed with SMA of first `period` values for standard behaviour
        ema[period - 1] = float(np.mean(data[:period]))
        for i in range(period, len(data)):
            ema[i] = data[i] * k + ema[i - 1] * (1 - k)
        return ema
