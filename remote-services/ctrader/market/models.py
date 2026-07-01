"""Normalized market data models."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class SpotTick:
    symbol_id: int
    symbol_name: str
    bid: float
    ask: float
    spread: float
    timestamp_ms: int

    def to_json(self) -> dict[str, Any]:
        return {**asdict(self), "type": "spot_tick"}


@dataclass
class BarClose:
    symbol_id: int
    symbol_name: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    timestamp_ms: int

    def to_json(self) -> dict[str, Any]:
        return {**asdict(self), "type": "bar_close"}


@dataclass
class DepthUpdate:
    symbol_id: int
    symbol_name: str
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]
    timestamp_ms: int

    def to_json(self) -> dict[str, Any]:
        return {**asdict(self), "type": "depth_update"}


@dataclass
class ContextSnapshot:
    symbol_name: str
    price: dict[str, Any]
    context: dict[str, Any]
    quality: dict[str, Any]
    timestamp_ms: int

    def to_json(self) -> dict[str, Any]:
        return {**asdict(self), "type": "context_snapshot"}
