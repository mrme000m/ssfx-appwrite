"""Order Flow Engine — detects structural flow events from tick + depth data."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class OrderFlowEngine:
    """Detects sweeps, absorption, exhaustion, and imbalance from tick stream."""

    def __init__(self, sweep_lookback_ticks: int = 50, min_sweep_delta_pct: float = 0.3) -> None:
        self.sweep_lookback = sweep_lookback_ticks
        self.min_sweep_delta_pct = min_sweep_delta_pct
        self._recent_ticks: list[dict[str, Any]] = []
        self._recent_highs: list[float] = []
        self._recent_lows: list[float] = []
        self._last_event: str = "none"
        self._last_event_price: float | None = None
        self._last_event_ms: int | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    def on_tick(self, tick: dict[str, Any]) -> str | None:
        """
        Process a tick and return an event name if a new flow event is detected.
        Possible returns: sweep_reject_bullish, sweep_reject_bearish,
        absorption_bullish, absorption_bearish, exhaustion, None
        """
        self._recent_ticks.append(tick)
        if len(self._recent_ticks) > self.sweep_lookback * 3:
            self._recent_ticks.pop(0)

        mid = (tick.get("bid", 0.0) + tick.get("ask", 0.0)) / 2
        if mid == 0:
            return None

        self._recent_highs.append(tick.get("ask", mid))
        self._recent_lows.append(tick.get("bid", mid))
        if len(self._recent_highs) > self.sweep_lookback:
            self._recent_highs.pop(0)
            self._recent_lows.pop(0)

        # Check events in order of priority
        event = self._detect_sweep_reject(tick, mid)
        if event:
            return event

        event = self._detect_exhaustion(tick, mid)
        if event:
            return event

        event = self._detect_absorption(tick, mid)
        if event:
            return event

        return None

    def on_depth(self, depth: dict[str, Any]) -> dict[str, Any]:
        """Process an order-book depth snapshot/delta."""
        bids = depth.get("bids", [])
        asks = depth.get("asks", [])
        bid_depth = sum(b.get("volume", 0.0) for b in bids)
        ask_depth = sum(a.get("volume", 0.0) for a in asks)
        total = bid_depth + ask_depth
        imbalance = (bid_depth - ask_depth) / total if total > 0 else 0.0
        return {
            "bid_depth": bid_depth,
            "ask_depth": ask_depth,
            "imbalance": imbalance,
        }

    @property
    def last_event(self) -> str:
        return self._last_event

    @property
    def last_event_price(self) -> float | None:
        return self._last_event_price

    @property
    def last_event_ms(self) -> int | None:
        return self._last_event_ms

    # ── Detection internals ───────────────────────────────────────────────────

    def _detect_sweep_reject(self, tick: dict[str, Any], mid: float) -> str | None:
        """
        Sweep & reject logic:
        - Price pierces a recent swing low/high
        - But delta is weak (opposite colour = no follow-through)
        - Then reverses with strong delta in opposite direction
        """
        if len(self._recent_ticks) < self.sweep_lookback:
            return None

        recent_low = min(self._recent_lows[-self.sweep_lookback :])
        recent_high = max(self._recent_highs[-self.sweep_lookback :])

        # Safe margin
        margin = max(0.05, (recent_high - recent_low) * 0.05)

        # Bearish sweep (pierce high, reject down)
        if tick.get("ask", mid) >= recent_high - margin:
            # Look back for weak delta at the sweep
            window = self._recent_ticks[-10:]
            total_vol = sum(
                (t.get("bid_volume", 0.0) or 0.0) + (t.get("ask_volume", 0.0) or 0.0)
                for t in window
            )
            total_delta = sum(
                (t.get("ask_volume", 0.0) or 0.0) - (t.get("bid_volume", 0.0) or 0.0)
                for t in window
            )
            if total_vol > 0 and (total_delta / total_vol) < self.min_sweep_delta_pct:
                # Now check if reversing down
                if self._is_reversing_down(window):
                    self._record_event("sweep_reject_bearish", mid, tick.get("timestamp_ms"))
                    return "sweep_reject_bearish"

        # Bullish sweep (pierce low, reject up)
        if tick.get("bid", mid) <= recent_low + margin:
            window = self._recent_ticks[-10:]
            total_vol = sum(
                (t.get("bid_volume", 0.0) or 0.0) + (t.get("ask_volume", 0.0) or 0.0)
                for t in window
            )
            total_delta = sum(
                (t.get("ask_volume", 0.0) or 0.0) - (t.get("bid_volume", 0.0) or 0.0)
                for t in window
            )
            if total_vol > 0 and (total_delta / total_vol) > -self.min_sweep_delta_pct:
                if self._is_reversing_up(window):
                    self._record_event("sweep_reject_bullish", mid, tick.get("timestamp_ms"))
                    return "sweep_reject_bullish"

        return None

    def _detect_exhaustion(self, tick: dict[str, Any], mid: float) -> str | None:
        """
        Exhaustion: large delta spike with minimal price follow-through.
        """
        if len(self._recent_ticks) < 20:
            return None

        window = self._recent_ticks[-20:]
        total_delta = sum(
            (t.get("ask_volume", 0.0) or 0.0) - (t.get("bid_volume", 0.0) or 0.0)
            for t in window
        )
        total_vol = sum(
            (t.get("bid_volume", 0.0) or 0.0) + (t.get("ask_volume", 0.0) or 0.0)
            for t in window
        )
        if total_vol == 0:
            return None

        delta_pct = abs(total_delta) / total_vol
        price_range = max(
            (t.get("ask", mid) or mid) for t in window
        ) - min((t.get("bid", mid) or mid) for t in window)

        # High delta % but small price range = exhaustion
        if delta_pct > 0.6 and price_range < 0.30:
            direction = "bullish" if total_delta > 0 else "bearish"
            event = f"exhaustion_{direction}"
            self._record_event(event, mid, tick.get("timestamp_ms"))
            return event

        return None

    def _detect_absorption(self, tick: dict[str, Any], mid: float) -> str | None:
        """
        Absorption: high volume at a tight price range = institutional size.
        """
        if len(self._recent_ticks) < 15:
            return None

        window = self._recent_ticks[-15:]
        total_vol = sum(
            (t.get("bid_volume", 0.0) or 0.0) + (t.get("ask_volume", 0.0) or 0.0)
            for t in window
        )
        price_range = max(
            (t.get("ask", mid) or mid) for t in window
        ) - min((t.get("bid", mid) or mid) for t in window)

        # High volume, small range = absorption
        if total_vol > 50 and price_range < 0.20:
            total_delta = sum(
                (t.get("ask_volume", 0.0) or 0.0) - (t.get("bid_volume", 0.0) or 0.0)
                for t in window
            )
            direction = "bullish" if total_delta > 0 else "bearish"
            event = f"absorption_{direction}"
            self._record_event(event, mid, tick.get("timestamp_ms"))
            return event

        return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _is_reversing_down(self, window: list[dict[str, Any]]) -> bool:
        if len(window) < 3:
            return False
        mids = [(t.get("bid", 0.0) + t.get("ask", 0.0)) / 2 for t in window]
        return mids[-1] < mids[-2] < mids[-3]

    def _is_reversing_up(self, window: list[dict[str, Any]]) -> bool:
        if len(window) < 3:
            return False
        mids = [(t.get("bid", 0.0) + t.get("ask", 0.0)) / 2 for t in window]
        return mids[-1] > mids[-2] > mids[-3]

    def _record_event(self, event: str, price: float, ts_ms: Any) -> None:
        self._last_event = event
        self._last_event_price = price
        try:
            self._last_event_ms = int(ts_ms) if ts_ms else None
        except (TypeError, ValueError):
            self._last_event_ms = None

    def reset(self) -> None:
        self._recent_ticks.clear()
        self._recent_highs.clear()
        self._recent_lows.clear()
        self._last_event = "none"
        self._last_event_price = None
        self._last_event_ms = None
