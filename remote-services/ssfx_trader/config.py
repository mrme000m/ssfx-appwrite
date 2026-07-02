"""Per-account and trading configuration dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ssfx_parser import (
    EntryUpdateAction,
    ExecutionMode,
    OrderHandling,
    SecondUpdateAction,
    SlStrategy,
    TpStrategy,
    VolumeMode,
)

# Deployed Cloudflare auth broker for cTrader OAuth delegation.
DEFAULT_CTRADER_BROKER_URL = "https://auth-ctrader.mrme0.store"


@dataclass(slots=True)
class CTraderConfig:
    broker_url: str = DEFAULT_CTRADER_BROKER_URL
    grant_id: str = ""
    client_id: str = ""
    client_secret: str = ""
    account_id: int = 0
    host_type: str = "demo"  # "live" or "demo"


@dataclass(slots=True)
class PartialCloseConfig:
    on_tp1_pct: float = 50.0
    on_tp2_pct: float = 25.0
    on_tp3_pct: float = 100.0
    on_close_half_pct: float = 50.0
    on_second_update_pct: float = 100.0

    def get_for_tp(self, tp_num: int) -> float:
        return {1: self.on_tp1_pct, 2: self.on_tp2_pct, 3: self.on_tp3_pct}.get(tp_num, 100.0)


@dataclass(slots=True)
class UpdateActionConfig:
    close_half_override_pct: float | None = None
    close_partial_override_pct: float | None = None
    second_update_action: SecondUpdateAction = SecondUpdateAction.FULL_CLOSE
    entry_update_action: EntryUpdateAction = EntryUpdateAction.IGNORE


@dataclass(slots=True)
class SymbolOverride:
    symbol: str
    enabled: bool = True
    volume_mode: VolumeMode | None = None
    volume_value: float | None = None
    max_positions: int | None = None
    tp_strategy: TpStrategy | None = None
    sl_strategy: SlStrategy | None = None
    partial_close: PartialCloseConfig | None = None


@dataclass(slots=True)
class PerAccountTradingConfig:
    enabled: bool = True
    execution_mode: ExecutionMode | str = ExecutionMode.DEMO
    min_parse_confidence: float = 0.75
    max_positions: int = 3
    max_positions_per_symbol: int = 5

    default_volume: float = 0.01
    volume_mode: VolumeMode | str = VolumeMode.FIXED_LOTS
    volume_value: float = 0.01
    max_volume_lots: float | None = None
    min_volume_lots: float | None = None

    order_handling: OrderHandling | str = OrderHandling.FOLLOW_SIGNAL
    tp_strategy: TpStrategy | str = TpStrategy.TP1_ONLY
    sl_strategy: SlStrategy | str = SlStrategy.FOLLOW_SIGNAL
    max_sl_distance_pips: float | None = None
    max_risk_per_trade_pct: float | None = None
    max_daily_risk_pct: float | None = None
    max_spread_pips: float | None = None
    market_context_mode: str = "warn"  # strict | warn | ignore

    # Kill-switches (per-account safety guardrails)
    max_daily_loss_pct: float | None = None  # e.g. 2.0 = 2% of daily starting equity
    max_drawdown_pct: float | None = None    # e.g. 5.0 = 5% peak-to-trough drawdown
    max_open_risk_pct: float | None = None   # e.g. 2.0 = max % of equity at risk across open positions
    panic_stop: bool = False                 # manual kill-switch; overrides everything
    risk_reset_utc_hour: int = 0             # hour of day to reset daily counters

    partial_close: PartialCloseConfig = field(default_factory=PartialCloseConfig)
    update_actions: UpdateActionConfig = field(default_factory=UpdateActionConfig)
    symbol_overrides: list[SymbolOverride] = field(default_factory=list)

    allow_same_symbol_add: bool = False
    allow_opposite_direction: bool = False
    update_aggregation_ms: int | None = None
    position_timeout_minutes: float | None = None
    close_stale_positions: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.execution_mode, str):
            self.execution_mode = ExecutionMode(self.execution_mode)
        if isinstance(self.volume_mode, str):
            self.volume_mode = VolumeMode(self.volume_mode)
        if isinstance(self.order_handling, str):
            self.order_handling = OrderHandling(self.order_handling)
        if isinstance(self.tp_strategy, str):
            self.tp_strategy = TpStrategy(self.tp_strategy)
        if isinstance(self.sl_strategy, str):
            self.sl_strategy = SlStrategy(self.sl_strategy)
        if isinstance(self.update_actions, dict):
            self.update_actions = UpdateActionConfig(**self.update_actions)
        if isinstance(self.partial_close, dict):
            self.partial_close = PartialCloseConfig(**self.partial_close)
        if self.symbol_overrides and isinstance(self.symbol_overrides[0], dict):
            self.symbol_overrides = [SymbolOverride(**ov) for ov in self.symbol_overrides]

    @property
    def use_live(self) -> bool:
        return self.execution_mode == ExecutionMode.LIVE

    def get_symbol_override(self, symbol: str | None) -> SymbolOverride | None:
        if not symbol:
            return None
        target = symbol.upper()
        for ov in self.symbol_overrides:
            if ov.symbol.upper() == target:
                return ov
        return None


@dataclass(slots=True)
class AccountConfig:
    """Per-account (formerly per-follower) configuration."""

    name: str
    enabled: bool
    ctrader: CTraderConfig
    trading: PerAccountTradingConfig
    owner_id: str = "system"
    symbols_filter: list[str] = field(default_factory=list)

    @property
    def use_live(self) -> bool:
        return (
            self.trading.execution_mode == ExecutionMode.LIVE
            or self.ctrader.host_type == "live"
        )

    @property
    def has_grant_id(self) -> bool:
        return bool(self.ctrader.grant_id and self.ctrader.grant_id != "GRANT_ID_HERE")

    def allows_symbol(self, symbol: str | None) -> bool:
        if not symbol:
            return False
        if not self.symbols_filter:
            return True
        return symbol.upper() in [s.upper() for s in self.symbols_filter]

    def to_mongo(self) -> dict[str, Any]:
        return {
            "_id": self.name,
            "name": self.name,
            "enabled": self.enabled,
            "owner_id": self.owner_id,
            "ctrader": {
                "broker_url": self.ctrader.broker_url,
                "grant_id": self.ctrader.grant_id,
                "client_id": self.ctrader.client_id,
                "client_secret": self.ctrader.client_secret,
                "account_id": self.ctrader.account_id,
                "host_type": self.ctrader.host_type,
            },
            "trading": {
                "enabled": self.trading.enabled,
                "execution_mode": self.trading.execution_mode.value,
                "min_parse_confidence": self.trading.min_parse_confidence,
                "max_positions": self.trading.max_positions,
                "max_positions_per_symbol": self.trading.max_positions_per_symbol,
                "default_volume": self.trading.default_volume,
                "volume_mode": self.trading.volume_mode.value,
                "volume_value": self.trading.volume_value,
                "max_volume_lots": self.trading.max_volume_lots,
                "min_volume_lots": self.trading.min_volume_lots,
                "order_handling": self.trading.order_handling.value,
                "tp_strategy": self.trading.tp_strategy.value,
                "sl_strategy": self.trading.sl_strategy.value,
                "max_sl_distance_pips": self.trading.max_sl_distance_pips,
                "max_risk_per_trade_pct": self.trading.max_risk_per_trade_pct,
                "max_daily_risk_pct": self.trading.max_daily_risk_pct,
                "max_spread_pips": self.trading.max_spread_pips,
                "market_context_mode": self.trading.market_context_mode,
                # Kill-switches
                "max_daily_loss_pct": self.trading.max_daily_loss_pct,
                "max_drawdown_pct": self.trading.max_drawdown_pct,
                "max_open_risk_pct": self.trading.max_open_risk_pct,
                "panic_stop": self.trading.panic_stop,
                "risk_reset_utc_hour": self.trading.risk_reset_utc_hour,
                "partial_close": {
                    "on_tp1_pct": self.trading.partial_close.on_tp1_pct,
                    "on_tp2_pct": self.trading.partial_close.on_tp2_pct,
                    "on_tp3_pct": self.trading.partial_close.on_tp3_pct,
                    "on_close_half_pct": self.trading.partial_close.on_close_half_pct,
                    "on_second_update_pct": self.trading.partial_close.on_second_update_pct,
                },
                "update_actions": {
                    "close_half_override_pct": self.trading.update_actions.close_half_override_pct,
                    "close_partial_override_pct": self.trading.update_actions.close_partial_override_pct,
                    "second_update_action": self.trading.update_actions.second_update_action.value,
                    "entry_update_action": self.trading.update_actions.entry_update_action.value,
                },
                "symbol_overrides": [
                    {
                        "symbol": ov.symbol,
                        "enabled": ov.enabled,
                        "volume_mode": ov.volume_mode.value if ov.volume_mode else None,
                        "volume_value": ov.volume_value,
                        "max_positions": ov.max_positions,
                        "tp_strategy": ov.tp_strategy.value if ov.tp_strategy else None,
                        "sl_strategy": ov.sl_strategy.value if ov.sl_strategy else None,
                    }
                    for ov in self.trading.symbol_overrides
                ],
                "allow_same_symbol_add": self.trading.allow_same_symbol_add,
                "allow_opposite_direction": self.trading.allow_opposite_direction,
                "update_aggregation_ms": self.trading.update_aggregation_ms,
                "position_timeout_minutes": self.trading.position_timeout_minutes,
                "close_stale_positions": self.trading.close_stale_positions,
            },
            "symbols_filter": self.symbols_filter,
        }

    @classmethod
    def from_mongo(cls, doc: dict[str, Any]) -> AccountConfig:
        doc = dict(doc)
        doc.pop("_id", None)
        ct = doc.get("ctrader", {})
        tr = doc.get("trading", {})
        return AccountConfig(
            name=doc.get("name", ""),
            enabled=doc.get("enabled", True),
            owner_id=doc.get("owner_id", "system"),
            ctrader=CTraderConfig(
                broker_url=ct.get("broker_url", ""),
                grant_id=ct.get("grant_id", ""),
                client_id=ct.get("client_id", ""),
                client_secret=ct.get("client_secret", ""),
                account_id=int(ct.get("account_id", 0)),
                host_type=ct.get("host_type", "demo"),
            ),
            trading=PerAccountTradingConfig(
                enabled=tr.get("enabled", True),
                execution_mode=tr.get("execution_mode", "demo"),
                min_parse_confidence=float(tr.get("min_parse_confidence", 0.75)),
                max_positions=int(tr.get("max_positions", 3)),
                max_positions_per_symbol=int(tr.get("max_positions_per_symbol", 5)),
                default_volume=float(tr.get("default_volume", 0.01)),
                volume_mode=tr.get("volume_mode", "fixed_lots"),
                volume_value=float(tr.get("volume_value", 0.01)),
                max_volume_lots=float(tr.get("max_volume_lots")) if tr.get("max_volume_lots") is not None else None,
                min_volume_lots=float(tr.get("min_volume_lots")) if tr.get("min_volume_lots") is not None else None,
                order_handling=tr.get("order_handling", "follow_signal"),
                tp_strategy=tr.get("tp_strategy", "tp1_only"),
                sl_strategy=tr.get("sl_strategy", "follow_signal"),
                max_sl_distance_pips=float(tr.get("max_sl_distance_pips")) if tr.get("max_sl_distance_pips") is not None else None,
                max_risk_per_trade_pct=float(tr.get("max_risk_per_trade_pct")) if tr.get("max_risk_per_trade_pct") is not None else None,
                max_daily_risk_pct=float(tr.get("max_daily_risk_pct")) if tr.get("max_daily_risk_pct") is not None else None,
                max_spread_pips=float(tr.get("max_spread_pips")) if tr.get("max_spread_pips") is not None else None,
                market_context_mode=tr.get("market_context_mode", "warn"),
                max_daily_loss_pct=float(tr.get("max_daily_loss_pct")) if tr.get("max_daily_loss_pct") is not None else None,
                max_drawdown_pct=float(tr.get("max_drawdown_pct")) if tr.get("max_drawdown_pct") is not None else None,
                max_open_risk_pct=float(tr.get("max_open_risk_pct")) if tr.get("max_open_risk_pct") is not None else None,
                panic_stop=bool(tr.get("panic_stop", False)),
                risk_reset_utc_hour=int(tr.get("risk_reset_utc_hour", 0)),
                partial_close=tr.get("partial_close", {}),
                update_actions=tr.get("update_actions", {}),
                symbol_overrides=tr.get("symbol_overrides", []),
                allow_same_symbol_add=tr.get("allow_same_symbol_add", False),
                allow_opposite_direction=tr.get("allow_opposite_direction", False),
                update_aggregation_ms=int(tr.get("update_aggregation_ms")) if tr.get("update_aggregation_ms") is not None else None,
                position_timeout_minutes=float(tr.get("position_timeout_minutes")) if tr.get("position_timeout_minutes") is not None else None,
                close_stale_positions=tr.get("close_stale_positions", False),
            ),
            symbols_filter=doc.get("symbols_filter", []),
        )
