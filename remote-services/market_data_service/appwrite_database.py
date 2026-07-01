"""Appwrite database backend for market data service.

Wraps the synchronous Appwrite Python SDK with asyncio.to_thread() to
implement the async BaseDatabaseManager interface.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from .config import get_settings
from .database import BaseDatabaseManager, _iso_now
from .models import (
    DataQualityReport,
    OHLCVBar,
    OrderBookLevel,
    OrderBookSnapshot,
    SymbolConfig,
    SymbolInfo,
    TechnicalIndicator,
    TickData,
    TimeFrame,
    TradingSignal,
)

logger = logging.getLogger(__name__)


# ── Table schema definitions ─────────────────────────────────────────────────

TABLE_SCHEMAS: dict[str, dict[str, Any]] = {
    "symbols": {
        "columns": [
            {"key": "symbol_id", "type": "integer", "required": True},
            {"key": "name", "type": "varchar", "size": 255, "required": True},
            {"key": "digits", "type": "integer", "required": False},
            {"key": "status", "type": "varchar", "size": 50, "required": False},
            {"key": "description", "type": "text", "required": False},
            {"key": "asset_class", "type": "varchar", "size": 50, "required": False},
            {"key": "lot_size", "type": "integer", "required": False},
            {"key": "exchange", "type": "varchar", "size": 255, "required": False},
            {"key": "pip_position", "type": "integer", "required": False},
            {"key": "tick_size", "type": "float", "required": False},
            {"key": "min_volume", "type": "integer", "required": False},
            {"key": "max_volume", "type": "integer", "required": False},
            {"key": "volume_step", "type": "integer", "required": False},
            {"key": "measurement_units", "type": "varchar", "size": 50, "required": False},
            {"key": "updated_at", "type": "datetime", "required": False},
        ],
    },
    "ticks": {
        "columns": [
            {"key": "symbol_id", "type": "integer", "required": True},
            {"key": "symbol_name", "type": "varchar", "size": 255, "required": True},
            {"key": "bid", "type": "float", "required": True},
            {"key": "ask", "type": "float", "required": True},
            {"key": "bid_volume", "type": "float", "required": False},
            {"key": "ask_volume", "type": "float", "required": False},
            {"key": "timestamp_ms", "type": "integer", "required": True},
            {"key": "digits", "type": "integer", "required": False},
            {"key": "received_at", "type": "datetime", "required": False},
        ],
    },
    "bars": {
        "columns": [
            {"key": "symbol_id", "type": "integer", "required": True},
            {"key": "symbol_name", "type": "varchar", "size": 255, "required": True},
            {"key": "timeframe", "type": "varchar", "size": 20, "required": True},
            {"key": "open", "type": "float", "required": True},
            {"key": "high", "type": "float", "required": True},
            {"key": "low", "type": "float", "required": True},
            {"key": "close", "type": "float", "required": True},
            {"key": "volume", "type": "float", "required": True},
            {"key": "timestamp_ms", "type": "integer", "required": True},
            {"key": "source", "type": "varchar", "size": 50, "required": False},
        ],
    },
    "orderbook": {
        "columns": [
            {"key": "symbol_id", "type": "integer", "required": True},
            {"key": "symbol_name", "type": "varchar", "size": 255, "required": True},
            {"key": "bids_json", "type": "text", "required": True},
            {"key": "asks_json", "type": "text", "required": True},
            {"key": "timestamp_ms", "type": "integer", "required": True},
            {"key": "digits", "type": "integer", "required": False},
        ],
    },
    "indicators": {
        "columns": [
            {"key": "symbol_id", "type": "integer", "required": True},
            {"key": "symbol_name", "type": "varchar", "size": 255, "required": False},
            {"key": "indicator", "type": "varchar", "size": 100, "required": True},
            {"key": "value_json", "type": "text", "required": True},
            {"key": "timeframe", "type": "varchar", "size": 20, "required": True},
            {"key": "period", "type": "integer", "required": False},
            {"key": "timestamp_ms", "type": "integer", "required": True},
        ],
    },
    "signals": {
        "columns": [
            {"key": "symbol_id", "type": "integer", "required": True},
            {"key": "symbol_name", "type": "varchar", "size": 255, "required": False},
            {"key": "direction", "type": "varchar", "size": 50, "required": True},
            {"key": "strength", "type": "float", "required": True},
            {"key": "indicators_json", "type": "text", "required": True},
            {"key": "confidence", "type": "float", "required": True},
            {"key": "timestamp_ms", "type": "integer", "required": True},
            {"key": "timeframe", "type": "varchar", "size": 20, "required": False},
        ],
    },
    "market_structure": {
        "columns": [
            {"key": "symbol_id", "type": "integer", "required": True},
            {"key": "symbol_name", "type": "varchar", "size": 255, "required": False},
            {"key": "timeframe", "type": "varchar", "size": 20, "required": True},
            {"key": "swing_highs_json", "type": "text", "required": False},
            {"key": "swing_lows_json", "type": "text", "required": False},
            {"key": "support_levels_json", "type": "text", "required": False},
            {"key": "resistance_levels_json", "type": "text", "required": False},
            {"key": "trend", "type": "varchar", "size": 100, "required": False},
            {"key": "volatility_regime", "type": "varchar", "size": 100, "required": False},
            {"key": "timestamp_ms", "type": "integer", "required": True},
        ],
    },
    "data_quality": {
        "columns": [
            {"key": "symbol_id", "type": "integer", "required": True},
            {"key": "symbol_name", "type": "varchar", "size": 255, "required": False},
            {"key": "total_records", "type": "integer", "required": True},
            {"key": "score", "type": "float", "required": True},
            {"key": "issues_json", "type": "text", "required": False},
            {"key": "last_tick_ms", "type": "integer", "required": False},
            {"key": "freshness_seconds", "type": "float", "required": False},
            {"key": "timeframe", "type": "varchar", "size": 20, "required": False},
            {"key": "gap_count", "type": "integer", "required": False},
            {"key": "anomaly_count", "type": "integer", "required": False},
            {"key": "last_bar_ms", "type": "integer", "required": False},
            {"key": "checked_at", "type": "datetime", "required": True},
        ],
    },
    "symbol_configs": {
        "columns": [
            {"key": "symbol_id", "type": "integer", "required": True},
            {"key": "name", "type": "varchar", "size": 255, "required": True},
            {"key": "enabled", "type": "boolean", "required": False},
            {"key": "feed_sources_json", "type": "text", "required": False},
            {"key": "collect_ticks", "type": "boolean", "required": False},
            {"key": "collect_bars", "type": "boolean", "required": False},
            {"key": "collect_depth", "type": "boolean", "required": False},
            {"key": "bar_timeframes_json", "type": "text", "required": False},
        ],
    },
    "service_heartbeats": {
        "columns": [
            {"key": "service", "type": "varchar", "size": 100, "required": True},
            {"key": "timestamp", "type": "datetime", "required": True},
            {"key": "data_json", "type": "text", "required": False},
        ],
    },
    "backfill_requests": {
        "columns": [
            {"key": "symbol_id", "type": "integer", "required": True},
            {"key": "symbol_name", "type": "varchar", "size": 255, "required": False},
            {"key": "timeframe", "type": "varchar", "size": 20, "required": True},
            {"key": "status", "type": "varchar", "size": 50, "required": False},
            {"key": "priority", "type": "integer", "required": False},
            {"key": "requested_at", "type": "datetime", "required": True},
            {"key": "started_at", "type": "datetime", "required": False},
            {"key": "completed_at", "type": "datetime", "required": False},
            {"key": "error", "type": "text", "required": False},
            {"key": "bars_expected", "type": "integer", "required": False},
            {"key": "bars_filled", "type": "integer", "required": False},
        ],
    },
    "cached_symbols": {
        "columns": [
            {"key": "symbol_id", "type": "integer", "required": True},
            {"key": "name", "type": "varchar", "size": 255, "required": True},
            {"key": "digits", "type": "integer", "required": False},
            {"key": "description", "type": "text", "required": False},
            {"key": "asset_class", "type": "varchar", "size": 50, "required": False},
            {"key": "lot_size", "type": "integer", "required": False},
            {"key": "exchange", "type": "varchar", "size": 255, "required": False},
            {"key": "pip_position", "type": "integer", "required": False},
            {"key": "tick_size", "type": "float", "required": False},
            {"key": "min_volume", "type": "integer", "required": False},
            {"key": "max_volume", "type": "integer", "required": False},
            {"key": "volume_step", "type": "integer", "required": False},
            {"key": "measurement_units", "type": "varchar", "size": 50, "required": False},
            {"key": "source", "type": "varchar", "size": 50, "required": False},
            {"key": "enabled", "type": "boolean", "required": False},
            {"key": "cached_at", "type": "datetime", "required": False},
        ],
    },
    "service_config": {
        "columns": [
            {"key": "config_json", "type": "text", "required": True},
            {"key": "updated_at", "type": "datetime", "required": False},
        ],
    },
}

# Tables where symbol_id is the natural row key (use str(symbol_id) as $id)
_NATURAL_KEY_TABLES = {"symbols", "symbol_configs", "cached_symbols"}


def _strip_appwrite_meta(row: Any) -> dict[str, Any]:
    """Normalize an Appwrite row/model to a plain dict of user data, preserving $id."""
    # Extract the Appwrite document id first so update/delete operations can use it.
    row_id: Any | None = None
    if hasattr(row, "id"):
        row_id = row.id
    elif hasattr(row, "to_dict"):
        d_meta = row.to_dict()
        row_id = d_meta.get("$id") or d_meta.get("id")
    elif isinstance(row, dict):
        row_id = row.get("$id") or row.get("id")

    # SDK v21+ nests user data under the .data property.
    if hasattr(row, "data"):
        data = row.data
        result = dict(data) if data is not None else {}
    else:
        if hasattr(row, "to_dict"):
            d = row.to_dict()
        elif isinstance(row, dict):
            d = row
        else:
            d = dict(row)
        if isinstance(d.get("data"), dict):
            result = dict(d["data"])
        else:
            result = {k: v for k, v in d.items() if not k.startswith("$")}

    if row_id is not None:
        result["$id"] = row_id
    return result


# ── AppwriteDatabaseManager ──────────────────────────────────────────────────


class AppwriteDatabaseManager(BaseDatabaseManager):
    """Appwrite TablesDB-backed async database manager.

    Wraps the synchronous Appwrite Python SDK with asyncio.to_thread().
    Nested/complex fields are stored as JSON strings, matching the SQLite
    implementation's approach.
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._tables: Any = None
        self._database_id: str = ""
        self._connected = False
        self._existing_tables: set[str] = set()

    async def connect(self) -> None:
        try:
            from appwrite.client import Client
            from appwrite.services.tables_db import TablesDB
        except ImportError:
            raise RuntimeError(
                "Appwrite backend requires the 'appwrite' package. "
                "Install with: pip install appwrite"
            ) from None

        settings = get_settings()
        self._database_id = settings.appwrite_database_id

        def _init() -> None:
            self._client = (
                Client()
                .set_endpoint(settings.appwrite_endpoint)
                .set_project(settings.appwrite_project_id)
                .set_key(settings.appwrite_api_key)
            )
            self._tables = TablesDB(self._client)

        await asyncio.to_thread(_init)
        await self._ensure_database()
        await self._ensure_tables()
        await self.ensure_indexes()
        self._connected = True
        logger.info(
            "Connected to Appwrite: project=%s database=%s",
            settings.appwrite_project_id,
            self._database_id,
        )

    async def disconnect(self) -> None:
        self._client = None
        self._tables = None
        self._connected = False
        logger.info("Disconnected from Appwrite")

    @property
    def is_connected(self) -> bool:
        return self._connected and self._tables is not None

    # ── Schema setup ─────────────────────────────────────────────────────────

    async def _ensure_database(self) -> None:
        from appwrite.exception import AppwriteException

        def _check_or_create() -> None:
            try:
                self._tables.get(database_id=self._database_id)
                logger.debug("Appwrite database '%s' exists", self._database_id)
            except AppwriteException as e:
                if e.code == 404:
                    logger.info(
                        "Creating Appwrite database '%s'", self._database_id
                    )
                    self._tables.create(
                        database_id=self._database_id,
                        name="Market Data Service",
                    )
                else:
                    raise

        await asyncio.to_thread(_check_or_create)

    async def _ensure_tables(self) -> None:
        from appwrite.exception import AppwriteException

        def _list_tables() -> list[str]:
            try:
                result = self._tables.list_tables(database_id=self._database_id)
                if hasattr(result, "tables"):
                    return [t.id for t in result.tables]
                d = result.to_dict() if hasattr(result, "to_dict") else dict(result)
                tables = d.get("tables", [])
                return [t.get("id") or t.get("$id") for t in tables]
            except AppwriteException:
                return []

        existing = await asyncio.to_thread(_list_tables)
        self._existing_tables = set(existing)

        # Map SQLite-ish types to Appwrite TablesDB attribute types
        _TYPE_MAP = {
            "varchar": "string",
            "text": "string",
            "float": "double",
            "integer": "integer",
            "boolean": "boolean",
            "datetime": "datetime",
        }

        for table_id, schema in TABLE_SCHEMAS.items():
            if table_id in self._existing_tables:
                continue
            logger.info("Creating Appwrite table '%s'", table_id)

            def _create(tid: str = table_id, cols: list = schema["columns"]) -> None:
                mapped_cols = []
                for col in cols:
                    mapped = dict(col)
                    mapped["type"] = _TYPE_MAP.get(mapped.get("type", ""), mapped.get("type", ""))
                    if mapped["type"] == "string" and not mapped.get("size"):
                        mapped["size"] = 65535
                    mapped_cols.append(mapped)
                self._tables.create_table(
                    database_id=self._database_id,
                    table_id=tid,
                    name=tid,
                    columns=mapped_cols,
                )

            try:
                await asyncio.to_thread(_create)
                self._existing_tables.add(table_id)
            except AppwriteException as e:
                if e.code == 409:
                    self._existing_tables.add(table_id)
                else:
                    logger.error("Failed to create table '%s': %s", table_id, e)
                    raise

    async def ensure_indexes(self) -> None:
        logger.info("Appwrite indexes ensured (managed by table schema)")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _create_row(self, table_id: str, body: dict[str, Any], row_id: str | None = None) -> dict[str, Any]:
        from appwrite.id import ID

        result = self._tables.create_row(
            database_id=self._database_id,
            table_id=table_id,
            row_id=row_id or ID.unique(),
            data=body,
        )
        return _strip_appwrite_meta(result)

    def _list_rows(self, table_id: str, queries: list | None = None) -> Any:
        return self._tables.list_rows(
            database_id=self._database_id,
            table_id=table_id,
            queries=queries or [],
        )

    def _rows_from_result(self, result: Any) -> list[dict[str, Any]]:
        """Extract and normalize rows from an Appwrite list result."""
        if hasattr(result, "rows"):
            return [_strip_appwrite_meta(r) for r in result.rows]
        d = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        return [_strip_appwrite_meta(r) for r in d.get("rows", [])]

    def _total_from_result(self, result: Any) -> int:
        """Extract total count from an Appwrite list result."""
        if hasattr(result, "total"):
            return result.total
        d = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        return d.get("total", 0)

    def _get_row(self, table_id: str, row_id: str) -> dict[str, Any] | None:
        result = self._tables.get_row(
            database_id=self._database_id,
            table_id=table_id,
            row_id=row_id,
        )
        return _strip_appwrite_meta(result)

    def _update_row(self, table_id: str, row_id: str, body: dict[str, Any]) -> dict[str, Any]:
        result = self._tables.update_row(
            database_id=self._database_id,
            table_id=table_id,
            row_id=row_id,
            data=body,
        )
        return _strip_appwrite_meta(result)

    def _delete_row(self, table_id: str, row_id: str) -> None:
        self._tables.delete_row(
            database_id=self._database_id,
            table_id=table_id,
            row_id=row_id,
        )

    def _find_one(self, table_id: str, queries: list) -> dict[str, Any] | None:
        from appwrite.query import Query

        result = self._list_rows(table_id, queries + [Query.limit(1)])
        rows = self._rows_from_result(result)
        return rows[0] if rows else None

    def _find_many(self, table_id: str, queries: list, limit: int = 100) -> list[dict[str, Any]]:
        from appwrite.query import Query

        result = self._list_rows(table_id, queries + [Query.limit(limit)])
        return self._rows_from_result(result)

    def _count_rows(self, table_id: str, queries: list | None = None) -> int:
        from appwrite.query import Query

        result = self._list_rows(table_id, (queries or []) + [Query.limit(0)])
        return self._total_from_result(result)

    def _upsert_by_query(
        self,
        table_id: str,
        find_queries: list,
        body: dict[str, Any],
        natural_key: str | None = None,
    ) -> None:
        from appwrite.id import ID

        result = self._list_rows(table_id, find_queries)
        # Extract the raw first row so we can read its Appwrite $id
        if hasattr(result, "rows") and result.rows:
            raw = result.rows[0]
            row_id = getattr(raw, "id", None) or raw.to_dict().get("$id")
        else:
            d = result.to_dict() if hasattr(result, "to_dict") else dict(result)
            rows = d.get("rows", [])
            row_id = rows[0].get("$id") if rows else None

        if row_id:
            self._update_row(table_id, row_id, body)
        else:
            row_id = ID.unique()
            if natural_key:
                row_id = str(body.get(natural_key, row_id))
            self._create_row(table_id, body, row_id=row_id)

    # ── Symbol CRUD ──────────────────────────────────────────────────────────

    async def add_symbol(self, info: SymbolInfo) -> None:
        data = info.model_dump(mode="json")
        body = {k: v for k, v in data.items() if v is not None}
        body["updated_at"] = _iso_now()

        def _upsert() -> None:
            self._upsert_by_query(
                "symbols",
                [self._q().equal("symbol_id", info.symbol_id)],
                body,
                natural_key="symbol_id",
            )

        await asyncio.to_thread(_upsert)

    async def remove_symbol(self, symbol_id: int) -> bool:
        def _delete() -> bool:
            row = self._find_one("symbols", [self._q().equal("symbol_id", symbol_id)])
            if not row:
                return False
            self._delete_row("symbols", row["$id"])
            return True

        return await asyncio.to_thread(_delete)

    async def get_symbol(self, symbol_id: int) -> SymbolInfo | None:
        def _get() -> SymbolInfo | None:
            row = self._find_one("symbols", [self._q().equal("symbol_id", symbol_id)])
            if not row:
                return None
            return SymbolInfo(**_strip_appwrite_meta(row))

        return await asyncio.to_thread(_get)

    async def get_symbol_by_name(self, name: str) -> SymbolInfo | None:
        def _get() -> SymbolInfo | None:
            row = self._find_one("symbols", [self._q().equal("name", name)])
            if not row:
                return None
            return SymbolInfo(**_strip_appwrite_meta(row))

        return await asyncio.to_thread(_get)

    async def list_symbols(self, status: str | None = None) -> list[SymbolInfo]:
        from appwrite.query import Query

        def _list() -> list[SymbolInfo]:
            queries = [Query.order_asc("name")]
            if status:
                queries.append(Query.equal("status", status))
            rows = self._find_many("symbols", queries, limit=5000)
            return [SymbolInfo(**_strip_appwrite_meta(r)) for r in rows]

        return await asyncio.to_thread(_list)

    async def update_symbol(self, symbol_id: int, **kwargs: Any) -> bool:
        kwargs["updated_at"] = _iso_now()

        def _update() -> bool:
            row = self._find_one("symbols", [self._q().equal("symbol_id", symbol_id)])
            if not row:
                return False
            self._update_row("symbols", row["$id"], kwargs)
            return True

        return await asyncio.to_thread(_update)

    # ── Tick storage ─────────────────────────────────────────────────────────

    async def store_ticks(self, ticks: list[TickData]) -> int:
        if not ticks:
            return 0
        now = _iso_now()

        def _store() -> int:
            count = 0
            for t in ticks:
                body = {
                    "symbol_id": t.symbol_id,
                    "symbol_name": t.symbol_name,
                    "bid": t.bid,
                    "ask": t.ask,
                    "bid_volume": t.bid_volume,
                    "ask_volume": t.ask_volume,
                    "timestamp_ms": t.timestamp_ms,
                    "digits": t.digits,
                    "received_at": now,
                }
                self._create_row("ticks", body)
                count += 1
            return count

        return await asyncio.to_thread(_store)

    async def get_ticks(
        self,
        symbol_id: int,
        from_ms: int | None = None,
        to_ms: int | None = None,
        limit: int = 1000,
    ) -> list[TickData]:
        from appwrite.query import Query

        def _get() -> list[TickData]:
            queries = [Query.equal("symbol_id", symbol_id), Query.order_desc("timestamp_ms")]
            if from_ms is not None:
                queries.append(Query.greater_than_equal("timestamp_ms", from_ms))
            if to_ms is not None:
                queries.append(Query.less_than_equal("timestamp_ms", to_ms))
            rows = self._find_many("ticks", queries, limit=limit)
            return [TickData(**_strip_appwrite_meta(r)) for r in rows]

        return await asyncio.to_thread(_get)

    async def get_latest_tick(self, symbol_id: int) -> TickData | None:
        from appwrite.query import Query

        def _get() -> TickData | None:
            row = self._find_one("ticks", [
                Query.equal("symbol_id", symbol_id),
                Query.order_desc("timestamp_ms"),
            ])
            if not row:
                return None
            return TickData(**_strip_appwrite_meta(row))

        return await asyncio.to_thread(_get)

    async def count_ticks(self, symbol_id: int | None = None) -> int:
        from appwrite.query import Query

        def _count() -> int:
            queries = []
            if symbol_id is not None:
                queries.append(Query.equal("symbol_id", symbol_id))
            return self._count_rows("ticks", queries)

        return await asyncio.to_thread(_count)

    # ── Bar storage ──────────────────────────────────────────────────────────

    async def store_bars(self, bars: list[OHLCVBar]) -> int:
        if not bars:
            return 0

        def _store() -> int:
            count = 0
            for b in bars:
                tf = b.timeframe.value if hasattr(b.timeframe, "value") else str(b.timeframe)
                src = b.source.value if hasattr(b.source, "value") else str(b.source)
                body = {
                    "symbol_id": b.symbol_id,
                    "symbol_name": b.symbol_name,
                    "timeframe": tf,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                    "timestamp_ms": b.timestamp_ms,
                    "source": src,
                }
                self._upsert_by_query(
                    "bars",
                    [
                        self._q().equal("symbol_id", b.symbol_id),
                        self._q().equal("timeframe", tf),
                        self._q().equal("timestamp_ms", b.timestamp_ms),
                    ],
                    body,
                )
                count += 1
            return count

        return await asyncio.to_thread(_store)

    async def get_bars(
        self,
        symbol_id: int,
        timeframe: TimeFrame,
        from_ms: int | None = None,
        to_ms: int | None = None,
        limit: int = 1000,
    ) -> list[OHLCVBar]:
        from appwrite.query import Query

        tf = timeframe.value if hasattr(timeframe, "value") else str(timeframe)

        def _get() -> list[OHLCVBar]:
            queries = [
                Query.equal("symbol_id", symbol_id),
                Query.equal("timeframe", tf),
                Query.order_desc("timestamp_ms"),
            ]
            if from_ms is not None:
                queries.append(Query.greater_than_equal("timestamp_ms", from_ms))
            if to_ms is not None:
                queries.append(Query.less_than_equal("timestamp_ms", to_ms))
            rows = self._find_many("bars", queries, limit=limit)
            return [OHLCVBar(**_strip_appwrite_meta(r)) for r in rows]

        return await asyncio.to_thread(_get)

    async def get_latest_bar(self, symbol_id: int, timeframe: TimeFrame) -> OHLCVBar | None:
        from appwrite.query import Query

        tf = timeframe.value if hasattr(timeframe, "value") else str(timeframe)

        def _get() -> OHLCVBar | None:
            row = self._find_one("bars", [
                Query.equal("symbol_id", symbol_id),
                Query.equal("timeframe", tf),
                Query.order_desc("timestamp_ms"),
            ])
            if not row:
                return None
            return OHLCVBar(**_strip_appwrite_meta(row))

        return await asyncio.to_thread(_get)

    async def bar_exists(self, symbol_id: int, timeframe: TimeFrame, timestamp_ms: int) -> bool:
        from appwrite.query import Query

        tf = timeframe.value if hasattr(timeframe, "value") else str(timeframe)

        def _check() -> bool:
            row = self._find_one("bars", [
                Query.equal("symbol_id", symbol_id),
                Query.equal("timeframe", tf),
                Query.equal("timestamp_ms", timestamp_ms),
            ])
            return row is not None

        return await asyncio.to_thread(_check)

    async def count_bars(self, symbol_id: int | None = None) -> int:
        from appwrite.query import Query

        def _count() -> int:
            queries = []
            if symbol_id is not None:
                queries.append(Query.equal("symbol_id", symbol_id))
            return self._count_rows("bars", queries)

        return await asyncio.to_thread(_count)

    # ── Order book storage ───────────────────────────────────────────────────

    async def store_orderbook(self, snapshot: OrderBookSnapshot) -> None:
        def _store() -> None:
            bids_json = json.dumps([
                {"price": b.price, "volume": b.volume, "side": b.side, "level": b.level}
                for b in snapshot.bids
            ])
            asks_json = json.dumps([
                {"price": a.price, "volume": a.volume, "side": a.side, "level": a.level}
                for a in snapshot.asks
            ])
            body = {
                "symbol_id": snapshot.symbol_id,
                "symbol_name": snapshot.symbol_name,
                "bids_json": bids_json,
                "asks_json": asks_json,
                "timestamp_ms": snapshot.timestamp_ms,
                "digits": snapshot.digits,
            }
            self._create_row("orderbook", body)

        await asyncio.to_thread(_store)

    async def get_latest_orderbook(self, symbol_id: int) -> OrderBookSnapshot | None:
        from appwrite.query import Query

        def _get() -> OrderBookSnapshot | None:
            row = self._find_one("orderbook", [
                Query.equal("symbol_id", symbol_id),
                Query.order_desc("timestamp_ms"),
            ])
            if not row:
                return None
            d = _strip_appwrite_meta(row)
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

        return await asyncio.to_thread(_get)

    # ── Indicator storage ────────────────────────────────────────────────────

    async def store_indicator(self, indicator: TechnicalIndicator) -> None:
        def _store() -> None:
            tf = indicator.timeframe.value if hasattr(indicator.timeframe, "value") else str(indicator.timeframe)
            value_json = (
                json.dumps(indicator.value)
                if isinstance(indicator.value, dict)
                else json.dumps({"value": indicator.value})
            )
            body = {
                "symbol_id": indicator.symbol_id,
                "symbol_name": indicator.symbol_name,
                "indicator": indicator.indicator,
                "value_json": value_json,
                "timeframe": tf,
                "period": indicator.period,
                "timestamp_ms": indicator.timestamp_ms,
            }
            self._upsert_by_query(
                "indicators",
                [
                    self._q().equal("symbol_id", indicator.symbol_id),
                    self._q().equal("indicator", indicator.indicator),
                    self._q().equal("timeframe", tf),
                    self._q().equal("timestamp_ms", indicator.timestamp_ms),
                ],
                body,
            )

        await asyncio.to_thread(_store)

    async def get_latest_indicator(
        self, symbol_id: int, indicator: str, timeframe: TimeFrame
    ) -> TechnicalIndicator | None:
        from appwrite.query import Query

        tf = timeframe.value if hasattr(timeframe, "value") else str(timeframe)

        def _get() -> TechnicalIndicator | None:
            row = self._find_one("indicators", [
                Query.equal("symbol_id", symbol_id),
                Query.equal("indicator", indicator),
                Query.equal("timeframe", tf),
                Query.order_desc("timestamp_ms"),
            ])
            if not row:
                return None
            return self._indicator_from_row(row)

        return await asyncio.to_thread(_get)

    async def get_indicators(
        self,
        symbol_id: int | None = None,
        indicator_type: str | None = None,
        limit: int = 100,
    ) -> list[TechnicalIndicator]:
        from appwrite.query import Query

        def _get() -> list[TechnicalIndicator]:
            queries = [Query.order_desc("timestamp_ms")]
            if symbol_id is not None:
                queries.append(Query.equal("symbol_id", symbol_id))
            if indicator_type is not None:
                queries.append(Query.equal("indicator", indicator_type))
            rows = self._find_many("indicators", queries, limit=limit)
            return [self._indicator_from_row(r) for r in rows]

        return await asyncio.to_thread(_get)

    # ── Signal storage ───────────────────────────────────────────────────────

    async def store_signal(self, signal: TradingSignal) -> None:
        def _store() -> None:
            tf = signal.timeframe.value if hasattr(signal.timeframe, "value") else str(signal.timeframe)
            body = {
                "symbol_id": signal.symbol_id,
                "symbol_name": signal.symbol_name,
                "direction": signal.direction,
                "strength": signal.strength,
                "indicators_json": json.dumps(signal.indicators),
                "confidence": signal.confidence,
                "timestamp_ms": signal.timestamp_ms,
                "timeframe": tf,
            }
            self._create_row("signals", body)

        await asyncio.to_thread(_store)

    async def get_signals(
        self,
        symbol_id: int | None = None,
        signal_type: str | None = None,
        limit: int = 100,
    ) -> list[TradingSignal]:
        from appwrite.query import Query

        def _get() -> list[TradingSignal]:
            queries = [Query.order_desc("timestamp_ms")]
            if symbol_id is not None:
                queries.append(Query.equal("symbol_id", symbol_id))
            if signal_type is not None:
                queries.append(Query.equal("direction", signal_type))
            rows = self._find_many("signals", queries, limit=limit)
            return [self._signal_from_row(r) for r in rows]

        return await asyncio.to_thread(_get)

    # ── Market structure storage ─────────────────────────────────────────────

    async def store_market_structure(self, structure: Any) -> None:
        def _store() -> None:
            tf = structure.timeframe.value if hasattr(structure.timeframe, "value") else str(structure.timeframe)
            body = {
                "symbol_id": structure.symbol_id,
                "symbol_name": structure.symbol_name,
                "timeframe": tf,
                "swing_highs_json": json.dumps(structure.swing_highs),
                "swing_lows_json": json.dumps(structure.swing_lows),
                "support_levels_json": json.dumps(structure.support_levels),
                "resistance_levels_json": json.dumps(structure.resistance_levels),
                "trend": structure.trend,
                "volatility_regime": structure.volatility_regime,
                "timestamp_ms": structure.timestamp_ms,
            }
            self._upsert_by_query(
                "market_structure",
                [
                    self._q().equal("symbol_id", structure.symbol_id),
                    self._q().equal("timeframe", tf),
                    self._q().equal("timestamp_ms", structure.timestamp_ms),
                ],
                body,
            )

        await asyncio.to_thread(_store)

    # ── Data quality ─────────────────────────────────────────────────────────

    async def store_quality_report(self, report: DataQualityReport) -> None:
        def _store() -> None:
            tf = (
                report.timeframe.value
                if hasattr(report.timeframe, "value")
                else str(report.timeframe)
                if report.timeframe
                else None
            )
            body = {
                "symbol_id": report.symbol_id,
                "symbol_name": report.symbol_name,
                "total_records": report.total_records,
                "score": report.score,
                "issues_json": json.dumps(report.issues),
                "last_tick_ms": report.last_tick_ms,
                "freshness_seconds": report.freshness_seconds,
                "timeframe": tf,
                "gap_count": report.gap_count,
                "anomaly_count": report.anomaly_count,
                "last_bar_ms": report.last_bar_ms,
                "checked_at": _iso_now(),
            }
            self._create_row("data_quality", body)

        await asyncio.to_thread(_store)

    async def get_latest_quality_report(self, symbol_id: int | None = None) -> DataQualityReport | None:
        from appwrite.query import Query

        def _get() -> DataQualityReport | None:
            queries = [Query.order_desc("checked_at")]
            if symbol_id is not None:
                queries.append(Query.equal("symbol_id", symbol_id))
            row = self._find_one("data_quality", queries)
            if not row:
                return None
            return self._dq_from_row(row)

        return await asyncio.to_thread(_get)

    # ── Symbol config ────────────────────────────────────────────────────────

    async def upsert_symbol_config(self, config: SymbolConfig) -> None:
        def _upsert() -> None:
            body = {
                "symbol_id": config.symbol_id,
                "name": config.name,
                "enabled": config.enabled,
                "feed_sources_json": json.dumps([
                    f.value if hasattr(f, "value") else str(f) for f in config.feed_sources
                ]),
                "collect_ticks": config.collect_ticks,
                "collect_bars": config.collect_bars,
                "collect_depth": config.collect_depth,
                "bar_timeframes_json": json.dumps([
                    t.value if hasattr(t, "value") else str(t) for t in config.bar_timeframes
                ]),
            }
            self._upsert_by_query(
                "symbol_configs",
                [self._q().equal("symbol_id", config.symbol_id)],
                body,
                natural_key="symbol_id",
            )

        await asyncio.to_thread(_upsert)

    async def get_symbol_config(self, symbol_id: int) -> SymbolConfig | None:
        def _get() -> SymbolConfig | None:
            row = self._find_one("symbol_configs", [self._q().equal("symbol_id", symbol_id)])
            if not row:
                return None
            return self._config_from_row(row)

        return await asyncio.to_thread(_get)

    async def list_symbol_configs(self) -> list[SymbolConfig]:
        def _list() -> list[SymbolConfig]:
            rows = self._find_many("symbol_configs", [], limit=5000)
            return [self._config_from_row(r) for r in rows]

        return await asyncio.to_thread(_list)

    async def delete_symbol_config(self, symbol_id: int) -> bool:
        def _delete() -> bool:
            row = self._find_one("symbol_configs", [self._q().equal("symbol_id", symbol_id)])
            if not row:
                return False
            self._delete_row("symbol_configs", row["$id"])
            return True

        return await asyncio.to_thread(_delete)

    # ── Service heartbeats ───────────────────────────────────────────────────

    async def insert_service_heartbeat(self, service: str, timestamp: datetime, data: dict[str, Any] | None = None) -> None:
        def _insert() -> None:
            body = {
                "service": service,
                "timestamp": timestamp.isoformat(),
                "data_json": json.dumps(data or {}),
            }
            self._create_row("service_heartbeats", body)

        await asyncio.to_thread(_insert)

    async def get_latest_service_heartbeat(self, service: str) -> dict[str, Any] | None:
        from appwrite.query import Query

        def _get() -> dict[str, Any] | None:
            row = self._find_one("service_heartbeats", [
                Query.equal("service", service),
                Query.order_desc("timestamp"),
            ])
            if not row:
                return None
            d = _strip_appwrite_meta(row)
            data = json.loads(d.pop("data_json", "{}"))
            return {**d, **data}

        return await asyncio.to_thread(_get)

    # ── Backfill requests ────────────────────────────────────────────────────

    async def insert_backfill_request(self, doc: dict[str, Any]) -> None:
        def _insert() -> None:
            body = {
                "symbol_id": doc.get("symbol_id"),
                "symbol_name": doc.get("symbol_name"),
                "timeframe": doc.get("timeframe"),
                "status": doc.get("status", "pending"),
                "priority": doc.get("priority", 0),
                "requested_at": doc.get("requested_at", _iso_now()),
                "started_at": doc.get("started_at"),
                "completed_at": doc.get("completed_at"),
                "error": doc.get("error"),
                "bars_expected": doc.get("bars_expected"),
                "bars_filled": doc.get("bars_filled", 0),
            }
            self._create_row("backfill_requests", body)

        await asyncio.to_thread(_insert)

    async def get_pending_backfill_request(self) -> dict[str, Any] | None:
        from appwrite.query import Query

        def _claim() -> dict[str, Any] | None:
            row = self._find_one("backfill_requests", [
                Query.equal("status", "pending"),
                Query.order_desc("priority"),
                Query.order_asc("requested_at"),
            ])
            if not row:
                return None
            self._update_row("backfill_requests", row["$id"], {
                "status": "running",
                "started_at": _iso_now(),
            })
            return _strip_appwrite_meta(row)

        return await asyncio.to_thread(_claim)

    async def update_backfill_request(self, request_id: Any, updates: dict[str, Any]) -> bool:
        def _update() -> bool:
            try:
                self._update_row("backfill_requests", str(request_id), updates)
                return True
            except Exception:
                return False

        return await asyncio.to_thread(_update)

    async def count_pending_backfills(self) -> int:
        from appwrite.query import Query

        def _count() -> int:
            return self._count_rows("backfill_requests", [Query.equal("status", "pending")])

        return await asyncio.to_thread(_count)

    # ── Service config ───────────────────────────────────────────────────────

    async def get_service_config(self) -> dict[str, Any] | None:
        def _get() -> dict[str, Any] | None:
            try:
                row = self._get_row("service_config", "service_config")
            except Exception:
                return None
            d = _strip_appwrite_meta(row)
            config_str = d.get("config_json", "{}")
            return json.loads(config_str) if config_str else {}

        return await asyncio.to_thread(_get)

    async def update_service_config(self, updates: dict[str, Any]) -> bool:
        def _update() -> bool:
            existing = {}
            try:
                row = self._get_row("service_config", "service_config")
                d = _strip_appwrite_meta(row)
                existing = json.loads(d.get("config_json", "{}"))
            except Exception:
                pass
            existing.update(updates)
            body = {
                "config_json": json.dumps(existing),
                "updated_at": _iso_now(),
            }
            try:
                self._update_row("service_config", "service_config", body)
            except Exception:
                self._create_row("service_config", body, row_id="service_config")
            return True

        return await asyncio.to_thread(_update)

    # ── Stats ────────────────────────────────────────────────────────────────

    async def get_storage_stats(self) -> dict[str, Any]:
        def _stats() -> dict[str, Any]:
            stats = {}
            for table in ["ticks", "bars", "orderbook", "symbols", "signals", "indicators"]:
                try:
                    stats[table] = self._count_rows(table)
                except Exception:
                    stats[table] = 0
            return stats

        return await asyncio.to_thread(_stats)

    # ── Maintenance ──────────────────────────────────────────────────────────

    async def delete_old_records(self, collection: str, before_ms: int, symbol_ids: set[int] | None = None) -> int:
        from appwrite.query import Query

        table_map = {
            "ticks": "ticks", "bars": "bars", "orderbook": "orderbook",
            "signals": "signals", "indicators": "indicators",
        }
        table = table_map.get(collection, collection)

        def _delete() -> int:
            deleted = 0
            while True:
                queries = [Query.less_than("timestamp_ms", before_ms), Query.limit(100)]
                rows = self._find_many(table, queries, limit=100)
                if not rows:
                    break
                for row in rows:
                    row_data = _strip_appwrite_meta(row)
                    if symbol_ids is not None and row_data.get("symbol_id") in symbol_ids:
                        continue
                    self._delete_row(table, row["$id"])
                    deleted += 1
                if len(rows) < 100:
                    break
            return deleted

        return await asyncio.to_thread(_delete)

    async def compact_collection(self, collection: str) -> None:
        logger.debug("compact_collection is a no-op on Appwrite (managed service)")

    # ── Cached symbol lookup ───────────────────────────────────────────────

    async def cache_symbols(self, symbols: list[dict[str, Any]], source: str = "ctrader") -> int:
        if not symbols:
            return 0
        now = _iso_now()

        def _cache() -> int:
            count = 0
            for s in symbols:
                body = {
                    "symbol_id": s.get("symbol_id"),
                    "name": s.get("name", ""),
                    "digits": s.get("digits", 5),
                    "description": s.get("description"),
                    "asset_class": s.get("asset_class"),
                    "lot_size": s.get("lot_size"),
                    "exchange": s.get("exchange"),
                    "pip_position": s.get("pip_position"),
                    "tick_size": s.get("tick_size"),
                    "min_volume": s.get("min_volume"),
                    "max_volume": s.get("max_volume"),
                    "volume_step": s.get("volume_step"),
                    "measurement_units": s.get("measurement_units"),
                    "source": source,
                    "enabled": bool(s.get("enabled", True)),
                    "cached_at": now,
                }
                self._upsert_by_query(
                    "cached_symbols",
                    [self._q().equal("symbol_id", s.get("symbol_id"))],
                    body,
                    natural_key="symbol_id",
                )
                count += 1
            return count

        return await asyncio.to_thread(_cache)

    async def get_cached_symbols(
        self, source: str | None = None, search: str | None = None
    ) -> list[dict[str, Any]]:
        from appwrite.query import Query

        def _get() -> list[dict[str, Any]]:
            queries = [Query.order_asc("name")]
            if source is not None:
                queries.append(Query.equal("source", source))
            if search:
                queries.append(Query.contains("name", search))
            rows = self._find_many("cached_symbols", queries, limit=5000)
            return [_strip_appwrite_meta(r) for r in rows]

        return await asyncio.to_thread(_get)

    async def get_cached_symbol(self, symbol_id: int) -> dict[str, Any] | None:
        def _get() -> dict[str, Any] | None:
            row = self._find_one("cached_symbols", [self._q().equal("symbol_id", symbol_id)])
            return _strip_appwrite_meta(row) if row else None

        return await asyncio.to_thread(_get)

    async def get_cached_symbol_by_name(self, name: str) -> dict[str, Any] | None:
        def _get() -> dict[str, Any] | None:
            row = self._find_one("cached_symbols", [self._q().equal("name", name)])
            return _strip_appwrite_meta(row) if row else None

        return await asyncio.to_thread(_get)

    async def clear_cached_symbols(self, source: str | None = None) -> int:
        from appwrite.query import Query

        def _clear() -> int:
            queries = []
            if source:
                queries.append(Query.equal("source", source))
            deleted = 0
            while True:
                rows = self._find_many("cached_symbols", queries, limit=100)
                if not rows:
                    break
                for row in rows:
                    self._delete_row("cached_symbols", row["$id"])
                    deleted += 1
                if len(rows) < 100:
                    break
            return deleted

        return await asyncio.to_thread(_clear)

    # ── Conversion helpers ────────────────────────────────────────────────────

    @staticmethod
    def _q():
        """Lazy import for Query to avoid module-level appwrite dependency."""
        from appwrite.query import Query
        return Query

    @staticmethod
    def _indicator_from_row(row: dict[str, Any]) -> TechnicalIndicator:
        d = _strip_appwrite_meta(row)
        value_json = json.loads(d.get("value_json", "{}"))
        value: float | dict[str, float] = (
            value_json
            if isinstance(value_json, dict) and len(value_json) > 1
            else value_json.get("value", 0.0)
        )
        return TechnicalIndicator(
            symbol_id=d["symbol_id"],
            symbol_name=d.get("symbol_name", ""),
            indicator=d["indicator"],
            value=value,
            timeframe=TimeFrame(d["timeframe"]),
            period=d.get("period", 0) or 0,
            timestamp_ms=d["timestamp_ms"],
        )

    @staticmethod
    def _signal_from_row(row: dict[str, Any]) -> TradingSignal:
        d = _strip_appwrite_meta(row)
        return TradingSignal(
            symbol_id=d["symbol_id"],
            symbol_name=d.get("symbol_name", ""),
            direction=d["direction"],
            strength=d["strength"],
            indicators=json.loads(d.get("indicators_json", "[]")),
            confidence=d["confidence"],
            timestamp_ms=d["timestamp_ms"],
            timeframe=TimeFrame(d.get("timeframe", "1m")),
        )

    @staticmethod
    def _dq_from_row(row: dict[str, Any]) -> DataQualityReport:
        d = _strip_appwrite_meta(row)
        issues = json.loads(d.get("issues_json", "[]")) if d.get("issues_json") else []
        tf = TimeFrame(d["timeframe"]) if d.get("timeframe") else None
        checked = d.get("checked_at")
        return DataQualityReport(
            symbol_id=d["symbol_id"],
            symbol_name=d.get("symbol_name", ""),
            total_records=d["total_records"],
            score=d["score"],
            issues=issues,
            last_tick_ms=d.get("last_tick_ms"),
            freshness_seconds=d.get("freshness_seconds"),
            timeframe=tf,
            gap_count=d.get("gap_count", 0) or 0,
            anomaly_count=d.get("anomaly_count", 0) or 0,
            last_bar_ms=d.get("last_bar_ms"),
            checked_at=(
                datetime.fromisoformat(checked)
                if isinstance(checked, str)
                else checked or datetime.now(UTC)
            ),
        )

    @staticmethod
    def _config_from_row(row: dict[str, Any]) -> SymbolConfig:
        d = _strip_appwrite_meta(row)
        feed_sources = json.loads(d.get("feed_sources_json", "[]")) if d.get("feed_sources_json") else []
        bar_timeframes = json.loads(d.get("bar_timeframes_json", "[]")) if d.get("bar_timeframes_json") else []
        return SymbolConfig(
            symbol_id=d["symbol_id"],
            name=d["name"],
            enabled=bool(d.get("enabled", True)),
            feed_sources=feed_sources,
            collect_ticks=bool(d.get("collect_ticks", True)),
            collect_bars=bool(d.get("collect_bars", True)),
            collect_depth=bool(d.get("collect_depth", False)),
            bar_timeframes=[TimeFrame(t) for t in bar_timeframes],
        )
