"""Resolves forex symbol names to cTrader symbol IDs."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

SYMBOL_ALIASES: dict[str, str] = {
    "XAUUSD": "XAUUSD",
    "GOLD": "XAUUSD",
    "XAU": "XAUUSD",
    "BTCUSD": "BTCUSD",
    "BITCOIN": "BTCUSD",
    "BTC": "BTCUSD",
    "ETHUSD": "ETHUSD",
    "ETHEREUM": "ETHUSD",
    "ETH": "ETHUSD",
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
    "USDJPY": "USDJPY",
    "GBPJPY": "GBPJPY",
    "AUDUSD": "AUDUSD",
    "USDCAD": "USDCAD",
    "USDCHF": "USDCHF",
    "NZDUSD": "NZDUSD",
    "EURJPY": "EURJPY",
    "EURGBP": "EURGBP",
}


class SymbolResolver:
    """Maps signal symbol names to broker symbol IDs.

    In simulation mode the resolver uses a static map. With a real cTrader
    session it would fetch the symbol list and build name→id lookup tables.
    """

    def __init__(self, session: Any | None = None):
        self._session = session
        self._name_to_id: dict[str, int] = {
            "XAUUSD": 1,
            "BTCUSD": 2,
            "ETHUSD": 3,
            "EURUSD": 4,
            "GBPUSD": 5,
            "USDJPY": 6,
            "GBPJPY": 7,
            "AUDUSD": 8,
            "USDCAD": 9,
            "USDCHF": 10,
            "NZDUSD": 11,
            "EURJPY": 12,
            "EURGBP": 13,
        }
        self._id_to_info: dict[int, dict[str, Any]] = {
            1: {"name": "XAUUSD", "digits": 3, "lot_size": 100, "pip_size": 0.01},
            2: {"name": "BTCUSD", "digits": 2, "lot_size": 1, "pip_size": 1.0},
            3: {"name": "ETHUSD", "digits": 2, "lot_size": 1, "pip_size": 1.0},
            4: {"name": "EURUSD", "digits": 5, "lot_size": 100_000, "pip_size": 0.0001},
            5: {"name": "GBPUSD", "digits": 5, "lot_size": 100_000, "pip_size": 0.0001},
            6: {"name": "USDJPY", "digits": 3, "lot_size": 100_000, "pip_size": 0.01},
            7: {"name": "GBPJPY", "digits": 3, "lot_size": 100_000, "pip_size": 0.01},
            8: {"name": "AUDUSD", "digits": 5, "lot_size": 100_000, "pip_size": 0.0001},
            9: {"name": "USDCAD", "digits": 5, "lot_size": 100_000, "pip_size": 0.0001},
            10: {"name": "USDCHF", "digits": 5, "lot_size": 100_000, "pip_size": 0.0001},
            11: {"name": "NZDUSD", "digits": 5, "lot_size": 100_000, "pip_size": 0.0001},
            12: {"name": "EURJPY", "digits": 3, "lot_size": 100_000, "pip_size": 0.01},
            13: {"name": "EURGBP", "digits": 5, "lot_size": 100_000, "pip_size": 0.0001},
        }
        self._resolved = True

    async def resolve_all(self) -> None:
        if self._session is None:
            return
        logger.info("SymbolResolver: live symbol resolution not implemented in scaffold")

    def resolve_symbol_name(self, name: str) -> str | None:
        upper = name.upper().replace("/", "").strip()
        return SYMBOL_ALIASES.get(upper, upper if upper in self._name_to_id else None)

    def get_symbol_id(self, name: str) -> int | None:
        normalized = self.resolve_symbol_name(name)
        if normalized is None:
            return None
        return self._name_to_id.get(normalized)

    def get_symbol_info(self, symbol_id: int) -> dict[str, Any] | None:
        return self._id_to_info.get(symbol_id)

    @property
    def is_resolved(self) -> bool:
        return self._resolved

    def list_available_symbols(self) -> list[str]:
        return sorted(self._name_to_id.keys())
