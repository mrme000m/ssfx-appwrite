"""Price Series — numpy ring buffer and series registry."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class OHLCVSeries:
    """Fixed-capacity ring buffer for OHLCV bars, backed by numpy arrays.

    Provides O(1) append and O(n) slice access for indicator computation.
    """

    capacity: int = 500

    def __post_init__(self) -> None:
        self.open = np.full(self.capacity, np.nan)
        self.high = np.full(self.capacity, np.nan)
        self.low = np.full(self.capacity, np.nan)
        self.close = np.full(self.capacity, np.nan)
        self.volume = np.zeros(self.capacity, dtype=np.float64)
        self.timestamps = np.zeros(self.capacity, dtype=np.int64)
        self._head = 0
        self.count = 0

    def push(
        self,
        o: float,
        h: float,
        low: float,
        c: float,
        v: float = 0.0,
        ts: int = 0,
    ) -> None:
        """Append a new bar to the ring buffer."""
        i = self._head % self.capacity
        self.open[i] = o
        self.high[i] = h
        self.low[i] = low
        self.close[i] = c
        self.volume[i] = v
        self.timestamps[i] = ts
        self._head += 1
        self.count = min(self.count + 1, self.capacity)

    def get_close(self, n: int) -> np.ndarray:
        """Return the last n close prices in chronological order."""
        n = min(n, self.count)
        if n == 0:
            return np.array([], dtype=np.float64)
        end = self._head
        indices = [(end - n + i) % self.capacity for i in range(n)]
        return self.close[indices].copy()

    def get_ohlc(self, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Return the last n OHLC arrays in chronological order."""
        n = min(n, self.count)
        if n == 0:
            empty = np.array([], dtype=np.float64)
            return empty, empty, empty, empty
        end = self._head
        indices = [(end - n + i) % self.capacity for i in range(n)]
        return (
            self.open[indices].copy(),
            self.high[indices].copy(),
            self.low[indices].copy(),
            self.close[indices].copy(),
        )

    @property
    def last_close(self) -> float | None:
        """Return the most recent close price, or None if empty."""
        if self.count == 0:
            return None
        return float(self.close[(self._head - 1) % self.capacity])

    @property
    def last_bar(self) -> dict[str, float] | None:
        """Return the most recent bar as a dict, or None if empty."""
        if self.count == 0:
            return None
        i = (self._head - 1) % self.capacity
        return {
            "open": float(self.open[i]),
            "high": float(self.high[i]),
            "low": float(self.low[i]),
            "close": float(self.close[i]),
            "volume": float(self.volume[i]),
            "timestamp": int(self.timestamps[i]),
        }


class SeriesRegistry:
    """Registry of OHLCVSeries keyed by (symbol_id, period).

    Auto-creates series on first access and provides a convenient update
    method for feeding BarClose events.
    """

    def __init__(self, default_capacity: int = 500):
        self._series: dict[tuple[int, str], OHLCVSeries] = {}
        self._default_capacity = default_capacity

    def get(self, symbol_id: int, period: str) -> OHLCVSeries:
        """Get or create a series for the given symbol+period."""
        key = (symbol_id, period)
        if key not in self._series:
            self._series[key] = OHLCVSeries(capacity=self._default_capacity)
        return self._series[key]

    def update(self, symbol_id: int, period: str, bar: dict) -> OHLCVSeries:
        """Push a bar into the series, creating it if needed."""
        series = self.get(symbol_id, period)
        series.push(
            bar["open"],
            bar["high"],
            bar["low"],
            bar["close"],
            bar.get("volume", 0.0),
            bar.get("timestamp_ms", 0),
        )
        return series

    def keys(self) -> list[tuple[int, str]]:
        return list(self._series.keys())

    def __len__(self) -> int:
        return len(self._series)
