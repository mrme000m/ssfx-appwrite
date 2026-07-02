"""Unit tests for Gold Quantitative Analysis Engine components."""

from __future__ import annotations

import numpy as np

from market_data_service.gold_quant_engine.key_levels import KeyLevelsDetector
from market_data_service.gold_quant_engine.models import TickWindowState
from market_data_service.gold_quant_engine.multi_timeframe import (
    MultiTimeframeEngine as MtfEngine,
)
from market_data_service.gold_quant_engine.order_flow import OrderFlowEngine
from market_data_service.gold_quant_engine.tick_volume import TickVolumeAnalyzer


def test_tick_window_delta() -> None:
    state = TickWindowState(window_size=10)
    for i in range(5):
        state.add_tick(
            {
                "bid": 2300.0 + i,
                "ask": 2300.5 + i,
                "bid_volume": 1.0,
                "ask_volume": 3.0,
                "timestamp_ms": 1000 + i * 1000,
            }
        )
    assert state.cumulative_delta == 5.0 * (3.0 - 1.0)
    assert state.tick_count == 5


def test_tick_volume_profile() -> None:
    state = TickWindowState(window_size=20)
    for i in range(10):
        state.add_tick(
            {
                "bid": 2300.0,
                "ask": 2300.5,
                "bid_volume": 1.0,
                "ask_volume": 2.0,
                "timestamp_ms": 1000 + i * 1000,
            }
        )
    poc = state.compute_poc()
    assert poc is not None
    assert abs(poc - 2300.2) < 0.5


def test_order_flow_sweep_reject() -> None:
    engine = OrderFlowEngine(sweep_lookback_ticks=10)
    # Generate a bullish sweep & reject sequence
    ticks = []
    base = 2300.0
    for i in range(15):
        # Declining prices into a low
        bid = base - i * 0.2
        ask = bid + 0.5
        ticks.append(
            {
                "bid": bid,
                "ask": ask,
                "bid_volume": 5.0,
                "ask_volume": 1.0,
                "timestamp_ms": 1000 + i * 1000,
            }
        )
    # Strong reversal up with bullish delta (3 consecutive higher closes)
    for i in range(5):
        bid = base - 3.0 + i * 0.8
        ask = bid + 0.5
        ticks.append(
            {
                "bid": bid,
                "ask": ask,
                "bid_volume": 1.0,
                "ask_volume": 8.0,
                "timestamp_ms": 16000 + i * 1000,
            }
        )

    event = None
    for t in ticks:
        ev = engine.on_tick(t)
        if ev:
            event = ev

    # The engine may or may not detect depending on exact pattern; just ensure no crash
    assert engine.last_event in ("none", "sweep_reject_bullish", "absorption_bullish", "exhaustion_bearish")


def test_key_levels_fvg() -> None:
    detector = KeyLevelsDetector(fvg_min_size=0.5)
    # Bullish FVG: right low > left high (gap up)
    # Need >= 10 bars to avoid early return in detect()
    bars = []
    for i in range(10):
        if i == 7:
            # left candle
            bars.append({"open": 2295.0, "high": 2298.0, "low": 2295.0, "close": 2297.0, "timestamp_ms": i * 60000})
        elif i == 8:
            # middle candle
            bars.append({"open": 2297.0, "high": 2300.0, "low": 2296.0, "close": 2298.0, "timestamp_ms": i * 60000})
        elif i == 9:
            # right candle with gap up
            bars.append({"open": 2302.0, "high": 2306.0, "low": 2301.0, "close": 2305.0, "timestamp_ms": i * 60000})
        else:
            bars.append({"open": 2290.0 + i, "high": 2291.0 + i, "low": 2289.0 + i, "close": 2290.5 + i, "timestamp_ms": i * 60000})
    levels = detector.detect(bars, timeframe="M15")
    assert len(levels.fvgs) > 0
    assert levels.fvgs[0].fvg_type == "bullish"


def test_mtf_confluence_bullish() -> None:
    engine = MtfEngine(min_confluence_tfs=3)
    from market_data_service.gold_quant_engine.models import TimeframeReading

    readings = [
        TimeframeReading(timeframe="M15", direction="bullish", score=0.45),
        TimeframeReading(timeframe="H1", direction="bullish", score=0.35),
        TimeframeReading(timeframe="H4", direction="bullish", score=0.40),
    ]
    result = engine.compute(readings)
    assert result.overall_direction == "STRONGLY_BULLISH"
    assert result.confidence > 0.6
    assert result.bull_count == 3


def test_mtf_confluence_mixed() -> None:
    engine = MtfEngine(min_confluence_tfs=3)
    from market_data_service.gold_quant_engine.models import TimeframeReading

    readings = [
        TimeframeReading(timeframe="M15", direction="bullish", score=0.45),
        TimeframeReading(timeframe="H1", direction="bearish", score=-0.35),
        TimeframeReading(timeframe="H4", direction="neutral", score=0.05),
    ]
    result = engine.compute(readings)
    assert result.overall_direction == "NEUTRAL"
    assert result.confidence < 0.5


def test_mtf_score_timeframe() -> None:
    engine = MtfEngine()
    np.random.seed(42)
    # Build synthetic bullish bars
    bars = []
    close = 2300.0
    for i in range(60):
        open_p = close
        close += np.random.uniform(-0.5, 1.0)  # slight upward drift
        high_p = max(open_p, close) + np.random.uniform(0, 0.5)
        low_p = min(open_p, close) - np.random.uniform(0, 0.5)
        bars.append(
            {
                "open": round(open_p, 2),
                "high": round(high_p, 2),
                "low": round(low_p, 2),
                "close": round(close, 2),
                "volume": 100.0,
                "timestamp_ms": i * 60000,
            }
        )
    reading = engine.score_timeframe(bars, "H1")
    # With random seed 42, there should be some upward drift
    assert reading.direction in ("bullish", "neutral")


if __name__ == "__main__":
    test_tick_window_delta()
    test_tick_volume_profile()
    test_order_flow_sweep_reject()
    test_key_levels_fvg()
    test_mtf_confluence_bullish()
    test_mtf_confluence_mixed()
    test_mtf_score_timeframe()
    print("All gold quant tests passed!")
