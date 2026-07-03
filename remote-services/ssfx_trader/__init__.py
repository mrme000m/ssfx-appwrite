"""SSFX trade execution — per-account slave and trade lifecycle management."""
from __future__ import annotations

from .backends.base import ExecutionBackend
from .backends.simulated import SimulatedBackend
from .config import AccountConfig, CTraderConfig, PerAccountTradingConfig
from .executor import TradeExecutor
from .slave import AccountSlave
from .stores.base import AccountStore, SignalStore

__all__ = [
    "AccountConfig",
    "AccountSlave",
    "AccountStore",
    "CTraderConfig",
    "ExecutionBackend",
    "PerAccountTradingConfig",
    "SignalStore",
    "SimulatedBackend",
    "TradeExecutor",
]
