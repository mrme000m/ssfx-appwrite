"""Pydantic models for market data entities."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

# Import event types from ctrader_client to avoid duplication
try:
    from ctrader_client.market_data import SpotTick as CTraderSpotTick, DepthUpdate as CTraderDepthUpdate
except ImportError:
    CTraderSpotTick = None
    CTraderDepthUpdate = None

# StrEnum is available in Python 3.11+
try:
    from enum import StrEnum
except ImportError:
    StrEnum = str


class TimeFrame(StrEnum):
    """Standard timeframe identifiers.

    Internal representation uses compact format (e.g., "1m", "1h").
    cTrader API uses ProtoOA format (e.g., "M1", "H1").
    """

    M1 = "1m"
    M3 = "3m"  # Added for completeness
    M5 = "5m"
    M10 = "10m"  # Added for completeness
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H2 = "2h"  # Added for completeness
    H3 = "3h"  # Added for completeness
    H4 = "4h"
    H6 = "6h"  # Added for completeness
    H8 = "8h"  # Added for completeness
    H12 = "12h"  # Added for completeness
    D1 = "1d"
    W1 = "1w"
    MN = "1M"  # Monthly

    @classmethod
    def from_ctrader_period(cls, period: str) -> "TimeFrame":
        """Convert cTrader ProtoOATrendbarPeriod name (e.g. 'M1', 'H1') to TimeFrame.

        Args:
            period: cTrader period string (e.g., "M1", "H1", "D1")

        Returns:
            TimeFrame enum value

        Raises:
            ValueError: If period is not recognized
        """
        _map = {
            "M1": cls.M1,
            "M3": cls.M3,
            "M5": cls.M5,
            "M10": cls.M10,
            "M15": cls.M15,
            "M30": cls.M30,
            "H1": cls.H1,
            "H2": cls.H2,
            "H3": cls.H3,
            "H4": cls.H4,
            "H6": cls.H6,
            "H8": cls.H8,
            "H12": cls.H12,
            "D1": cls.D1,
            "W1": cls.W1,
            "MN": cls.MN,
        }
        if period in _map:
            return _map[period]
        # Fallback: try direct value match (for already-converted values)
        try:
            return cls(period)
        except ValueError:
            raise ValueError(
                f"Unrecognized cTrader period: {period}. "
                f"Valid periods: {list(_map.keys())}"
            )

    def to_ctrader_period(self) -> str:
        """Convert TimeFrame to cTrader ProtoOATrendbarPeriod name (e.g. 'M1', 'H1').

        Returns:
            cTrader period string (e.g., "M1", "H1", "D1")
        """
        _map = {
            TimeFrame.M1: "M1",
            TimeFrame.M3: "M3",
            TimeFrame.M5: "M5",
            TimeFrame.M10: "M10",
            TimeFrame.M15: "M15",
            TimeFrame.M30: "M30",
            TimeFrame.H1: "H1",
            TimeFrame.H2: "H2",
            TimeFrame.H3: "H3",
            TimeFrame.H4: "H4",
            TimeFrame.H6: "H6",
            TimeFrame.H8: "H8",
            TimeFrame.H12: "H12",
            TimeFrame.D1: "D1",
            TimeFrame.W1: "W1",
            TimeFrame.MN: "MN",
        }
        result = _map.get(self)
        if result is None:
            logger = logging.getLogger(__name__)
            logger.warning(
                "TimeFrame %s has no cTrader mapping, returning value %s",
                self,
                self.value,
            )
        return result or self.value

    @property
    def minutes(self) -> int:
        """Get timeframe duration in minutes."""
        _minutes_map = {
            TimeFrame.M1: 1,
            TimeFrame.M3: 3,
            TimeFrame.M5: 5,
            TimeFrame.M10: 10,
            TimeFrame.M15: 15,
            TimeFrame.M30: 30,
            TimeFrame.H1: 60,
            TimeFrame.H2: 120,
            TimeFrame.H3: 180,
            TimeFrame.H4: 240,
            TimeFrame.H6: 360,
            TimeFrame.H8: 480,
            TimeFrame.H12: 720,
            TimeFrame.D1: 1440,
            TimeFrame.W1: 10080,
            TimeFrame.MN: 43200,  # Approximate (30 days)
        }
        return _minutes_map.get(self, 60)

    @property
    def milliseconds(self) -> int:
        """Get timeframe duration in milliseconds."""
        return self.minutes * 60 * 1000


class SymbolStatus(StrEnum):
    """Lifecycle status."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    DELISTED = "delisted"


class FeedSource(StrEnum):
    """Market data provider identifiers."""

    BINANCE = "binance"
    BYBIT = "bybit"
    COINBASE = "coinbase"
    KRAKEN = "kraken"
    BASES = "bases"
    CTRADER = "ctrader"
    MANUAL = "manual"
    BACKFILL = "backfill"
    UNKNOWN = "unknown"


class MarketRegime(StrEnum):
    """Detected market regime."""

    RANGING = "ranging"
    UPTREND = "uptrend"
    DOWNTREND = "downtrend"
    BREAKOUT = "breakout"


class Symbol(BaseModel):
    """Trading pair metadata."""

    symbol: str = Field(..., min_length=3, max_length=20)
    base: str = Field(..., min_length=1, max_length=10)
    quote: str = Field(..., min_length=1, max_length=10)
    status: SymbolStatus = SymbolStatus.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class OHLCVBar(BaseModel):
    """Candlestick data."""

    symbol_id: int
    symbol_name: str
    timeframe: TimeFrame
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp_ms: int
    source: FeedSource = FeedSource.UNKNOWN

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def body(self) -> float:
        return abs(self.close - self.open)


# Re-export ctrader_client types if available, else provide fallbacks
if CTraderSpotTick is not None:
    SpotTick = CTraderSpotTick
else:
    @dataclass(slots=True)
    class SpotTick:
        """Real-time tick (ctrader_client compatible)."""

        symbol_id: int
        symbol_name: str
        bid: float
        ask: float
        timestamp_ms: int


if CTraderDepthUpdate is not None:
    DepthUpdate = CTraderDepthUpdate
else:
    @dataclass(slots=True)
    class DepthUpdate:
        """Orderbook depth change (ctrader_client compatible)."""

        symbol_id: int
        new_quotes: list[Any]
        deleted_ids: list[int]


class TechnicalIndicator(BaseModel):
    """Computed indicator value."""

    symbol_id: int = Field(default=0)
    symbol_name: str = Field(default="")
    indicator: str
    value: float | dict[str, float]
    timeframe: TimeFrame
    period: int = Field(default=0)
    timestamp_ms: int = Field(default=0)


class TradingSignal(BaseModel):
    """Generated trading signal."""

    symbol_id: int = Field(default=0)
    symbol_name: str = Field(default="")
    direction: str
    strength: float
    indicators: list[str]
    confidence: float
    timestamp_ms: int = Field(default=0)
    timeframe: TimeFrame


class BarCloseEvent(BaseModel):
    """Bar close event with technical indicators and signals."""

    symbol: str
    bar: OHLCVBar
    indicators: list[TechnicalIndicator]
    signals: list[TradingSignal]
    structure: MarketRegime | None


class DataQualityReport(BaseModel):
    """Quality check summary."""

    symbol_id: int
    symbol_name: str
    total_records: int
    score: float
    issues: list[str] = []
    # Tick-specific fields
    last_tick_ms: int | None = None
    freshness_seconds: float | None = None
    # Bar-specific fields
    timeframe: TimeFrame | None = None
    gap_count: int = 0
    anomaly_count: int = 0
    last_bar_ms: int | None = None
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MarketStructureInfo(BaseModel):
    """Detected market regime with boundaries."""

    symbol_id: int
    symbol_name: str
    timeframe: TimeFrame
    swing_highs: list[dict[str, Any]]
    swing_lows: list[dict[str, Any]]
    support_levels: list[float]
    resistance_levels: list[float]
    trend: str
    volatility_regime: str
    timestamp_ms: int


class OrderBookSnapshot(BaseModel):
    """Order book snapshot."""

    symbol_id: int
    symbol_name: str
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    timestamp_ms: int
    digits: int = 5

    @property
    def spread(self) -> float:
        if not self.bids or not self.asks:
            return 0.0
        return round(self.asks[0].price - self.bids[0].price, self.digits)

    @property
    def bid_depth(self) -> float:
        return sum(level.volume for level in self.bids)

    @property
    def ask_depth(self) -> float:
        return sum(level.volume for level in self.asks)

    @property
    def depth_imbalance(self) -> float:
        bid_depth = self.bid_depth
        ask_depth = self.ask_depth
        if bid_depth + ask_depth == 0:
            return 0.0
        return round((bid_depth - ask_depth) / (bid_depth + ask_depth), 5)


class OrderBookLevel(BaseModel):
    """Single level in the order book."""

    price: float
    volume: float
    side: str
    level: int


class TickData(BaseModel):
    """Tick / trade data with validation.

    Represents a real-time price tick with bid/ask quotes.
    All fields are validated for correctness and proper formatting.
    """

    symbol_id: int = Field(..., gt=0, description="cTrader symbol identifier")
    symbol_name: str = Field(
        ..., min_length=1, max_length=20, description="Symbol name"
    )
    bid: float = Field(..., gt=0, description="Bid price")
    ask: float = Field(..., gt=0, description="Ask price")
    bid_volume: float = Field(default=0.0, ge=0, description="Bid volume")
    ask_volume: float = Field(default=0.0, ge=0, description="Ask volume")
    timestamp_ms: int = Field(..., gt=0, description="Timestamp in milliseconds")
    digits: int = Field(default=5, ge=0, le=6, description="Price decimal places")

    @property
    def spread(self) -> float:
        """Calculate spread (ask - bid)."""
        return round(self.ask - self.bid, self.digits)

    @property
    def spread_pct(self) -> float:
        """Calculate spread as percentage of mid price."""
        mid = (self.bid + self.ask) / 2
        if mid == 0:
            return 0.0
        return round((self.spread / mid) * 100, 6)

    @property
    def mid(self) -> float:
        """Calculate mid price."""
        return round((self.bid + self.ask) / 2, self.digits)

    @property
    def is_valid(self) -> bool:
        """Check if tick has valid bid/ask relationship."""
        return self.ask > self.bid and self.bid > 0

    @model_validator(mode="after")
    def validate_tick(self) -> "TickData":
        """Validate tick data after construction.

        Checks:
        - Ask must be greater than bid
        - Prices must be positive
        - Timestamp must be recent (within 24 hours)

        Returns:
            Self for method chaining

        Raises:
            ValueError: If tick validation fails
        """
        # Validate bid/ask relationship
        if self.ask <= self.bid:
            raise ValueError(
                f"Invalid tick: ask ({self.ask}) must be greater than "
                f"bid ({self.bid}) for {self.symbol_name}"
            )

        # Validate timestamp freshness (within 24 hours)
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        age_hours = (now_ms - self.timestamp_ms) / (1000 * 60 * 60)
        if age_hours > 24:
            logger = logging.getLogger(__name__)
            logger.warning(
                "Stale tick data for %s: age=%.1f hours",
                self.symbol_name,
                age_hours,
            )

        return self


class SymbolInfo(BaseModel):
    """Symbol metadata stored in registry.

    This model captures all relevant metadata for a trading instrument,
    including cTrader-specific fields required for proper price conversion
    and order execution.

    Attributes:
        symbol_id: Unique identifier from cTrader API
        name: Symbol name (e.g., "EURUSD", "BTCUSD")
        digits: Number of decimal places for price (default: 2, forex: 5)
        status: Current lifecycle status
        description: Human-readable description
        asset_class: Asset category (forex, crypto, indices, commodities)
        lot_size: Standard lot size in base currency units
        exchange: Exchange or venue identifier
        pip_position: Decimal position of 1 pip (often equals digits)
        tick_size: Minimum price movement
        min_volume: Minimum order volume in base currency units
        max_volume: Maximum order volume in base currency units
        volume_step: Volume increment in base currency units
        measurement_units: Units of measurement (e.g., "ounces" for XAU)
    """

    symbol_id: int = Field(..., description="Unique cTrader symbol identifier")
    name: str = Field(..., min_length=1, max_length=20, description="Symbol name")
    digits: int = Field(default=5, ge=0, le=6, description="Price decimal places")
    status: SymbolStatus = Field(default=SymbolStatus.ACTIVE)
    description: str | None = Field(default=None, max_length=256)
    asset_class: str = Field(default="forex", description="Asset category")
    lot_size: int | None = Field(default=None, gt=0, description="Standard lot size")
    exchange: str | None = Field(default=None, description="Exchange/venue")

    # cTrader-specific metadata for price/volume conversion
    pip_position: int | None = Field(
        default=None, ge=0, le=6, description="Decimal position of 1 pip"
    )
    tick_size: float | None = Field(
        default=None, gt=0, description="Minimum price movement"
    )
    min_volume: int | None = Field(
        default=None, ge=0, description="Minimum order volume"
    )
    max_volume: int | None = Field(
        default=None, ge=0, description="Maximum order volume"
    )
    volume_step: int | None = Field(default=None, ge=0, description="Volume increment")
    measurement_units: str | None = Field(default=None, description="Measurement units")

    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def pip_size(self) -> float:
        """Get pip size as a decimal value.

        Returns:
            Pip size (e.g., 0.00001 for 5-digit forex pairs)
        """
        return 10 ** -(self.pip_position or self.digits)

    @property
    def is_forex(self) -> bool:
        """Check if symbol is a forex pair."""
        return self.asset_class.lower() == "forex"

    @property
    def is_crypto(self) -> bool:
        """Check if symbol is a cryptocurrency."""
        return self.asset_class.lower() in ["crypto", "cryptocurrency"]

    @property
    def is_metal(self) -> bool:
        """Check if symbol is a precious metal."""
        return self.asset_class.lower() in ["metals", "precious_metals"]

    def validate_price(self, price: float) -> tuple[bool, str | None]:
        """Validate a price value for this symbol.

        Args:
            price: Price to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        if price <= 0:
            return False, "Price must be positive"

        # Check decimal places
        price_str = str(price)
        if "." in price_str:
            decimals = len(price_str.split(".")[1])
            if decimals > self.digits:
                return (
                    False,
                    f"Price has {decimals} decimals, expected <= {self.digits}",
                )

        return True, None

    def validate_volume(self, volume: float) -> tuple[bool, str | None]:
        """Validate an order volume for this symbol.

        Args:
            volume: Volume to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        if volume <= 0:
            return False, "Volume must be positive"

        if self.min_volume is not None and volume < self.min_volume:
            return False, f"Volume {volume} below minimum {self.min_volume}"

        if self.max_volume is not None and volume > self.max_volume:
            return False, f"Volume {volume} above maximum {self.max_volume}"

        if self.volume_step is not None:
            remainder = volume % self.volume_step
            if remainder != 0:
                return False, f"Volume not a multiple of step {self.volume_step}"

        return True, None

    def model_dump(self, *args, **kwargs) -> dict:
        """Override to ensure proper datetime serialization."""
        result = super().model_dump(*args, **kwargs)
        # Ensure datetime fields are ISO format strings
        if "updated_at" in result and isinstance(result["updated_at"], datetime):
            result["updated_at"] = result["updated_at"].isoformat()
        return result


class SymbolConfig(BaseModel):
    """Per-symbol configuration."""

    symbol_id: int
    name: str
    enabled: bool = True
    feed_sources: list[FeedSource] = []
    collect_ticks: bool = True
    collect_bars: bool = True
    collect_depth: bool = False
    bar_timeframes: list[TimeFrame] = []
