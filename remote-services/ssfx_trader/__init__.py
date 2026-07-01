"""SSFX trade execution — per-account follower and trade lifecycle management."""
from __future__ import annotations

from .backends.base import ExecutionBackend
from .backends.simulated import SimulatedBackend
from .config import AccountConfig, CTraderConfig, PerAccountTradingConfig
from .executor import TradeExecutor
from .follower import AccountFollower
from .stores.base import AccountStore, SignalStore
from .stores.mongo_store import MongoAccountStore, MongoSignalStore

__all__ = [
    "AccountConfig",
    "AccountFollower",
    "AccountStore",
    "CTraderConfig",
    "ExecutionBackend",
    "MongoAccountStore",
    "MongoSignalStore",
    "PerAccountTradingConfig",
    "SignalStore",
    "SimulatedBackend",
    "TradeExecutor",
]
