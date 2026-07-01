"""Async database access layer supporting SQLite (default), MongoDB (optional), and Appwrite (optional)."""

from __future__ import annotations

import json
import logging
import sqlite3
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import BASE_DIR, get_settings
from .models import (
    DataQualityReport,
    OHLCVBar,
    OrderBookSnapshot,
    SymbolConfig,
    SymbolInfo,
    TechnicalIndicator,
    TickData,
    TimeFrame,
    TradingSignal,
)

logger = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


class BaseDatabaseManager(ABC):
    """Abstract interface for market data persistence."""

    @abstractmethod
    async def connect(self) -> None:
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        ...

    # ── Symbol CRUD ──────────────────────────────────────────────────────────

    @abstractmethod
    async def add_symbol(self, info: SymbolInfo) -> None:
        ...

    @abstractmethod
    async def remove_symbol(self, symbol_id: int) -> bool:
        ...

    @abstractmethod
    async def get_symbol(self, symbol_id: int) -> SymbolInfo | None:
        ...

    @abstractmethod
    async def get_symbol_by_name(self, name: str) -> SymbolInfo | None:
        ...

    @abstractmethod
    async def list_symbols(self, status: str | None = None) -> list[SymbolInfo]:
        ...

    @abstractmethod
    async def update_symbol(self, symbol_id: int, **kwargs: Any) -> bool:
        ...

    # ── Tick storage ─────────────────────────────────────────────────────────

    @abstractmethod
    async def store_ticks(self, ticks: list[TickData]) -> int:
        ...

    @abstractmethod
    async def get_ticks(
        self,
        symbol_id: int,
        from_ms: int | None = None,
        to_ms: int | None = None,
        limit: int = 1000,
    ) -> list[TickData]:
        ...

    @abstractmethod
    async def get_latest_tick(self, symbol_id: int) -> TickData | None:
        ...

    @abstractmethod
    async def count_ticks(self, symbol_id: int | None = None) -> int:
        ...

    # ── Bar storage ──────────────────────────────────────────────────────────

    @abstractmethod
    async def store_bars(self, bars: list[OHLCVBar]) -> int:
        ...

    @abstractmethod
    async def get_bars(
        self,
        symbol_id: int,
        timeframe: TimeFrame,
        from_ms: int | None = None,
        to_ms: int | None = None,
        limit: int = 1000,
    ) -> list[OHLCVBar]:
        ...

    @abstractmethod
    async def get_latest_bar(self, symbol_id: int, timeframe: TimeFrame) -> OHLCVBar | None:
        ...

    @abstractmethod
    async def bar_exists(self, symbol_id: int, timeframe: TimeFrame, timestamp_ms: int) -> bool:
        ...

    @abstractmethod
    async def count_bars(self, symbol_id: int | None = None) -> int:
        ...

    # ── Order book storage ───────────────────────────────────────────────────

    @abstractmethod
    async def store_orderbook(self, snapshot: OrderBookSnapshot) -> None:
        ...

    @abstractmethod
    async def get_latest_orderbook(self, symbol_id: int) -> OrderBookSnapshot | None:
        ...

    # ── Indicator storage ────────────────────────────────────────────────────

    @abstractmethod
    async def store_indicator(self, indicator: TechnicalIndicator) -> None:
        ...

    @abstractmethod
    async def get_latest_indicator(
        self, symbol_id: int, indicator: str, timeframe: TimeFrame
    ) -> TechnicalIndicator | None:
        ...

    @abstractmethod
    async def get_indicators(
        self,
        symbol_id: int | None = None,
        indicator_type: str | None = None,
        limit: int = 100,
    ) -> list[TechnicalIndicator]:
        ...

    # ── Signal storage ───────────────────────────────────────────────────────

    @abstractmethod
    async def store_signal(self, signal: TradingSignal) -> None:
        ...

    @abstractmethod
    async def get_signals(
        self,
        symbol_id: int | None = None,
        signal_type: str | None = None,
        limit: int = 100,
    ) -> list[TradingSignal]:
        ...

    # ── Market structure storage ─────────────────────────────────────────────

    @abstractmethod
    async def store_market_structure(self, structure: Any) -> None:
        ...

    # ── Data quality ─────────────────────────────────────────────────────────

    @abstractmethod
    async def store_quality_report(self, report: DataQualityReport) -> None:
        ...

    @abstractmethod
    async def get_latest_quality_report(self, symbol_id: int | None = None) -> DataQualityReport | None:
        ...

    # ── Symbol config ────────────────────────────────────────────────────────

    @abstractmethod
    async def upsert_symbol_config(self, config: SymbolConfig) -> None:
        ...

    @abstractmethod
    async def get_symbol_config(self, symbol_id: int) -> SymbolConfig | None:
        ...

    @abstractmethod
    async def list_symbol_configs(self) -> list[SymbolConfig]:
        ...

    @abstractmethod
    async def delete_symbol_config(self, symbol_id: int) -> bool:
        ...

    # ── Service heartbeats ───────────────────────────────────────────────────

    @abstractmethod
    async def insert_service_heartbeat(self, service: str, timestamp: datetime, data: dict[str, Any] | None = None) -> None:
        ...

    @abstractmethod
    async def get_latest_service_heartbeat(self, service: str) -> dict[str, Any] | None:
        ...

    # ── Backfill requests ────────────────────────────────────────────────────

    @abstractmethod
    async def insert_backfill_request(self, doc: dict[str, Any]) -> None:
        ...

    @abstractmethod
    async def get_pending_backfill_request(self) -> dict[str, Any] | None:
        ...

    @abstractmethod
    async def update_backfill_request(self, request_id: Any, updates: dict[str, Any]) -> bool:
        ...

    @abstractmethod
    async def count_pending_backfills(self) -> int:
        ...

    # ── Service config ───────────────────────────────────────────────────────

    @abstractmethod
    async def get_service_config(self) -> dict[str, Any] | None:
        ...

    @abstractmethod
    async def update_service_config(self, updates: dict[str, Any]) -> bool:
        ...

    # ── Stats ────────────────────────────────────────────────────────────────

    @abstractmethod
    async def get_storage_stats(self) -> dict[str, Any]:
        ...

    # ── Maintenance ──────────────────────────────────────────────────────────

    @abstractmethod
    async def delete_old_records(self, collection: str, before_ms: int, symbol_ids: set[int] | None = None) -> int:
        ...

    @abstractmethod
    async def compact_collection(self, collection: str) -> None:
        ...

    # ── Cached symbol lookup ───────────────────────────────────────────────

    @abstractmethod
    async def cache_symbols(self, symbols: list[dict[str, Any]], source: str = "ctrader") -> int:
        """Bulk cache available symbol metadata from a feed source."""
        ...

    @abstractmethod
    async def get_cached_symbols(
        self, source: str | None = None, search: str | None = None
    ) -> list[dict[str, Any]]:
        """Return cached symbols, optionally filtered by source or search query."""
        ...

    @abstractmethod
    async def get_cached_symbol(self, symbol_id: int) -> dict[str, Any] | None:
        """Return a single cached symbol by ID."""
        ...

    @abstractmethod
    async def get_cached_symbol_by_name(self, name: str) -> dict[str, Any] | None:
        """Return a single cached symbol by name."""
        ...

    @abstractmethod
    async def clear_cached_symbols(self, source: str | None = None) -> int:
        """Clear cached symbols for a source (or all if source is None)."""
        ...

    @abstractmethod
    async def ensure_indexes(self) -> None:
        ...


# ── SQLite Implementation ────────────────────────────────────────────────────

class SQLiteDatabaseManager(BaseDatabaseManager):
    """SQLite-based async database manager using aiosqlite."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else BASE_DIR / "market_data.db"
        self._conn: Any = None
        self._connected = False

    async def connect(self) -> None:
        import aiosqlite

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        await self._create_tables()
        await self._migrate_schema()
        await self.ensure_indexes()
        self._connected = True
        logger.info("Connected to SQLite: %s", self.db_path)

    async def disconnect(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
        self._connected = False
        logger.info("Disconnected from SQLite")

    @property
    def is_connected(self) -> bool:
        return self._connected and self._conn is not None

    async def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        assert self._conn is not None
        async with self._conn.execute(sql, params) as cursor:
            return cursor

    async def _executemany(self, sql: str, params: list[tuple[Any, ...]]) -> Any:
        assert self._conn is not None
        async with self._conn.executemany(sql, params) as cursor:
            return cursor

    async def _fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        assert self._conn is not None
        async with self._conn.execute(sql, params) as cursor:
            return await cursor.fetchone()

    async def _fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        assert self._conn is not None
        async with self._conn.execute(sql, params) as cursor:
            return await cursor.fetchall()

    async def _migrate_schema(self) -> None:
        """Create any tables that were added after the DB was first created."""
        assert self._conn is not None
        # cached_symbols may be missing on pre-existing DBs
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS cached_symbols (
                symbol_id INTEGER PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                digits INTEGER DEFAULT 5,
                description TEXT,
                asset_class TEXT,
                lot_size INTEGER,
                exchange TEXT,
                pip_position INTEGER,
                tick_size REAL,
                min_volume INTEGER,
                max_volume INTEGER,
                volume_step INTEGER,
                measurement_units TEXT,
                source TEXT DEFAULT 'ctrader',
                enabled INTEGER DEFAULT 1,
                cached_at TEXT
            )
        """)
        await self._conn.commit()

    async def _create_tables(self) -> None:
        assert self._conn is not None
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS symbols (
                symbol_id INTEGER PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                digits INTEGER DEFAULT 5,
                status TEXT DEFAULT 'active',
                description TEXT,
                asset_class TEXT DEFAULT 'forex',
                lot_size INTEGER,
                exchange TEXT,
                pip_position INTEGER,
                tick_size REAL,
                min_volume INTEGER,
                max_volume INTEGER,
                volume_step INTEGER,
                measurement_units TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS ticks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol_id INTEGER NOT NULL,
                symbol_name TEXT NOT NULL,
                bid REAL NOT NULL,
                ask REAL NOT NULL,
                bid_volume REAL DEFAULT 0,
                ask_volume REAL DEFAULT 0,
                timestamp_ms INTEGER NOT NULL,
                digits INTEGER DEFAULT 5,
                received_at TEXT
            );

            CREATE TABLE IF NOT EXISTS bars (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol_id INTEGER NOT NULL,
                symbol_name TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                source TEXT DEFAULT 'unknown',
                UNIQUE(symbol_id, timeframe, timestamp_ms)
            );

            CREATE TABLE IF NOT EXISTS orderbook (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol_id INTEGER NOT NULL,
                symbol_name TEXT NOT NULL,
                bids_json TEXT NOT NULL,
                asks_json TEXT NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                digits INTEGER DEFAULT 5
            );

            CREATE TABLE IF NOT EXISTS indicators (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol_id INTEGER NOT NULL,
                symbol_name TEXT NOT NULL,
                indicator TEXT NOT NULL,
                value_json TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                period INTEGER DEFAULT 0,
                timestamp_ms INTEGER NOT NULL,
                UNIQUE(symbol_id, indicator, timeframe, timestamp_ms)
            );

            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol_id INTEGER NOT NULL,
                symbol_name TEXT NOT NULL,
                direction TEXT NOT NULL,
                strength REAL NOT NULL,
                indicators_json TEXT NOT NULL,
                confidence REAL NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                timeframe TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS market_structure (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol_id INTEGER NOT NULL,
                symbol_name TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                swing_highs_json TEXT,
                swing_lows_json TEXT,
                support_levels_json TEXT,
                resistance_levels_json TEXT,
                trend TEXT,
                volatility_regime TEXT,
                timestamp_ms INTEGER NOT NULL,
                UNIQUE(symbol_id, timeframe, timestamp_ms)
            );

            CREATE TABLE IF NOT EXISTS data_quality (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol_id INTEGER NOT NULL,
                symbol_name TEXT NOT NULL,
                total_records INTEGER NOT NULL,
                score REAL NOT NULL,
                issues_json TEXT,
                last_tick_ms INTEGER,
                freshness_seconds REAL,
                timeframe TEXT,
                gap_count INTEGER DEFAULT 0,
                anomaly_count INTEGER DEFAULT 0,
                last_bar_ms INTEGER,
                checked_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS symbol_configs (
                symbol_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                feed_sources_json TEXT,
                collect_ticks INTEGER DEFAULT 1,
                collect_bars INTEGER DEFAULT 1,
                collect_depth INTEGER DEFAULT 0,
                bar_timeframes_json TEXT
            );

            CREATE TABLE IF NOT EXISTS service_heartbeats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                data_json TEXT
            );

            CREATE TABLE IF NOT EXISTS backfill_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol_id INTEGER NOT NULL,
                symbol_name TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                priority INTEGER DEFAULT 0,
                requested_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                error TEXT,
                bars_expected INTEGER,
                bars_filled INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS cached_symbols (
                symbol_id INTEGER PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                digits INTEGER DEFAULT 5,
                description TEXT,
                asset_class TEXT,
                lot_size INTEGER,
                exchange TEXT,
                pip_position INTEGER,
                tick_size REAL,
                min_volume INTEGER,
                max_volume INTEGER,
                volume_step INTEGER,
                measurement_units TEXT,
                source TEXT DEFAULT 'ctrader',
                enabled INTEGER DEFAULT 1,
                cached_at TEXT
            );

            CREATE TABLE IF NOT EXISTS service_config (
                id TEXT PRIMARY KEY DEFAULT 'service_config',
                config_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT
            );
        """)
        await self._conn.execute(
            "INSERT OR IGNORE INTO service_config (id, config_json, updated_at) VALUES ('service_config', '{}', ?)",
            (_iso_now(),),
        )
        await self._conn.commit()

    async def ensure_indexes(self) -> None:
        assert self._conn is not None
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_ticks_symbol_time ON ticks(symbol_id, timestamp_ms DESC)",
            "CREATE INDEX IF NOT EXISTS idx_ticks_name_time ON ticks(symbol_name, timestamp_ms DESC)",
            "CREATE INDEX IF NOT EXISTS idx_ticks_received ON ticks(received_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_bars_symbol_tf_time ON bars(symbol_id, timeframe, timestamp_ms DESC)",
            "CREATE INDEX IF NOT EXISTS idx_bars_name_tf_time ON bars(symbol_name, timeframe, timestamp_ms DESC)",
            "CREATE INDEX IF NOT EXISTS idx_orderbook_symbol_time ON orderbook(symbol_id, timestamp_ms DESC)",
            "CREATE INDEX IF NOT EXISTS idx_indicators_lookup ON indicators(symbol_id, indicator, timeframe, timestamp_ms DESC)",
            "CREATE INDEX IF NOT EXISTS idx_signals_symbol_time ON signals(symbol_id, timestamp_ms DESC)",
            "CREATE INDEX IF NOT EXISTS idx_signals_type_time ON signals(direction, timestamp_ms DESC)",
            "CREATE INDEX IF NOT EXISTS idx_ms_lookup ON market_structure(symbol_id, timeframe, timestamp_ms DESC)",
            "CREATE INDEX IF NOT EXISTS idx_dq_symbol_checked ON data_quality(symbol_id, checked_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_hb_service_time ON service_heartbeats(service, timestamp DESC)",
            "CREATE INDEX IF NOT EXISTS idx_backfill_status ON backfill_requests(status, requested_at)",
            "CREATE INDEX IF NOT EXISTS idx_backfill_symbol ON backfill_requests(symbol_id, timeframe)",
            "CREATE INDEX IF NOT EXISTS idx_cached_symbols_source ON cached_symbols(source)",
            "CREATE INDEX IF NOT EXISTS idx_cached_symbols_name ON cached_symbols(name)",
        ]
        for sql in indexes:
            await self._conn.execute(sql)
        await self._conn.commit()
        logger.info("SQLite indexes ensured")

    # ── Symbol CRUD ──────────────────────────────────────────────────────────

    async def add_symbol(self, info: SymbolInfo) -> None:
        data = info.model_dump(mode="json")
        data.pop("symbol_id", None)
        columns = ["symbol_id", "name", "digits", "status", "description", "asset_class", "lot_size",
                   "exchange", "pip_position", "tick_size", "min_volume", "max_volume", "volume_step",
                   "measurement_units", "updated_at"]
        values = [info.symbol_id, info.name, info.digits, info.status.value if hasattr(info.status, "value") else str(info.status),
                  info.description, info.asset_class, info.lot_size, info.exchange, info.pip_position,
                  info.tick_size, info.min_volume, info.max_volume, info.volume_step, info.measurement_units,
                  _iso_now()]
        placeholders = ",".join("?" for _ in columns)
        sql = f"INSERT OR REPLACE INTO symbols ({','.join(columns)}) VALUES ({placeholders})"
        await self._execute(sql, tuple(values))
        await self._conn.commit()

    async def remove_symbol(self, symbol_id: int) -> bool:
        cursor = await self._execute("DELETE FROM symbols WHERE symbol_id = ?", (symbol_id,))
        await self._conn.commit()
        return cursor.rowcount > 0

    async def get_symbol(self, symbol_id: int) -> SymbolInfo | None:
        row = await self._fetchone("SELECT * FROM symbols WHERE symbol_id = ?", (symbol_id,))
        return SymbolInfo(**_row_to_dict(row)) if row else None

    async def get_symbol_by_name(self, name: str) -> SymbolInfo | None:
        row = await self._fetchone("SELECT * FROM symbols WHERE name = ?", (name,))
        return SymbolInfo(**_row_to_dict(row)) if row else None

    async def list_symbols(self, status: str | None = None) -> list[SymbolInfo]:
        if status:
            rows = await self._fetchall("SELECT * FROM symbols WHERE status = ? ORDER BY name", (status,))
        else:
            rows = await self._fetchall("SELECT * FROM symbols ORDER BY name")
        return [SymbolInfo(**_row_to_dict(r)) for r in rows]

    async def update_symbol(self, symbol_id: int, **kwargs: Any) -> bool:
        if not kwargs:
            return False
        kwargs["updated_at"] = _iso_now()
        fields = ",".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [symbol_id]
        cursor = await self._execute(f"UPDATE symbols SET {fields} WHERE symbol_id = ?", tuple(values))
        await self._conn.commit()
        return cursor.rowcount > 0

    # ── Tick storage ─────────────────────────────────────────────────────────

    async def store_ticks(self, ticks: list[TickData]) -> int:
        if not ticks:
            return 0
        sql = """INSERT INTO ticks (symbol_id, symbol_name, bid, ask, bid_volume, ask_volume, timestamp_ms, digits, received_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        now = _iso_now()
        params = [(t.symbol_id, t.symbol_name, t.bid, t.ask, t.bid_volume, t.ask_volume, t.timestamp_ms, t.digits, now) for t in ticks]
        await self._executemany(sql, params)
        await self._conn.commit()
        return len(ticks)

    async def get_ticks(self, symbol_id: int, from_ms: int | None = None, to_ms: int | None = None, limit: int = 1000) -> list[TickData]:
        conditions = ["symbol_id = ?"]
        params: list[Any] = [symbol_id]
        if from_ms is not None:
            conditions.append("timestamp_ms >= ?")
            params.append(from_ms)
        if to_ms is not None:
            conditions.append("timestamp_ms <= ?")
            params.append(to_ms)
        where = " AND ".join(conditions)
        rows = await self._fetchall(f"SELECT * FROM ticks WHERE {where} ORDER BY timestamp_ms DESC LIMIT ?", (*params, limit))
        return [_tick_from_row(r) for r in rows]

    async def get_latest_tick(self, symbol_id: int) -> TickData | None:
        row = await self._fetchone("SELECT * FROM ticks WHERE symbol_id = ? ORDER BY timestamp_ms DESC LIMIT 1", (symbol_id,))
        return _tick_from_row(row) if row else None

    async def count_ticks(self, symbol_id: int | None = None) -> int:
        if symbol_id is not None:
            row = await self._fetchone("SELECT COUNT(*) as c FROM ticks WHERE symbol_id = ?", (symbol_id,))
        else:
            row = await self._fetchone("SELECT COUNT(*) as c FROM ticks")
        return row["c"] if row else 0

    # ── Bar storage ──────────────────────────────────────────────────────────

    async def store_bars(self, bars: list[OHLCVBar]) -> int:
        if not bars:
            return 0
        sql = """INSERT OR REPLACE INTO bars (symbol_id, symbol_name, timeframe, open, high, low, close, volume, timestamp_ms, source)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        params = [(b.symbol_id, b.symbol_name, b.timeframe.value if hasattr(b.timeframe, "value") else str(b.timeframe),
                   b.open, b.high, b.low, b.close, b.volume, b.timestamp_ms,
                   b.source.value if hasattr(b.source, "value") else str(b.source)) for b in bars]
        await self._executemany(sql, params)
        await self._conn.commit()
        return len(bars)

    async def get_bars(self, symbol_id: int, timeframe: TimeFrame, from_ms: int | None = None, to_ms: int | None = None, limit: int = 1000) -> list[OHLCVBar]:
        tf = timeframe.value if hasattr(timeframe, "value") else str(timeframe)
        conditions = ["symbol_id = ?", "timeframe = ?"]
        params: list[Any] = [symbol_id, tf]
        if from_ms is not None:
            conditions.append("timestamp_ms >= ?")
            params.append(from_ms)
        if to_ms is not None:
            conditions.append("timestamp_ms <= ?")
            params.append(to_ms)
        where = " AND ".join(conditions)
        rows = await self._fetchall(f"SELECT * FROM bars WHERE {where} ORDER BY timestamp_ms DESC LIMIT ?", (*params, limit))
        return [_bar_from_row(r) for r in rows]

    async def get_latest_bar(self, symbol_id: int, timeframe: TimeFrame) -> OHLCVBar | None:
        tf = timeframe.value if hasattr(timeframe, "value") else str(timeframe)
        row = await self._fetchone("SELECT * FROM bars WHERE symbol_id = ? AND timeframe = ? ORDER BY timestamp_ms DESC LIMIT 1",
                                    (symbol_id, tf))
        return _bar_from_row(row) if row else None

    async def bar_exists(self, symbol_id: int, timeframe: TimeFrame, timestamp_ms: int) -> bool:
        tf = timeframe.value if hasattr(timeframe, "value") else str(timeframe)
        row = await self._fetchone("SELECT 1 FROM bars WHERE symbol_id = ? AND timeframe = ? AND timestamp_ms = ? LIMIT 1",
                                    (symbol_id, tf, timestamp_ms))
        return row is not None

    async def count_bars(self, symbol_id: int | None = None) -> int:
        if symbol_id is not None:
            row = await self._fetchone("SELECT COUNT(*) as c FROM bars WHERE symbol_id = ?", (symbol_id,))
        else:
            row = await self._fetchone("SELECT COUNT(*) as c FROM bars")
        return row["c"] if row else 0

    # ── Order book storage ───────────────────────────────────────────────────

    async def store_orderbook(self, snapshot: OrderBookSnapshot) -> None:
        sql = """INSERT INTO orderbook (symbol_id, symbol_name, bids_json, asks_json, timestamp_ms, digits)
                 VALUES (?, ?, ?, ?, ?, ?)"""
        bids_json = json.dumps([{"price": b.price, "volume": b.volume, "side": b.side, "level": b.level} for b in snapshot.bids])
        asks_json = json.dumps([{"price": a.price, "volume": a.volume, "side": a.side, "level": a.level} for a in snapshot.asks])
        await self._execute(sql, (snapshot.symbol_id, snapshot.symbol_name, bids_json, asks_json, snapshot.timestamp_ms, snapshot.digits))
        await self._conn.commit()

    async def get_latest_orderbook(self, symbol_id: int) -> OrderBookSnapshot | None:
        row = await self._fetchone("SELECT * FROM orderbook WHERE symbol_id = ? ORDER BY timestamp_ms DESC LIMIT 1", (symbol_id,))
        return _ob_from_row(row) if row else None

    # ── Indicator storage ────────────────────────────────────────────────────

    async def store_indicator(self, indicator: TechnicalIndicator) -> None:
        sql = """INSERT OR REPLACE INTO indicators
                 (symbol_id, symbol_name, indicator, value_json, timeframe, period, timestamp_ms)
                 VALUES (?, ?, ?, ?, ?, ?, ?)"""
        value_json = json.dumps(indicator.value) if isinstance(indicator.value, dict) else json.dumps({"value": indicator.value})
        tf = indicator.timeframe.value if hasattr(indicator.timeframe, "value") else str(indicator.timeframe)
        await self._execute(sql, (indicator.symbol_id, indicator.symbol_name, indicator.indicator,
                                   value_json, tf, indicator.period, indicator.timestamp_ms))
        await self._conn.commit()

    async def get_latest_indicator(self, symbol_id: int, indicator: str, timeframe: TimeFrame) -> TechnicalIndicator | None:
        tf = timeframe.value if hasattr(timeframe, "value") else str(timeframe)
        row = await self._fetchone("""SELECT * FROM indicators
            WHERE symbol_id = ? AND indicator = ? AND timeframe = ?
            ORDER BY timestamp_ms DESC LIMIT 1""", (symbol_id, indicator, tf))
        return _indicator_from_row(row) if row else None

    async def get_indicators(self, symbol_id: int | None = None, indicator_type: str | None = None, limit: int = 100) -> list[TechnicalIndicator]:
        conditions: list[str] = []
        params: list[Any] = []
        if symbol_id is not None:
            conditions.append("symbol_id = ?")
            params.append(symbol_id)
        if indicator_type is not None:
            conditions.append("indicator = ?")
            params.append(indicator_type)
        where = " AND ".join(conditions) if conditions else "1=1"
        rows = await self._fetchall(f"SELECT * FROM indicators WHERE {where} ORDER BY timestamp_ms DESC LIMIT ?", (*params, limit))
        return [_indicator_from_row(r) for r in rows]

    # ── Signal storage ───────────────────────────────────────────────────────

    async def store_signal(self, signal: TradingSignal) -> None:
        sql = """INSERT INTO signals (symbol_id, symbol_name, direction, strength, indicators_json, confidence, timestamp_ms, timeframe)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)"""
        tf = signal.timeframe.value if hasattr(signal.timeframe, "value") else str(signal.timeframe)
        await self._execute(sql, (signal.symbol_id, signal.symbol_name, signal.direction, signal.strength,
                                   json.dumps(signal.indicators), signal.confidence, signal.timestamp_ms, tf))
        await self._conn.commit()

    async def get_signals(self, symbol_id: int | None = None, signal_type: str | None = None, limit: int = 100) -> list[TradingSignal]:
        conditions: list[str] = []
        params: list[Any] = []
        if symbol_id is not None:
            conditions.append("symbol_id = ?")
            params.append(symbol_id)
        if signal_type is not None:
            conditions.append("direction = ?")
            params.append(signal_type)
        where = " AND ".join(conditions) if conditions else "1=1"
        rows = await self._fetchall(f"SELECT * FROM signals WHERE {where} ORDER BY timestamp_ms DESC LIMIT ?", (*params, limit))
        return [_signal_from_row(r) for r in rows]

    # ── Market structure storage ─────────────────────────────────────────────

    async def store_market_structure(self, structure: Any) -> None:
        sql = """INSERT OR REPLACE INTO market_structure
                 (symbol_id, symbol_name, timeframe, swing_highs_json, swing_lows_json,
                  support_levels_json, resistance_levels_json, trend, volatility_regime, timestamp_ms)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        tf = structure.timeframe.value if hasattr(structure.timeframe, "value") else str(structure.timeframe)
        await self._execute(sql, (structure.symbol_id, structure.symbol_name, tf,
                                   json.dumps(structure.swing_highs), json.dumps(structure.swing_lows),
                                   json.dumps(structure.support_levels), json.dumps(structure.resistance_levels),
                                   structure.trend, structure.volatility_regime, structure.timestamp_ms))
        await self._conn.commit()

    # ── Data quality ─────────────────────────────────────────────────────────

    async def store_quality_report(self, report: DataQualityReport) -> None:
        sql = """INSERT INTO data_quality
                 (symbol_id, symbol_name, total_records, score, issues_json, last_tick_ms,
                  freshness_seconds, timeframe, gap_count, anomaly_count, last_bar_ms, checked_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        tf = report.timeframe.value if hasattr(report.timeframe, "value") else str(report.timeframe) if report.timeframe else None
        await self._execute(sql, (report.symbol_id, report.symbol_name, report.total_records, report.score,
                                   json.dumps(report.issues), report.last_tick_ms, report.freshness_seconds,
                                   tf, report.gap_count, report.anomaly_count, report.last_bar_ms, _iso_now()))
        await self._conn.commit()

    async def get_latest_quality_report(self, symbol_id: int | None = None) -> DataQualityReport | None:
        if symbol_id is not None:
            row = await self._fetchone("SELECT * FROM data_quality WHERE symbol_id = ? ORDER BY checked_at DESC LIMIT 1", (symbol_id,))
        else:
            row = await self._fetchone("SELECT * FROM data_quality ORDER BY checked_at DESC LIMIT 1")
        return _dq_from_row(row) if row else None

    # ── Symbol config ────────────────────────────────────────────────────────

    async def upsert_symbol_config(self, config: SymbolConfig) -> None:
        sql = """INSERT OR REPLACE INTO symbol_configs
                 (symbol_id, name, enabled, feed_sources_json, collect_ticks, collect_bars, collect_depth, bar_timeframes_json)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)"""
        await self._execute(sql, (config.symbol_id, config.name, int(config.enabled),
                                   json.dumps([f.value if hasattr(f, "value") else str(f) for f in config.feed_sources]),
                                   int(config.collect_ticks), int(config.collect_bars), int(config.collect_depth),
                                   json.dumps([t.value if hasattr(t, "value") else str(t) for t in config.bar_timeframes])))
        await self._conn.commit()

    async def get_symbol_config(self, symbol_id: int) -> SymbolConfig | None:
        row = await self._fetchone("SELECT * FROM symbol_configs WHERE symbol_id = ?", (symbol_id,))
        return _config_from_row(row) if row else None

    async def list_symbol_configs(self) -> list[SymbolConfig]:
        rows = await self._fetchall("SELECT * FROM symbol_configs")
        return [_config_from_row(r) for r in rows]

    async def delete_symbol_config(self, symbol_id: int) -> bool:
        cursor = await self._execute("DELETE FROM symbol_configs WHERE symbol_id = ?", (symbol_id,))
        await self._conn.commit()
        return cursor.rowcount > 0

    # ── Service heartbeats ───────────────────────────────────────────────────

    async def insert_service_heartbeat(self, service: str, timestamp: datetime, data: dict[str, Any] | None = None) -> None:
        sql = "INSERT INTO service_heartbeats (service, timestamp, data_json) VALUES (?, ?, ?)"
        await self._execute(sql, (service, timestamp.isoformat(), json.dumps(data or {})))
        await self._conn.commit()

    async def get_latest_service_heartbeat(self, service: str) -> dict[str, Any] | None:
        row = await self._fetchone(
            "SELECT * FROM service_heartbeats WHERE service = ? ORDER BY timestamp DESC LIMIT 1", (service,))
        return _row_to_dict(row) if row else None

    # ── Backfill requests ────────────────────────────────────────────────────

    async def insert_backfill_request(self, doc: dict[str, Any]) -> None:
        sql = """INSERT INTO backfill_requests
                 (symbol_id, symbol_name, timeframe, status, priority, requested_at, started_at, completed_at, error, bars_expected, bars_filled)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        await self._execute(sql, (
            doc.get("symbol_id"), doc.get("symbol_name"), doc.get("timeframe"),
            doc.get("status", "pending"), doc.get("priority", 0), doc.get("requested_at", _iso_now()),
            doc.get("started_at"), doc.get("completed_at"), doc.get("error"),
            doc.get("bars_expected"), doc.get("bars_filled", 0)
        ))
        await self._conn.commit()

    async def get_pending_backfill_request(self) -> dict[str, Any] | None:
        row = await self._fetchone(
            "SELECT * FROM backfill_requests WHERE status = 'pending' ORDER BY priority DESC, requested_at ASC LIMIT 1")
        return _row_to_dict(row) if row else None

    async def update_backfill_request(self, request_id: Any, updates: dict[str, Any]) -> bool:
        if not updates:
            return False
        fields = ",".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [request_id]
        cursor = await self._execute(f"UPDATE backfill_requests SET {fields} WHERE id = ?", tuple(values))
        await self._conn.commit()
        return cursor.rowcount > 0

    async def count_pending_backfills(self) -> int:
        row = await self._fetchone("SELECT COUNT(*) as c FROM backfill_requests WHERE status = 'pending'")
        return row["c"] if row else 0

    # ── Service config ───────────────────────────────────────────────────────

    async def get_service_config(self) -> dict[str, Any] | None:
        row = await self._fetchone("SELECT config_json FROM service_config WHERE id = 'service_config'")
        if row and row["config_json"]:
            return json.loads(row["config_json"])
        return None

    async def update_service_config(self, updates: dict[str, Any]) -> bool:
        existing = await self.get_service_config() or {}
        existing.update(updates)
        cursor = await self._execute(
            "UPDATE service_config SET config_json = ?, updated_at = ? WHERE id = 'service_config'",
            (json.dumps(existing), _iso_now()))
        await self._conn.commit()
        return cursor.rowcount > 0

    # ── Stats ────────────────────────────────────────────────────────────────

    async def get_storage_stats(self) -> dict[str, Any]:
        stats = {}
        for table in ["ticks", "bars", "orderbook", "symbols", "signals", "indicators"]:
            row = await self._fetchone(f"SELECT COUNT(*) as c FROM {table}")
            stats[table] = row["c"] if row else 0
        return stats

    # ── Maintenance ──────────────────────────────────────────────────────────

    async def delete_old_records(self, collection: str, before_ms: int, symbol_ids: set[int] | None = None) -> int:
        table_map = {
            "ticks": "ticks", "bars": "bars", "orderbook": "orderbook",
            "signals": "signals", "indicators": "indicators",
        }
        table = table_map.get(collection, collection)
        if symbol_ids:
            placeholders = ",".join("?" for _ in symbol_ids)
            cursor = await self._execute(
                f"DELETE FROM {table} WHERE timestamp_ms < ? AND symbol_id NOT IN ({placeholders})",
                (before_ms, *symbol_ids))
        else:
            cursor = await self._execute(f"DELETE FROM {table} WHERE timestamp_ms < ?", (before_ms,))
        await self._conn.commit()
        return cursor.rowcount

    async def cache_symbols(self, symbols: list[dict[str, Any]], source: str = "ctrader") -> int:
        if not symbols:
            return 0
        now = _iso_now()
        sql = """
            INSERT OR REPLACE INTO cached_symbols
            (symbol_id, name, digits, description, asset_class, lot_size, exchange,
             pip_position, tick_size, min_volume, max_volume, volume_step,
             measurement_units, source, enabled, cached_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        rows: list[tuple[Any, ...]] = []
        for s in symbols:
            rows.append((
                s.get("symbol_id"),
                s.get("name", ""),
                s.get("digits", 5),
                s.get("description"),
                s.get("asset_class"),
                s.get("lot_size"),
                s.get("exchange"),
                s.get("pip_position"),
                s.get("tick_size"),
                s.get("min_volume"),
                s.get("max_volume"),
                s.get("volume_step"),
                s.get("measurement_units"),
                source,
                1 if s.get("enabled", True) else 0,
                now,
            ))
        await self._executemany(sql, rows)
        await self._conn.commit()
        return len(rows)

    async def get_cached_symbols(
        self, source: str | None = None, search: str | None = None
    ) -> list[dict[str, Any]]:
        conditions: list[str] = []
        params: list[Any] = []
        if source is not None:
            conditions.append("source = ?")
            params.append(source)
        if search:
            conditions.append("(name LIKE ? OR description LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])
        where = " AND ".join(conditions) if conditions else "1=1"
        rows = await self._fetchall(
            f"SELECT * FROM cached_symbols WHERE {where} ORDER BY name",
            tuple(params),
        )
        return [_row_to_dict(r) for r in rows]

    async def get_cached_symbol(self, symbol_id: int) -> dict[str, Any] | None:
        row = await self._fetchone("SELECT * FROM cached_symbols WHERE symbol_id = ?", (symbol_id,))
        return _row_to_dict(row) if row else None

    async def get_cached_symbol_by_name(self, name: str) -> dict[str, Any] | None:
        row = await self._fetchone("SELECT * FROM cached_symbols WHERE name = ?", (name,))
        return _row_to_dict(row) if row else None

    async def clear_cached_symbols(self, source: str | None = None) -> int:
        if source:
            cursor = await self._execute("DELETE FROM cached_symbols WHERE source = ?", (source,))
        else:
            cursor = await self._execute("DELETE FROM cached_symbols")
        await self._conn.commit()
        return cursor.rowcount

    async def compact_collection(self, collection: str) -> None:
        await self._execute("VACUUM")
        await self._conn.commit()


# ── MongoDB Implementation (optional) ────────────────────────────────────────

class MongoDBDatabaseManager(BaseDatabaseManager):
    """MongoDB-based async database manager using motor."""

    def __init__(self) -> None:
        self._client: Any = None
        self._db: Any = None

    async def connect(self) -> None:
        from motor.motor_asyncio import AsyncIOMotorClient

        settings = get_settings()
        self._client = AsyncIOMotorClient(
            settings.mongodb_uri,
            maxPoolSize=settings.mongodb_max_pool_size,
        )
        self._db = self._client[settings.mongodb_db]
        await self.ensure_indexes()
        logger.info("Connected to MongoDB: %s", settings.mongodb_db)

    async def disconnect(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
            self._db = None
            logger.info("Disconnected from MongoDB")

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._db is not None

    @property
    def db(self) -> Any:
        if self._db is None:
            raise RuntimeError("Database not connected")
        return self._db

    async def ensure_indexes(self) -> None:
        from pymongo import ASCENDING, DESCENDING, IndexModel

        db = self._db
        assert db is not None

        await db.ticks.create_indexes([
            IndexModel([("symbol_id", ASCENDING), ("timestamp_ms", DESCENDING)]),
            IndexModel([("symbol_name", ASCENDING), ("timestamp_ms", DESCENDING)]),
            IndexModel([("received_at", DESCENDING)]),
        ])
        await db.bars.create_indexes([
            IndexModel([("symbol_id", ASCENDING), ("timeframe", ASCENDING), ("timestamp_ms", DESCENDING)], unique=True),
            IndexModel([("symbol_name", ASCENDING), ("timeframe", ASCENDING), ("timestamp_ms", DESCENDING)]),
        ])
        await db.orderbook.create_indexes([
            IndexModel([("symbol_id", ASCENDING), ("timestamp_ms", DESCENDING)]),
        ])
        await db.symbols.create_indexes([
            IndexModel([("symbol_id", ASCENDING)], unique=True),
            IndexModel([("name", ASCENDING)], unique=True),
        ])
        await db.indicators.create_indexes([
            IndexModel([("symbol_id", ASCENDING), ("indicator", ASCENDING), ("timeframe", ASCENDING), ("timestamp_ms", DESCENDING)]),
        ])
        await db.signals.create_indexes([
            IndexModel([("symbol_id", ASCENDING), ("timestamp_ms", DESCENDING)]),
            IndexModel([("signal_type", ASCENDING), ("timestamp_ms", DESCENDING)]),
        ])
        await db.market_structure.create_indexes([
            IndexModel([("symbol_id", ASCENDING), ("timeframe", ASCENDING), ("timestamp_ms", DESCENDING)]),
        ])
        await db.data_quality.create_indexes([
            IndexModel([("symbol_id", ASCENDING), ("checked_at", DESCENDING)]),
        ])
        await db.symbol_configs.create_indexes([
            IndexModel([("symbol_id", ASCENDING)], unique=True),
        ])
        await db.service_heartbeats.create_indexes([
            IndexModel([("service", ASCENDING), ("timestamp", DESCENDING)]),
        ])
        await db.backfill_requests.create_indexes([
            IndexModel([("status", ASCENDING), ("requested_at", ASCENDING)]),
            IndexModel([("symbol_id", ASCENDING), ("timeframe", ASCENDING)]),
        ])
        await db.cached_symbols.create_indexes([
            IndexModel([("symbol_id", ASCENDING)], unique=True),
            IndexModel([("name", ASCENDING)], unique=True),
            IndexModel([("source", ASCENDING)]),
        ])
        logger.info("MongoDB indexes ensured")

    # ── Symbol CRUD ──────────────────────────────────────────────────────────

    async def add_symbol(self, info: SymbolInfo) -> None:
        await self.db.symbols.update_one(
            {"symbol_id": info.symbol_id},
            {"$set": info.model_dump(mode="json")},
            upsert=True,
        )

    async def remove_symbol(self, symbol_id: int) -> bool:
        res = await self.db.symbols.delete_one({"symbol_id": symbol_id})
        return res.deleted_count > 0

    async def get_symbol(self, symbol_id: int) -> SymbolInfo | None:
        doc = await self.db.symbols.find_one({"symbol_id": symbol_id})
        return SymbolInfo(**doc) if doc else None

    async def get_symbol_by_name(self, name: str) -> SymbolInfo | None:
        doc = await self.db.symbols.find_one({"name": name})
        return SymbolInfo(**doc) if doc else None

    async def list_symbols(self, status: str | None = None) -> list[SymbolInfo]:
        query: dict[str, Any] = {}
        if status:
            query["status"] = status
        cursor = self.db.symbols.find(query).sort("name", 1)
        return [SymbolInfo(**doc) async for doc in cursor]

    async def update_symbol(self, symbol_id: int, **kwargs: Any) -> bool:
        from datetime import datetime
        kwargs["updated_at"] = datetime.now(UTC)
        res = await self.db.symbols.update_one({"symbol_id": symbol_id}, {"$set": kwargs})
        return res.modified_count > 0

    # ── Tick storage ─────────────────────────────────────────────────────────

    async def store_ticks(self, ticks: list[TickData]) -> int:
        if not ticks:
            return 0
        docs = [t.model_dump(mode="json") for t in ticks]
        result = await self.db.ticks.insert_many(docs, ordered=False)
        return len(result.inserted_ids)

    async def get_ticks(self, symbol_id: int, from_ms: int | None = None, to_ms: int | None = None, limit: int = 1000) -> list[TickData]:
        query: dict[str, Any] = {"symbol_id": symbol_id}
        if from_ms is not None or to_ms is not None:
            query["timestamp_ms"] = {}
            if from_ms is not None:
                query["timestamp_ms"]["$gte"] = from_ms
            if to_ms is not None:
                query["timestamp_ms"]["$lte"] = to_ms
        cursor = self.db.ticks.find(query).sort("timestamp_ms", -1).limit(limit)
        return [TickData(**doc) async for doc in cursor]

    async def get_latest_tick(self, symbol_id: int) -> TickData | None:
        doc = await self.db.ticks.find_one({"symbol_id": symbol_id}, sort=[("timestamp_ms", -1)])
        return TickData(**doc) if doc else None

    async def count_ticks(self, symbol_id: int | None = None) -> int:
        query = {"symbol_id": symbol_id} if symbol_id is not None else {}
        return await self.db.ticks.count_documents(query)

    # ── Bar storage ──────────────────────────────────────────────────────────

    async def store_bars(self, bars: list[OHLCVBar]) -> int:
        if not bars:
            return 0
        docs = [b.model_dump(mode="json") for b in bars]
        result = await self.db.bars.insert_many(docs, ordered=False)
        return len(result.inserted_ids)

    async def get_bars(self, symbol_id: int, timeframe: TimeFrame, from_ms: int | None = None, to_ms: int | None = None, limit: int = 1000) -> list[OHLCVBar]:
        query: dict[str, Any] = {"symbol_id": symbol_id, "timeframe": timeframe.value}
        if from_ms is not None or to_ms is not None:
            query["timestamp_ms"] = {}
            if from_ms is not None:
                query["timestamp_ms"]["$gte"] = from_ms
            if to_ms is not None:
                query["timestamp_ms"]["$lte"] = to_ms
        cursor = self.db.bars.find(query).sort("timestamp_ms", -1).limit(limit)
        return [OHLCVBar(**doc) async for doc in cursor]

    async def get_latest_bar(self, symbol_id: int, timeframe: TimeFrame) -> OHLCVBar | None:
        doc = await self.db.bars.find_one({"symbol_id": symbol_id, "timeframe": timeframe.value}, sort=[("timestamp_ms", -1)])
        return OHLCVBar(**doc) if doc else None

    async def bar_exists(self, symbol_id: int, timeframe: TimeFrame, timestamp_ms: int) -> bool:
        count = await self.db.bars.count_documents({"symbol_id": symbol_id, "timeframe": timeframe.value, "timestamp_ms": timestamp_ms}, limit=1)
        return count > 0

    async def count_bars(self, symbol_id: int | None = None) -> int:
        query = {"symbol_id": symbol_id} if symbol_id is not None else {}
        return await self.db.bars.count_documents(query)

    # ── Order book storage ───────────────────────────────────────────────────

    async def store_orderbook(self, snapshot: OrderBookSnapshot) -> None:
        await self.db.orderbook.insert_one(snapshot.model_dump(mode="json"))

    async def get_latest_orderbook(self, symbol_id: int) -> OrderBookSnapshot | None:
        doc = await self.db.orderbook.find_one({"symbol_id": symbol_id}, sort=[("timestamp_ms", -1)])
        return OrderBookSnapshot(**doc) if doc else None

    # ── Indicator storage ────────────────────────────────────────────────────

    async def store_indicator(self, indicator: TechnicalIndicator) -> None:
        await self.db.indicators.update_one(
            {"symbol_id": indicator.symbol_id, "indicator": indicator.indicator, "timeframe": indicator.timeframe.value, "timestamp_ms": indicator.timestamp_ms},
            {"$set": indicator.model_dump(mode="json")},
            upsert=True,
        )

    async def get_latest_indicator(self, symbol_id: int, indicator: str, timeframe: TimeFrame) -> TechnicalIndicator | None:
        doc = await self.db.indicators.find_one(
            {"symbol_id": symbol_id, "indicator": indicator, "timeframe": timeframe.value},
            sort=[("timestamp_ms", -1)])
        return TechnicalIndicator(**doc) if doc else None

    async def get_indicators(self, symbol_id: int | None = None, indicator_type: str | None = None, limit: int = 100) -> list[TechnicalIndicator]:
        query: dict[str, Any] = {}
        if symbol_id is not None:
            query["symbol_id"] = symbol_id
        if indicator_type is not None:
            query["indicator"] = indicator_type
        cursor = self.db.indicators.find(query).sort("timestamp_ms", -1).limit(limit)
        return [TechnicalIndicator(**doc) async for doc in cursor]

    # ── Signal storage ───────────────────────────────────────────────────────

    async def store_signal(self, signal: TradingSignal) -> None:
        await self.db.signals.insert_one(signal.model_dump(mode="json"))

    async def get_signals(self, symbol_id: int | None = None, signal_type: str | None = None, limit: int = 100) -> list[TradingSignal]:
        query: dict[str, Any] = {}
        if symbol_id is not None:
            query["symbol_id"] = symbol_id
        if signal_type is not None:
            query["direction"] = signal_type
        cursor = self.db.signals.find(query).sort("timestamp_ms", -1).limit(limit)
        return [TradingSignal(**doc) async for doc in cursor]

    # ── Market structure storage ─────────────────────────────────────────────

    async def store_market_structure(self, structure: Any) -> None:
        await self.db.market_structure.update_one(
            {"symbol_id": structure.symbol_id, "timeframe": structure.timeframe.value, "timestamp_ms": structure.timestamp_ms},
            {"$set": structure.model_dump(mode="json")},
            upsert=True,
        )

    # ── Data quality ─────────────────────────────────────────────────────────

    async def store_quality_report(self, report: DataQualityReport) -> None:
        await self.db.data_quality.insert_one(report.model_dump(mode="json"))

    async def get_latest_quality_report(self, symbol_id: int | None = None) -> DataQualityReport | None:
        query: dict[str, Any] = {}
        if symbol_id is not None:
            query["symbol_id"] = symbol_id
        doc = await self.db.data_quality.find_one(query, sort=[("checked_at", -1)])
        return DataQualityReport(**doc) if doc else None

    # ── Symbol config ────────────────────────────────────────────────────────

    async def upsert_symbol_config(self, config: SymbolConfig) -> None:
        await self.db.symbol_configs.update_one(
            {"symbol_id": config.symbol_id},
            {"$set": config.model_dump(mode="json")},
            upsert=True,
        )

    async def get_symbol_config(self, symbol_id: int) -> SymbolConfig | None:
        doc = await self.db.symbol_configs.find_one({"symbol_id": symbol_id})
        return SymbolConfig(**doc) if doc else None

    async def list_symbol_configs(self) -> list[SymbolConfig]:
        cursor = self.db.symbol_configs.find()
        return [SymbolConfig(**doc) async for doc in cursor]

    async def delete_symbol_config(self, symbol_id: int) -> bool:
        res = await self.db.symbol_configs.delete_one({"symbol_id": symbol_id})
        return res.deleted_count > 0

    # ── Service heartbeats ───────────────────────────────────────────────────

    async def insert_service_heartbeat(self, service: str, timestamp: datetime, data: dict[str, Any] | None = None) -> None:
        doc = {"service": service, "timestamp": timestamp, **(data or {})}
        await self.db.service_heartbeats.insert_one(doc)

    async def get_latest_service_heartbeat(self, service: str) -> dict[str, Any] | None:
        doc = await self.db.service_heartbeats.find_one({"service": service}, sort=[("timestamp", -1)])
        return doc

    # ── Backfill requests ────────────────────────────────────────────────────

    async def insert_backfill_request(self, doc: dict[str, Any]) -> None:
        await self.db.backfill_requests.insert_one(doc)

    async def get_pending_backfill_request(self) -> dict[str, Any] | None:
        doc = await self.db.backfill_requests.find_one_and_update(
            {"status": "pending"},
            {"$set": {"status": "running", "started_at": datetime.now(UTC)}},
            sort=[("priority", -1), ("requested_at", 1)],
        )
        return doc

    async def update_backfill_request(self, request_id: Any, updates: dict[str, Any]) -> bool:
        res = await self.db.backfill_requests.update_one({"_id": request_id}, {"$set": updates})
        return res.modified_count > 0

    async def count_pending_backfills(self) -> int:
        return await self.db.backfill_requests.count_documents({"status": "pending"})

    # ── Service config ───────────────────────────────────────────────────────

    async def get_service_config(self) -> dict[str, Any] | None:
        doc = await self.db.service_config.find_one({"id": "service_config"})
        return doc

    async def update_service_config(self, updates: dict[str, Any]) -> bool:
        res = await self.db.service_config.update_one(
            {"id": "service_config"},
            {"$set": updates, "$setOnInsert": {"id": "service_config"}},
            upsert=True)
        return res.modified_count > 0 or res.upserted_id is not None

    # ── Cached symbol lookup ───────────────────────────────────────────────

    async def cache_symbols(self, symbols: list[dict[str, Any]], source: str = "ctrader") -> int:
        if not symbols:
            return 0
        now = datetime.now(UTC)
        ops: list[dict[str, Any]] = []
        for s in symbols:
            doc = dict(s)
            doc["source"] = source
            doc["cached_at"] = now
            ops.append(
                {"updateOne": {"filter": {"symbol_id": doc["symbol_id"]}, "update": {"$set": doc}, "upsert": True}}
            )
        await self.db.cached_symbols.bulk_write(ops)
        return len(symbols)

    async def get_cached_symbols(
        self, source: str | None = None, search: str | None = None
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {}
        if source is not None:
            query["source"] = source
        if search:
            query["$or"] = [
                {"name": {"$regex": search, "$options": "i"}},
                {"description": {"$regex": search, "$options": "i"}},
            ]
        cursor = self.db.cached_symbols.find(query).sort("name", 1)
        return [doc async for doc in cursor]

    async def get_cached_symbol(self, symbol_id: int) -> dict[str, Any] | None:
        return await self.db.cached_symbols.find_one({"symbol_id": symbol_id})

    async def get_cached_symbol_by_name(self, name: str) -> dict[str, Any] | None:
        return await self.db.cached_symbols.find_one({"name": name})

    async def clear_cached_symbols(self, source: str | None = None) -> int:
        query: dict[str, Any] = {"source": source} if source else {}
        result = await self.db.cached_symbols.delete_many(query)
        return result.deleted_count

    # ── Stats ────────────────────────────────────────────────────────────────

    async def get_storage_stats(self) -> dict[str, Any]:
        stats = {}
        for coll_name in ["ticks", "bars", "orderbook", "symbols", "signals", "indicators"]:
            try:
                stats[coll_name] = await self.db[coll_name].estimated_document_count()
            except Exception:
                stats[coll_name] = 0
        return stats

    # ── Maintenance ──────────────────────────────────────────────────────────

    async def delete_old_records(self, collection: str, before_ms: int, symbol_ids: set[int] | None = None) -> int:
        query: dict[str, Any] = {"timestamp_ms": {"$lt": before_ms}}
        if symbol_ids is not None:
            query["symbol_id"] = {"$nin": list(symbol_ids)}
        result = await self.db[collection].delete_many(query)
        return result.deleted_count

    async def compact_collection(self, collection: str) -> None:
        try:
            await self.db.command({"compact": collection})
        except Exception as e:
            logger.warning("Compact failed for %s: %s", collection, e)


# ── Helper functions ─────────────────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _tick_from_row(row: sqlite3.Row) -> TickData:
    d = _row_to_dict(row)
    return TickData(
        symbol_id=d["symbol_id"],
        symbol_name=d["symbol_name"],
        bid=d["bid"],
        ask=d["ask"],
        bid_volume=d.get("bid_volume", 0.0) or 0.0,
        ask_volume=d.get("ask_volume", 0.0) or 0.0,
        timestamp_ms=d["timestamp_ms"],
        digits=d.get("digits", 5) or 5,
    )


def _bar_from_row(row: sqlite3.Row) -> OHLCVBar:
    d = _row_to_dict(row)
    return OHLCVBar(
        symbol_id=d["symbol_id"],
        symbol_name=d["symbol_name"],
        timeframe=TimeFrame(d["timeframe"]),
        open=d["open"],
        high=d["high"],
        low=d["low"],
        close=d["close"],
        volume=d["volume"],
        timestamp_ms=d["timestamp_ms"],
        source=d.get("source", "unknown"),
    )


def _ob_from_row(row: sqlite3.Row) -> OrderBookSnapshot:
    d = _row_to_dict(row)
    from .models import OrderBookLevel
    bids = [OrderBookLevel(**b) for b in json.loads(d["bids_json"])]
    asks = [OrderBookLevel(**a) for a in json.loads(d["asks_json"])]
    return OrderBookSnapshot(
        symbol_id=d["symbol_id"],
        symbol_name=d["symbol_name"],
        bids=bids,
        asks=asks,
        timestamp_ms=d["timestamp_ms"],
        digits=d.get("digits", 5) or 5,
    )


def _indicator_from_row(row: sqlite3.Row) -> TechnicalIndicator:
    d = _row_to_dict(row)
    value_json = json.loads(d["value_json"])
    value: float | dict[str, float] = value_json if isinstance(value_json, dict) and len(value_json) > 1 else value_json.get("value", 0.0)
    return TechnicalIndicator(
        symbol_id=d["symbol_id"],
        symbol_name=d["symbol_name"],
        indicator=d["indicator"],
        value=value,
        timeframe=TimeFrame(d["timeframe"]),
        period=d.get("period", 0) or 0,
        timestamp_ms=d["timestamp_ms"],
    )


def _signal_from_row(row: sqlite3.Row) -> TradingSignal:
    d = _row_to_dict(row)
    return TradingSignal(
        symbol_id=d["symbol_id"],
        symbol_name=d["symbol_name"],
        direction=d["direction"],
        strength=d["strength"],
        indicators=json.loads(d["indicators_json"]),
        confidence=d["confidence"],
        timestamp_ms=d["timestamp_ms"],
        timeframe=TimeFrame(d["timeframe"]),
    )


def _dq_from_row(row: sqlite3.Row) -> DataQualityReport:
    d = _row_to_dict(row)
    issues = json.loads(d["issues_json"]) if d.get("issues_json") else []
    tf = TimeFrame(d["timeframe"]) if d.get("timeframe") else None
    return DataQualityReport(
        symbol_id=d["symbol_id"],
        symbol_name=d["symbol_name"],
        total_records=d["total_records"],
        score=d["score"],
        issues=issues,
        last_tick_ms=d.get("last_tick_ms"),
        freshness_seconds=d.get("freshness_seconds"),
        timeframe=tf,
        gap_count=d.get("gap_count", 0) or 0,
        anomaly_count=d.get("anomaly_count", 0) or 0,
        last_bar_ms=d.get("last_bar_ms"),
        checked_at=datetime.fromisoformat(d["checked_at"]) if d.get("checked_at") else datetime.now(UTC),
    )


def _config_from_row(row: sqlite3.Row) -> SymbolConfig:
    d = _row_to_dict(row)
    feed_sources = json.loads(d["feed_sources_json"]) if d.get("feed_sources_json") else []
    bar_timeframes = json.loads(d["bar_timeframes_json"]) if d.get("bar_timeframes_json") else []
    return SymbolConfig(
        symbol_id=d["symbol_id"],
        name=d["name"],
        enabled=bool(d.get("enabled", 1)),
        feed_sources=feed_sources,
        collect_ticks=bool(d.get("collect_ticks", 1)),
        collect_bars=bool(d.get("collect_bars", 1)),
        collect_depth=bool(d.get("collect_depth", 0)),
        bar_timeframes=[TimeFrame(t) for t in bar_timeframes],
    )


# ── Factory ──────────────────────────────────────────────────────────────────

def create_db_manager() -> BaseDatabaseManager:
    """Create the appropriate database manager based on configuration."""
    settings = get_settings()
    backend = getattr(settings, "db_backend", "sqlite").lower()

    if backend == "mongodb":
        try:
            return MongoDBDatabaseManager()
        except ImportError as e:
            logger.error("MongoDB backend requested but motor/pymongo not installed: %s", e)
            logger.warning("Falling back to SQLite")
            return SQLiteDatabaseManager()
    elif backend == "appwrite":
        try:
            from .appwrite_database import AppwriteDatabaseManager

            return AppwriteDatabaseManager()
        except ImportError as e:
            logger.error("Appwrite backend requested but appwrite not installed: %s", e)
            logger.warning("Falling back to SQLite")
            return SQLiteDatabaseManager()
    elif backend == "influxdb":
        try:
            from .influxdb_database import InfluxDBDatabaseManager

            return InfluxDBDatabaseManager()
        except ImportError as e:
            logger.error("InfluxDB backend requested but influxdb3-python not installed: %s", e)
            logger.warning("Falling back to SQLite")
            return SQLiteDatabaseManager()
    elif backend == "sqlite":
        db_path = getattr(settings, "sqlite_path", None)
        return SQLiteDatabaseManager(db_path)
    else:
        logger.warning("Unknown db_backend '%s', defaulting to SQLite", backend)
        return SQLiteDatabaseManager()


# Singleton instance
db_manager: BaseDatabaseManager = create_db_manager()


@asynccontextmanager
async def db_session() -> AsyncIterator[BaseDatabaseManager]:
    """Context manager for database operations."""
    await db_manager.connect()
    try:
        yield db_manager
    finally:
        await db_manager.disconnect()
