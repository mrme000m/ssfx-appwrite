"""Integration smoke test for Gold Quant Engine end-to-end flow."""

from __future__ import annotations

import numpy as np

from market_data_service.gold_quant_engine import GoldQuantEngine
from market_data_service.gold_quant_engine.models import TimeframeReading
from market_data_service.gold_quant_engine.multi_timeframe import MultiTimeframeEngine


def _make_bars(n: int, direction: str = "up") -> list[dict]:
    """Generate synthetic OHLCV bars."""
    bars = []
    close = 2300.0
    for i in range(n):
        open_p = close
        drift = 0.5 if direction == "up" else (-0.5 if direction == "down" else 0.0)
        close += np.random.uniform(-0.5, 1.0) + drift
        high_p = max(open_p, close) + np.random.uniform(0, 0.5)
        low_p = min(open_p, close) - np.random.uniform(0, 0.5)
        bars.append(
            {
                "open": round(open_p, 2),
                "high": round(high_p, 2),
                "low": round(low_p, 2),
                "close": round(close, 2),
                "volume": 100.0,
                "timestamp_ms": i * 900000,
            }
        )
    return bars


def _make_ticks(n: int, base_price: float = 2300.0) -> list[dict]:
    """Generate synthetic ticks with alternating delta."""
    ticks = []
    price = base_price
    for i in range(n):
        price += np.random.uniform(-0.1, 0.1)
        bid = round(price, 2)
        ask = round(price + 0.25, 2)
        ticks.append(
            {
                "symbol_id": 1,
                "symbol_name": "XAUUSD",
                "bid": bid,
                "ask": ask,
                "bid_volume": 2.0,
                "ask_volume": 5.0,
                "timestamp_ms": 1000 + i * 100,
            }
        )
    return ticks


def test_end_to_end() -> None:
    print("Creating GoldQuantEngine...")
    engine = GoldQuantEngine(
        symbol="XAUUSD",
        symbol_id=1,
        tick_window_size=200,
        timeframes=["M15", "H1", "H4"],
        min_confluence_tfs=3,
    )

    print("Feeding synthetic M15 bars (bullish)...")
    engine.set_bars("M15", _make_bars(80, "up"))

    print("Feeding synthetic H1 bars (bullish)...")
    engine.set_bars("H1", _make_bars(80, "up"))

    print("Feeding synthetic H4 bars (bullish)...")
    engine.set_bars("H4", _make_bars(80, "up"))

    print("Feeding synthetic ticks...")
    for t in _make_ticks(150):
        engine.ingest_tick(t)

    print("Computing snapshot...")
    snapshot = engine.get_snapshot_sync()

    print(f"  Price: bid={snapshot.bid} ask={snapshot.ask}")
    print(f"  MTF: {snapshot.mtf.overall_direction} (confidence={snapshot.mtf.confidence})")
    for r in snapshot.mtf.readings:
        print(f"    {r.timeframe}: {r.direction} score={r.score:+.2f}")

    print(f"  Order Flow: delta_regime={snapshot.order_flow.delta_regime} z={snapshot.order_flow.delta_z_score:.2f}")
    print(f"    POC={snapshot.order_flow.poc} VAH={snapshot.order_flow.vah} VAL={snapshot.order_flow.val}")

    print(f"  Key Levels: {len(snapshot.key_levels.support)} support, {len(snapshot.key_levels.resistance)} resistance")
    print(f"    FVGs={len(snapshot.key_levels.fvgs)} OBs={len(snapshot.key_levels.order_blocks)}")

    print(f"  Decisions:")
    print(f"    Short: {snapshot.decision.short_entry.verdict} (conf={snapshot.decision.short_entry.confidence})")
    print(f"    Long:  {snapshot.decision.long_entry.verdict} (conf={snapshot.decision.long_entry.confidence})")
    print(f"    Limit: {snapshot.decision.limit_order.verdict} (conf={snapshot.decision.limit_order.confidence})")

    print(f"  Prompt length: {len(snapshot.agent_prompt)} chars")

    # Verify the pipeline produced a valid snapshot with structural data
    assert snapshot.mtf is not None
    assert len(snapshot.mtf.readings) == 3  # M15, H1, H4 all computed
    assert snapshot.order_flow.poc is not None
    assert snapshot.order_flow.vah is not None
    assert snapshot.order_flow.val is not None
    assert len(snapshot.key_levels.fvgs) > 0  # FVGs detected from bars
    assert len(snapshot.agent_prompt) > 500  # Prompt generated
    # Decisions should have valid verdicts
    assert snapshot.decision.short_entry.verdict in ("ENTER", "REJECT", "WAIT")
    assert snapshot.decision.long_entry.verdict in ("ENTER", "REJECT", "WAIT")
    assert snapshot.decision.limit_order.verdict in ("ENTER", "REJECT", "WAIT")
    print("\nSUCCESS: End-to-end gold quant engine integration works correctly.")


if __name__ == "__main__":
    test_end_to_end()
