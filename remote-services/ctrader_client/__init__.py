"""ctrader-client — Asyncio-native Python client for cTrader Open API."""

__version__ = "0.2.0"

__all__ = [
    # Core
    "CTraderSession",
    "TransportType",
    "AsyncTcpTransport",
    "AsyncWsTransport",
    "CTraderProtocolClient",
    "TokenManager",
    "TokenData",
    "BrokerTokenManager",
    "AppwriteTokenManager",
    "AppwriteMultiTokenClient",
    # Broker
    "sync_accounts_to_broker",
    # Market data
    "MarketDataManager",
    "SpotTick",
    "BarClose",
    "DepthUpdate",
    "ExecutionEvent",
    "OHLCVSeries",
    "SeriesRegistry",
    # Trading
    "AsyncEventBus",
    "Signal",
    "SMA",
    "EMA",
    "RSI",
    "ATR",
    "Indicator",
    "Condition",
    "IndicatorCondition",
    "CrossCondition",
    "AndCondition",
    "OrCondition",
    "NotCondition",
    "MTFCondition",
    "ExecutionManager",
    "OrderRequest",
    "TokenBucket",
    "RiskManager",
    "RiskConfig",
    "RiskViolation",
    "BaseStrategy",
]

# Core
from .appwrite_auth import AppwriteMultiTokenClient, AppwriteTokenManager
from .auth import TokenData, TokenManager
from .broker_auth import BrokerTokenManager, sync_accounts_to_broker

# Trading
from .event_bus import AsyncEventBus, Signal
from .execution import ExecutionManager, OrderRequest, TokenBucket
from .indicators import (
    ATR,
    EMA,
    RSI,
    SMA,
    AndCondition,
    Condition,
    CrossCondition,
    Indicator,
    IndicatorCondition,
    MTFCondition,
    NotCondition,
    OrCondition,
)

# Market data
from .market_data import BarClose, DepthUpdate, ExecutionEvent, MarketDataManager, SpotTick
from .protocol import CTraderProtocolClient
from .risk import RiskConfig, RiskManager, RiskViolation
from .series import OHLCVSeries, SeriesRegistry
from .session import CTraderSession, TransportType
from .strategy import BaseStrategy
from .transport import AsyncTcpTransport, AsyncWsTransport
