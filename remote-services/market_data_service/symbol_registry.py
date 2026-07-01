"""Symbol lifecycle management — dynamic registry with persistence."""

from __future__ import annotations

import logging
from typing import Any

from .database import db_manager
from .models import SymbolConfig, SymbolInfo, SymbolStatus

logger = logging.getLogger(__name__)


class SymbolRegistry:
    """In-memory symbol registry backed by MongoDB."""

    def __init__(self) -> None:
        self._symbols: dict[int, SymbolInfo] = {}
        self._by_name: dict[str, int] = {}
        self._configs: dict[int, SymbolConfig] = {}

    async def load_from_db(self) -> None:
        """Hydrate registry from persistent store."""
        symbols = await db_manager.list_symbols()
        for sym in symbols:
            self._symbols[sym.symbol_id] = sym
            self._by_name[sym.name] = sym.symbol_id
        configs = await db_manager.list_symbol_configs()
        for cfg in configs:
            self._configs[cfg.symbol_id] = cfg
        logger.info("Loaded %d symbols from database", len(self._symbols))

    async def add(self, info: SymbolInfo) -> None:
        """Register a new symbol (or update existing)."""
        await db_manager.add_symbol(info)
        self._symbols[info.symbol_id] = info
        self._by_name[info.name] = info.symbol_id
        logger.info("Added symbol %s (id=%d)", info.name, info.symbol_id)

    async def remove(self, symbol_id: int) -> bool:
        """Remove a symbol from registry and database."""
        info = self._symbols.pop(symbol_id, None)
        if info:
            self._by_name.pop(info.name, None)
            self._configs.pop(symbol_id, None)
            await db_manager.remove_symbol(symbol_id)
            logger.info("Removed symbol %s (id=%d)", info.name, symbol_id)
            return True
        return False

    async def update(self, symbol_id: int, **kwargs: Any) -> bool:
        """Update symbol metadata."""
        info = self._symbols.get(symbol_id)
        if not info:
            return False
        await db_manager.update_symbol(symbol_id, **kwargs)
        # Refresh from DB
        updated = await db_manager.get_symbol(symbol_id)
        if updated:
            self._symbols[symbol_id] = updated
            self._by_name[updated.name] = symbol_id
        return True

    def get(self, symbol_id: int) -> SymbolInfo | None:
        return self._symbols.get(symbol_id)

    def get_by_name(self, name: str) -> SymbolInfo | None:
        sid = self._by_name.get(name)
        if sid is not None:
            return self._symbols.get(sid)
        return None

    def resolve(self, identifier: str | int) -> SymbolInfo | None:
        """Resolve by symbol_id (int) or name (str).

        Strings that look like integers are also tried as symbol IDs.
        """
        if isinstance(identifier, int):
            return self.get(identifier)
        # Try name lookup
        sid = self._by_name.get(identifier)
        if sid is not None:
            return self.get(sid)
        # Fallback: numeric string -> symbol_id
        try:
            return self.get(int(identifier))
        except ValueError:
            return None

    def list_all(self, status: SymbolStatus | None = None) -> list[SymbolInfo]:
        symbols = list(self._symbols.values())
        if status:
            symbols = [s for s in symbols if s.status == status]
        return sorted(symbols, key=lambda s: s.name)

    def list_active(self) -> list[SymbolInfo]:
        return self.list_all(SymbolStatus.ACTIVE)

    # ── Config management ────────────────────────────────────────────────────

    async def set_config(self, config: SymbolConfig) -> None:
        await db_manager.upsert_symbol_config(config)
        self._configs[config.symbol_id] = config
        logger.info("Updated config for symbol %d", config.symbol_id)

    def get_config(self, symbol_id: int) -> SymbolConfig | None:
        return self._configs.get(symbol_id)

    def get_or_create_config(self, symbol_id: int, name: str) -> SymbolConfig:
        cfg = self._configs.get(symbol_id)
        if cfg is None:
            cfg = SymbolConfig(symbol_id=symbol_id, name=name)
            self._configs[symbol_id] = cfg
        return cfg

    def active_symbol_ids(self) -> list[int]:
        return [
            sid for sid, info in self._symbols.items()
            if info.status == SymbolStatus.ACTIVE
        ]
