"""Indicators and Signal Condition Algebra.

Each indicator is a pure function over an OHLCVSeries. Conditions are
composable objects that can be AND/OR-combined at runtime.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np

from .series import OHLCVSeries

# ── Base Indicator Protocol ────────────────────────────────────────────────


class Indicator(ABC):
    """Abstract indicator that computes a value from a price series."""

    def __init__(self, period: int):
        self.period = period

    @abstractmethod
    def compute(self, series: OHLCVSeries) -> float | None:
        """Return the current indicator value, or None if insufficient data."""


# ── Built-in Indicators (NumPy-accelerated) ────────────────────────────────


class SMA(Indicator):
    """Simple Moving Average."""

    def compute(self, series: OHLCVSeries) -> float | None:
        if series.count < self.period:
            return None
        return float(np.mean(series.get_close(self.period)))


class EMA(Indicator):
    """Exponential Moving Average."""

    def compute(self, series: OHLCVSeries) -> float | None:
        if series.count < self.period:
            return None
        closes = series.get_close(self.period)
        k = 2.0 / (self.period + 1)
        ema = closes[0]
        for c in closes[1:]:
            ema = c * k + ema * (1 - k)
        return float(ema)


class RSI(Indicator):
    """Relative Strength Index."""

    def compute(self, series: OHLCVSeries) -> float | None:
        n = self.period + 1
        if series.count < n:
            return None
        closes = series.get_close(n)
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = np.mean(gains[-self.period :])
        avg_loss = np.mean(losses[-self.period :])
        if avg_loss == 0:
            return 100.0
        return float(100.0 - 100.0 / (1.0 + avg_gain / avg_loss))


class ATR(Indicator):
    """Average True Range."""

    def compute(self, series: OHLCVSeries) -> float | None:
        if series.count < self.period + 1:
            return None
        closes = series.close[: series.count]
        highs = series.high[: series.count]
        lows = series.low[: series.count]
        tr = np.maximum(
            highs[-self.period :] - lows[-self.period :],
            np.maximum(
                np.abs(highs[-self.period :] - closes[-self.period - 1 : -1]),
                np.abs(lows[-self.period :] - closes[-self.period - 1 : -1]),
            ),
        )
        return float(np.mean(tr))


# ── Signal / Condition Algebra ─────────────────────────────────────────────


class Condition(ABC):
    """A boolean condition evaluated against a price series."""

    @abstractmethod
    def evaluate(self, series: OHLCVSeries) -> bool: ...

    def __and__(self, other: "Condition") -> "AndCondition":
        return AndCondition(self, other)

    def __or__(self, other: "Condition") -> "OrCondition":
        return OrCondition(self, other)

    def __invert__(self) -> "NotCondition":
        return NotCondition(self)


class IndicatorCondition(Condition):
    """Wraps an indicator with a threshold comparison.

    Supported ops: ">", "<", ">=", "<=", "==", "cross_above", "cross_below".
    """

    def __init__(
        self,
        indicator: Indicator,
        op: str,
        threshold: float,
        name: str | None = None,
    ):
        self._indicator = indicator
        self._op = op
        self._threshold = threshold
        self._last_value: float | None = None
        self._name = name or f"{indicator.__class__.__name__}{self._op}{threshold}"

    def evaluate(self, series: OHLCVSeries) -> bool:
        val = self._indicator.compute(series)
        if val is None:
            return False
        result = self._compare(val, self._last_value)
        self._last_value = val
        return result

    def _compare(self, val: float, last: float | None) -> bool:
        if self._op == ">":
            return val > self._threshold
        if self._op == "<":
            return val < self._threshold
        if self._op == ">=":
            return val >= self._threshold
        if self._op == "<=":
            return val <= self._threshold
        if self._op == "==":
            return abs(val - self._threshold) < 1e-9
        if self._op == "cross_above":
            return last is not None and last <= self._threshold and val > self._threshold
        if self._op == "cross_below":
            return last is not None and last >= self._threshold and val < self._threshold
        return False

    @property
    def name(self) -> str:
        return self._name

    @property
    def last_value(self) -> float | None:
        return self._last_value


class CrossCondition(Condition):
    """True when fast indicator crosses above/below slow indicator."""

    def __init__(
        self,
        fast: Indicator,
        slow: Indicator,
        direction: str = "above",
        name: str | None = None,
    ):
        self._fast = fast
        self._slow = slow
        self._direction = direction
        self._prev_diff: float | None = None
        self._name = name or f"Cross({fast.__class__.__name__}/{slow.__class__.__name__},{direction})"

    def evaluate(self, series: OHLCVSeries) -> bool:
        f = self._fast.compute(series)
        s = self._slow.compute(series)
        if f is None or s is None:
            return False
        diff = f - s
        result = False
        if self._prev_diff is not None:
            if self._direction == "above":
                result = self._prev_diff <= 0 < diff
            else:
                result = self._prev_diff >= 0 > diff
        self._prev_diff = diff
        return result

    @property
    def name(self) -> str:
        return self._name


class AndCondition(Condition):
    """All sub-conditions must be True."""

    def __init__(self, *conditions: Condition):
        self._conditions = conditions

    def evaluate(self, series: OHLCVSeries) -> bool:
        return all(c.evaluate(series) for c in self._conditions)

    @property
    def name(self) -> str:
        return " AND ".join(c.name for c in self._conditions if hasattr(c, "name"))


class OrCondition(Condition):
    """Any sub-condition must be True."""

    def __init__(self, *conditions: Condition):
        self._conditions = conditions

    def evaluate(self, series: OHLCVSeries) -> bool:
        return any(c.evaluate(series) for c in self._conditions)

    @property
    def name(self) -> str:
        return " OR ".join(c.name for c in self._conditions if hasattr(c, "name"))


class NotCondition(Condition):
    """Negation of a condition."""

    def __init__(self, condition: Condition):
        self._condition = condition

    def evaluate(self, series: OHLCVSeries) -> bool:
        return not self._condition.evaluate(series)

    @property
    def name(self) -> str:
        inner = self._condition.name if hasattr(self._condition, "name") else "?"
        return f"NOT({inner})"


class MTFCondition(Condition):
    """Requires a condition to hold on TWO different timeframes simultaneously."""

    def __init__(
        self,
        condition: Condition,
        series_registry: Any,  # SeriesRegistry
        symbol_id: int,
        tf1: str,
        tf2: str,
        name: str | None = None,
    ):
        self._cond = condition
        self._registry = series_registry
        self._symbol_id = symbol_id
        self._tf1 = tf1
        self._tf2 = tf2
        self._name = name or f"MTF({condition.name},{tf1}/{tf2})"

    def evaluate(self, _: OHLCVSeries) -> bool:
        s1 = self._registry.get(self._symbol_id, self._tf1)
        s2 = self._registry.get(self._symbol_id, self._tf2)
        if s1.count == 0 or s2.count == 0:
            return False
        return self._cond.evaluate(s1) and self._cond.evaluate(s2)

    @property
    def name(self) -> str:
        return self._name
