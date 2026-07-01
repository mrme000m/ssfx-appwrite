"""Pydantic models for OpenPI API request/response schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from .models import FeedSource, SymbolConfig, SymbolInfo, SymbolStatus, TimeFrame

# ── Service Configuration ─────────────────────────────────────────────────────

class ServiceConfig(BaseModel):
    """Global service configuration persisted to the database (SQLite or MongoDB)."""

    id: str = Field(default="service_config", description="Config document ID")
    default_symbols: list[int] = Field(
        default_factory=list,
        description="Default symbol IDs to auto-subscribe on startup",
    )
    default_bar_timeframes: list[str] = Field(
        default_factory=lambda: [TimeFrame.H1.value, TimeFrame.D1.value],
        description="Default bar timeframes to collect",
    )
    collect_ticks: bool = Field(
        default=True,
        description="Whether to collect tick data by default",
    )
    collect_bars: bool = Field(
        default=True,
        description="Whether to collect bar data by default",
    )
    collect_depth: bool = Field(
        default=False,
        description="Whether to collect order book depth by default",
    )
    tick_ttl_seconds: int = Field(
        default=604800,
        ge=0,
        description="TTL for tick data in seconds (0 = no expiry)",
    )
    bar_ttl_seconds: int = Field(
        default=2592000,
        ge=0,
        description="TTL for bar data in seconds (0 = no expiry)",
    )
    max_backfill_range_days: int = Field(
        default=30,
        ge=1,
        description="Maximum lookback range for backfill requests in days",
    )
    auto_reconnect: bool = Field(
        default=True,
        description="Auto-reconnect to feed on disconnection",
    )
    reconnect_delay_seconds: float = Field(
        default=5.0,
        ge=1.0,
        description="Initial reconnect delay in seconds",
    )
    max_reconnect_delay_seconds: float = Field(
        default=60.0,
        ge=1.0,
        description="Maximum reconnect delay in seconds",
    )
    stale_threshold_seconds: float = Field(
        default=30.0,
        ge=1.0,
        description="Threshold for marking feed as stale",
    )
    gap_fill_enabled: bool = Field(
        default=True,
        description="Enable automatic gap detection and backfill",
    )
    analytics_enabled: bool = Field(
        default=True,
        description="Enable periodic indicator/signal computation",
    )
    analytics_interval_seconds: int = Field(
        default=60,
        ge=10,
        description="Interval between analytics cycles in seconds",
    )
    quality_check_interval_seconds: int = Field(
        default=30,
        ge=10,
        description="Interval between quality checks in seconds",
    )
    batch_flush_interval_seconds: float = Field(
        default=5.0,
        ge=0.5,
        description="Interval between database batch flushes in seconds",
    )
    tick_buffer_size: int = Field(
        default=10_000,
        ge=100,
        description="Maximum tick queue buffer size",
    )
    bar_buffer_size: int = Field(
        default=5_000,
        ge=100,
        description="Maximum bar queue buffer size",
    )
    depth_buffer_size: int = Field(
        default=1_000,
        ge=100,
        description="Maximum order book queue buffer size",
    )
    log_level: str = Field(
        default="INFO",
        description="Logging level",
    )
    feed_source: FeedSource = Field(
        default=FeedSource.CTRADER,
        description="Primary market data feed source",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Last configuration update timestamp",
    )
    updated_by: str = Field(
        default="api",
        description="Source of last update (api, mcp, cli)",
    )

    model_config = {"populate_by_name": True}


# ── Symbol Management ─────────────────────────────────────────────────────────

class SymbolCreate(BaseModel):
    """Request to create/register a new symbol."""

    symbol_id: int = Field(..., description="Unique symbol ID")
    name: str = Field(..., min_length=1, max_length=50, description="Symbol name")
    digits: int = Field(default=5, ge=0, le=10, description="Decimal places")
    status: SymbolStatus = Field(default=SymbolStatus.ACTIVE, description="Symbol status")
    description: str | None = Field(default=None, description="Human-readable description")
    asset_class: str = Field(default="forex", description="Asset class (forex, crypto, etc.)")
    lot_size: int | None = Field(default=None, description="Standard lot size")
    exchange: str | None = Field(default=None, description="Exchange name")
    # cTrader-specific metadata
    pip_position: int | None = Field(default=None, ge=0, le=10, description="Pip position (may differ from digits)")
    tick_size: float | None = Field(default=None, ge=0, description="Minimum price increment")
    min_volume: int | None = Field(default=None, ge=0, description="Minimum volume in base units")
    max_volume: int | None = Field(default=None, ge=0, description="Maximum volume in base units")
    volume_step: int | None = Field(default=None, ge=0, description="Volume increment in base units")
    measurement_units: str | None = Field(default=None, description="Unit of measurement")


class SymbolUpdate(BaseModel):
    """Request to update symbol metadata."""

    name: str | None = Field(default=None, min_length=1, max_length=50)
    digits: int | None = Field(default=None, ge=0, le=10)
    status: SymbolStatus | None = Field(default=None)
    description: str | None = Field(default=None)
    asset_class: str | None = Field(default=None)
    lot_size: int | None = Field(default=None)
    exchange: str | None = Field(default=None)
    pip_position: int | None = Field(default=None, ge=0, le=10)
    tick_size: float | None = Field(default=None, ge=0)
    min_volume: int | None = Field(default=None, ge=0)
    max_volume: int | None = Field(default=None, ge=0)
    volume_step: int | None = Field(default=None, ge=0)
    measurement_units: str | None = Field(default=None)


class SymbolConfigUpdate(BaseModel):
    """Request to update symbol-specific data collection config."""

    enabled: bool = Field(..., description="Enable/disable data collection for this symbol")
    collect_ticks: bool = Field(default=True, description="Collect tick data")
    collect_bars: bool = Field(default=True, description="Collect bar data")
    collect_depth: bool = Field(default=False, description="Collect order book depth")
    bar_timeframes: list[str] = Field(
        default_factory=lambda: [TimeFrame.H1.value, TimeFrame.D1.value],
        description="Bar timeframes to collect",
    )
    feed_sources: list[FeedSource] = Field(
        default_factory=list,
        description="Preferred feed sources for this symbol",
    )


# ── Database Operations ────────────────────────────────────────────────────────

class DatabaseResetRequest(BaseModel):
    """Request to reset/clear database collections."""

    collections: list[str] = Field(
        default_factory=lambda: ["ticks", "bars", "orderbook", "indicators", "signals", "market_structure", "data_quality", "backfill_requests"],
        description="Collections to clear (empty = all data collections)",
    )
    confirm: bool = Field(..., description="Must be true to execute reset")
    preserve_symbols: bool = Field(
        default=True,
        description="Preserve symbol registry during reset",
    )
    preserve_configs: bool = Field(
        default=True,
        description="Preserve symbol configs during reset",
    )


class GapRepairRequest(BaseModel):
    """Request to detect and repair data gaps."""

    symbol_ids: list[int] = Field(
        default_factory=list,
        description="Symbol IDs to repair (empty = all active symbols)",
    )
    timeframes: list[str] = Field(
        default_factory=lambda: [tf.value for tf in TimeFrame],
        description="Timeframes to check and repair",
    )
    from_ms: int | None = Field(
        default=None,
        description="Start timestamp in milliseconds (default: 7 days ago)",
    )
    to_ms: int | None = Field(
        default=None,
        description="End timestamp in milliseconds (default: now)",
    )
    dry_run: bool = Field(
        default=False,
        description="Only report gaps without repairing",
    )


# ── Feed Control ──────────────────────────────────────────────────────────────

class FeedConnectRequest(BaseModel):
    """Request to connect to market data feed."""

    symbols: list[int] | None = Field(
        default=None,
        description="Specific symbol IDs to subscribe (default: use service config defaults)",
    )
    reconnect: bool = Field(
        default=False,
        description="Force reconnection even if already connected",
    )


class FeedSubscribeRequest(BaseModel):
    """Request to subscribe/unsubscribe from symbols."""

    symbols: list[int] = Field(..., description="Symbol IDs to subscribe/unsubscribe")
    subscribe_ticks: bool = Field(default=True, description="Subscribe to ticks")
    subscribe_bars: bool = Field(default=True, description="Subscribe to bars")
    bar_timeframes: list[str] = Field(
        default_factory=lambda: [TimeFrame.M1.value, TimeFrame.H1.value, TimeFrame.D1.value],
        description="Bar timeframes to subscribe",
    )
    subscribe_depth: bool = Field(default=False, description="Subscribe to order book depth")


# ── Response Models ────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    """Service health status."""

    status: str = Field(..., description="Overall health status")
    db_connected: bool = Field(..., description="MongoDB connection status")
    feed_connected: bool = Field(..., description="Market data feed connection status")
    uptime_seconds: float = Field(..., description="Service uptime in seconds")
    active_symbols: int = Field(..., description="Number of active symbols")
    subscribed_spots: int = Field(..., description="Number of subscribed spot feeds")
    subscribed_bars: int = Field(..., description="Number of subscribed bar feeds")
    subscribed_depth: int = Field(..., description="Number of subscribed depth feeds")
    feed_source: str = Field(..., description="Current feed source")
    version: str = Field(default="0.1.0", description="Service version")


class StatsResponse(BaseModel):
    """System and storage statistics."""

    active_symbols: int
    db_connected: bool
    uptime_seconds: float
    feed_connected: bool
    feed_source: str
    tick_count: int = 0
    bar_count: int = 0
    orderbook_count: int = 0
    signal_count: int = 0
    indicator_count: int = 0
    queued_backfills: int = 0
    version: str = "0.1.0"


class SymbolResponse(BaseModel):
    """Registered symbol with current config."""

    symbol: SymbolInfo
    config: SymbolConfig | None = None
    last_tick_ms: int | None = None
    last_bar_ms: dict[str, int] = Field(default_factory=dict)
    data_freshness_seconds: float | None = None


class GapReport(BaseModel):
    """Detected gap in time series data."""

    symbol_id: int
    symbol_name: str
    timeframe: str
    expected_from_ms: int
    expected_to_ms: int
    gap_duration_ms: int
    gap_count: int = 1


class GapRepairResponse(BaseModel):
    """Result of a gap repair operation."""

    symbol_id: int
    symbol_name: str
    timeframe: str
    gaps_detected: int
    bars_filled: int
    status: str = Field(..., description="completed, failed, skipped, no_gaps")
    message: str | None = None


class DatabaseResetResponse(BaseModel):
    """Result of a database reset operation."""

    collections_cleared: list[str]
    symbols_preserved: bool
    configs_preserved: bool
    documents_removed: int
    status: str = Field(..., description="success, partial, failed")
    message: str | None = None


class OperationResponse(BaseModel):
    """Generic operation result."""

    success: bool
    message: str
    data: dict[str, Any] | None = None


class CachedSymbolResponse(BaseModel):
    """A cached available symbol from a feed source."""

    symbol_id: int = Field(..., description="Unique symbol ID from the feed")
    name: str = Field(..., description="Symbol name")
    digits: int = Field(default=5, description="Decimal places")
    description: str | None = Field(default=None)
    asset_class: str | None = Field(default=None)
    lot_size: int | None = Field(default=None)
    exchange: str | None = Field(default=None)
    pip_position: int | None = Field(default=None)
    tick_size: float | None = Field(default=None)
    min_volume: int | None = Field(default=None)
    max_volume: int | None = Field(default=None)
    volume_step: int | None = Field(default=None)
    measurement_units: str | None = Field(default=None)
    source: str = Field(default="ctrader")
    enabled: bool = Field(default=True)
    cached_at: str | None = Field(default=None)


class CacheRefreshResponse(BaseModel):
    """Result of a cache refresh operation."""

    success: bool
    source: str
    cached_count: int
    message: str


# ── Authentication ───────────────────────────────────────────────────────────

class AuthStatusResponse(BaseModel):
    """Current authentication status for cTrader feed."""

    mode: str = Field(..., description="Auth mode: broker, direct, raw, none")
    has_credentials: bool = Field(..., description="Whether credentials are configured")
    client_id: str | None = None
    client_secret: str | None = None
    account_id: int | None = None
    grant_id: str | None = None
    selected_account_id: int | None = Field(default=None, description="ctidTraderAccountId reported by the broker")
    host_type: str = Field(default="demo", description="demo or live")
    broker_url: str | None = None
    has_grant_id: bool = Field(default=False)
    has_access_token: bool = Field(default=False)
    has_refresh_token: bool = Field(default=False)
    token_expires_at: float | None = None
    token_days_remaining: float | None = None
    connected: bool = Field(default=False, description="Feed connected")
    can_refresh: bool = Field(default=False, description="Token auto-refresh available")
    account_balance: float | None = Field(default=None, description="Current account balance from cTrader")
    account_balance_digits: int | None = Field(default=None, description="Money digits exponent for balance")


class AuthBrokerRequest(BaseModel):
    """Set broker-based auth credentials."""

    broker_url: str = Field(..., description="Cloudflare auth broker URL")
    grant_id: str = Field(..., description="Grant ID from broker OAuth flow")
    account_id: int | None = Field(default=None, description="Selected cTrader account ID")
    client_id: str | None = Field(default=None, description="cTrader app client_id (if not already in config)")
    client_secret: str | None = Field(default=None, description="cTrader app client_secret (if not already in config)")


class AuthDirectRequest(BaseModel):
    """Set direct OAuth credentials."""

    client_id: str
    client_secret: str
    access_token: str
    refresh_token: str
    account_id: int


class AuthBrokerStartUrlResponse(BaseModel):
    """URL to start broker OAuth flow in browser."""

    auth_url: str
    broker_url: str
    user_id: str
    instructions: str