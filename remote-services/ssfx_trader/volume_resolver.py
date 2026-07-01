"""Resolve signal volume based on per-account trading configuration."""
from __future__ import annotations

import logging
from typing import Any

from ssfx_parser import TradeSignal

from .config import PerAccountTradingConfig, SymbolOverride, VolumeMode
from .risk_calculator import account_value_from_trader, clamp_volume, dollar_to_lots, risk_to_lots
from .symbol_resolver import SymbolResolver

logger = logging.getLogger(__name__)


class VolumeResolver:
    """Convert a trading signal into a concrete lot size for execution."""

    def __init__(
        self,
        trading: PerAccountTradingConfig,
        session: Any,
        resolver: SymbolResolver,
    ):
        self._trading = trading
        self._session = session
        self._resolver = resolver

    def update_config(self, trading: PerAccountTradingConfig) -> None:
        self._trading = trading

    async def resolve_volume(self, signal: TradeSignal) -> float:
        """Return the lot size for ``signal`` based on account config."""
        symbol = signal.symbol or ""
        override = self._trading.get_symbol_override(symbol)

        mode = self._resolve_enum(
            override.volume_mode if override else None,
            self._trading.volume_mode,
        )
        value = (
            override.volume_value
            if override and override.volume_value is not None
            else self._trading.volume_value
        )

        volume: float = self._trading.volume_value
        if mode == VolumeMode.FIXED_LOTS:
            volume = value
        elif mode in (VolumeMode.PERCENT_OF_BALANCE, VolumeMode.PERCENT_OF_EQUITY):
            summary = await self._get_account_summary()
            base = (
                summary["balance"]
                if mode == VolumeMode.PERCENT_OF_BALANCE
                else summary["equity"]
            )
            volume = await self._dollar_to_lots(symbol, base * value / 100.0, signal)
        elif mode == VolumeMode.FIXED_CURRENCY_RISK:
            volume = await self._risk_to_lots(symbol, value, signal)
        elif mode == VolumeMode.PERCENT_RISK:
            summary = await self._get_account_summary()
            risk_amount = summary["balance"] * value / 100.0
            volume = await self._risk_to_lots(symbol, risk_amount, signal)

        return self._clamp_volume(volume, override)

    def _resolve_enum(
        self,
        override_value: VolumeMode | str | None,
        global_value: VolumeMode | str,
    ) -> VolumeMode:
        chosen = override_value if override_value is not None else global_value
        if isinstance(chosen, str):
            return VolumeMode(chosen)
        return chosen

    async def _get_account_summary(self) -> dict[str, float]:
        try:
            trader = await self._session.protocol.get_trader(self._session.account_id)
            return account_value_from_trader(trader)
        except Exception as exc:
            logger.warning("Failed to fetch account summary: %s", exc)
            return {"balance": 0.0, "equity": 0.0}

    async def _dollar_to_lots(self, symbol: str, dollar_amount: float, signal: TradeSignal) -> float:
        price = await self._get_price(symbol, signal)
        lot_size = self._get_lot_size(symbol)
        return dollar_to_lots(dollar_amount, price, lot_size)

    async def _risk_to_lots(self, symbol: str, risk_amount: float, signal: TradeSignal) -> float:
        if signal.entry_price is None or signal.sl_float is None:
            logger.warning(
                "Risk-based sizing requires entry and SL; falling back to volume_value"
            )
            return self._trading.volume_value
        lot_size = self._get_lot_size(symbol)
        return risk_to_lots(risk_amount, signal.entry_price, signal.sl_float, lot_size)

    async def _get_price(self, symbol: str, signal: TradeSignal) -> float:
        symbol_id = self._resolver.get_symbol_id(symbol)
        if symbol_id is not None and hasattr(self._session, "market_data"):
            price = self._session.market_data.get_last_price(symbol_id)
            if price is not None and price > 0:
                return price
        if signal.entry_price is not None and signal.entry_price > 0:
            return signal.entry_price
        logger.warning("No price available for %s; using 1.0", symbol)
        return 1.0

    def _get_lot_size(self, symbol: str) -> float:
        symbol_id = self._resolver.get_symbol_id(symbol)
        if symbol_id is None:
            return 100_000.0
        info = self._resolver.get_symbol_info(symbol_id)
        if info is None:
            return 100_000.0
        return float(info.get("lot_size", 100_000))

    def _clamp_volume(self, volume: float, override: SymbolOverride | None) -> float:
        min_vol = self._trading.min_volume_lots
        max_vol = self._trading.max_volume_lots
        return clamp_volume(volume, min_vol, max_vol)
