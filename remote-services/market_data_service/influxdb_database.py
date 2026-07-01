"""InfluxDB Cloud Serverless (v3) backend for market data service.

Uses ``influxdb3-python`` for time-series storage and an internal
``SQLiteDatabaseManager`` sidecar for metadata.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from .config import BASE_DIR, get_settings
from .database import BaseDatabaseManager, SQLiteDatabaseManager, _iso_now
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
from .util.rate_limiter import ByteRateLimiter

logger = logging.getLogger(__name__)


# InfluxDB v3 measurement names
_MEASUREMENT_TICKS = "ticks"
_MEASUREMENT_BARS = "bars"
_MEASUREMENT_ORDERBOOK = "orderbook"
_MEASUREMENT_INDICATORS = "indicators"
_MEASUREMENT_SIGNALS = "signals"
_MEASUREMENT_MARKET_STRUCTURE = "market_structure"
_MEASUREMENT_DATA_QUALITY = "data_quality"


def _ms_to_rfc3339(timestamp_ms: int) -> str:
    """Convert milliseconds since epoch to InfluxDB-compatible RFC3339 UTC."""
    dt = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _timeframe_str(tf: TimeFrame | str | None) -> str | None:
    """Return a string timeframe value."""
    if tf is None:
        return None
    return tf.value if hasattr(tf, "value") else str(tf)


class InfluxDBDatabaseManager(BaseDatabaseManager):
    """InfluxDB Cloud Serverless backend with SQLite metadata sidecar.

    Time-series data (ticks, bars, order book, indicators, signals,
    market structure, quality reports) is written to InfluxDB.
    Metadata and operational state (symbols, configs, heartbeats,
    backfill requests, cached symbols, service config) live in a local SQLite
    sidecar for upsert semantics and fast random reads.
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._sidecar: SQLiteDatabaseManager | None = None
        self._connected = False
        self._write_limiter: ByteRateLimiter | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        try:
            from influxdb_client_3 import InfluxDBClient3
        except ImportError as exc:
            raise RuntimeError(
                "InfluxDB backend requires the 'influxdb3-python' package. "
                "Install with: pip install influxdb3-python"
            ) from exc

        settings = get_settings()
        if not settings.influxdb_host or not settings.influxdb_token:
            raise RuntimeError(
                "InfluxDB backend requires MARKET_DATA_INFLUXDB_HOST and "
                "MARKET_DATA_INFLUXDB_TOKEN"
            )

        kwargs: dict[str, Any] = {
            "host": settings.influxdb_host,
            "database": settings.influxdb_database,
            "token": settings.influxdb_token,
        }
        if settings.influxdb_org:
            kwargs["org"] = settings.influxdb_org

        self._client = InfluxDBClient3(**kwargs)
        self._write_limiter = ByteRateLimiter(
            rate=settings.influxdb_max_write_bytes_per_sec,
            burst=settings.influxdb_write_burst_bytes,
        )

        sidecar_path = settings.influxdb_sidecar_path or str(
            BASE_DIR / "influxdb_metadata.db"
        )
        self._sidecar = SQLiteDatabaseManager(sidecar_path)
        await self._sidecar.connect()

        await self._ensure_bucket()
        self._connected = True
        logger.info(
            "Connected to InfluxDB Cloud Serverless: host=%s database=%s sidecar=%s",
            settings.influxdb_host,
            settings.influxdb_database,
            sidecar_path,
        )

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception as exc:
                logger.debug("Error closing InfluxDB client: %s", exc)
            self._client = None
        if self._sidecar is not None:
            await self._sidecar.disconnect()
            self._sidecar = None
        self._connected = False
        logger.info("Disconnected from InfluxDB")

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None and self._sidecar is not None

    async def ensure_indexes(self) -> None:
        """Indexes are not explicitly managed in InfluxDB v3."""
        if self._sidecar:
            await self._sidecar.ensure_indexes()

    # ── Bucket / retention ─────────────────────────────────────────────────────

    async def _ensure_bucket(self) -> None:
        """Create the bucket with free-tier retention if it does not exist.

        InfluxDB Cloud Serverless uses bucket retention to enforce the 30-day
        storage limit. The v3 client does not expose bucket management, so this
        method uses the InfluxDB v2 management API via the same host/token.
        """
        settings = get_settings()
        if settings.influxdb_retention_days <= 0:
            return

        try:
            from influxdb_client import InfluxDBClient as InfluxDBClientV2
            from influxdb_client.client.bucket_api import BucketsApi
        except ImportError:
            logger.debug(
                "influxdb-client (v2) not installed; skipping explicit bucket creation"
            )
            return

        try:
            client = InfluxDBClientV2(
                url=settings.influxdb_host,
                token=settings.influxdb_token,
                org=settings.influxdb_org or "-",
            )
            buckets_api = client.buckets_api()
            bucket = buckets_api.find_bucket_by_name(settings.influxdb_database)
            if bucket is None:
                retention_seconds = settings.influxdb_retention_days * 24 * 60 * 60
                buckets_api.create_bucket(
                    bucket_name=settings.influxdb_database,
                    retention_rules=[
                        {
                            "type": "expire",
                            "everySeconds": retention_seconds,
                            "shardGroupDurationSeconds": 0,
                        }
                    ],
                )
                logger.info(
                    "Created InfluxDB bucket '%s' with %d-day retention",
                    settings.influxdb_database,
                    settings.influxdb_retention_days,
                )
        except Exception as exc:
            logger.warning("Could not ensure InfluxDB bucket/retention: %s", exc)

    # ── Symbol CRUD (sidecar) ──────────────────────────────────────────────────

    async def add_symbol(self, info: SymbolInfo) -> None:
        await self._sidecar_required().add_symbol(info)

    async def remove_symbol(self, symbol_id: int) -> bool:
        return await self._sidecar_required().remove_symbol(symbol_id)

    async def get_symbol(self, symbol_id: int) -> SymbolInfo | None:
        return await self._sidecar_required().get_symbol(symbol_id)

    async def get_symbol_by_name(self, name: str) -> SymbolInfo | None:
        return await self._sidecar_required().get_symbol_by_name(name)

    async def list_symbols(self, status: str | None = None) -> list[SymbolInfo]:
        return await self._sidecar_required().list_symbols(status)

    async def update_symbol(self, symbol_id: int, **kwargs: Any) -> bool:
        return await self._sidecar_required().update_symbol(symbol_id, **kwargs)

    # ── Tick storage ───────────────────────────────────────────────────────────

    async def store_ticks(self, ticks: list[TickData]) -> int:
        if not ticks:
            return 0
        points = [self._tick_to_point(t) for t in ticks]
        await self._write_points(points)
        return len(ticks)

    async def get_ticks(
        self,
        symbol_id: int,
        from_ms: int | None = None,
        to_ms: int | None = None,
        limit: int = 1000,
    ) -> list[TickData]:
        query = self._build_query(
            measurement=_MEASUREMENT_TICKS,
            symbol_id=symbol_id,
            from_ms=from_ms,
            to_ms=to_ms,
            limit=limit,
        )
        table = await self._query(query)
        rows = self._table_to_rows(table)
        return [self._tick_from_row(r) for r in rows]

    async def get_latest_tick(self, symbol_id: int) -> TickData | None:
        query = (
            f"SELECT * FROM {_MEASUREMENT_TICKS} "
            f"WHERE symbol_id = '{symbol_id}' ORDER BY time DESC LIMIT 1"
        )
        rows = self._table_to_rows(await self._query(query))
        return self._tick_from_row(rows[0]) if rows else None

    async def count_ticks(self, symbol_id: int | None = None) -> int:
        return await self._count_measurement(_MEASUREMENT_TICKS, symbol_id)

    # ── Bar storage ────────────────────────────────────────────────────────────

    async def store_bars(self, bars: list[OHLCVBar]) -> int:
        if not bars:
            return 0
        points = [self._bar_to_point(b) for b in bars]
        await self._write_points(points)
        return len(bars)

    async def get_bars(
        self,
        symbol_id: int,
        timeframe: TimeFrame,
        from_ms: int | None = None,
        to_ms: int | None = None,
        limit: int = 1000,
    ) -> list[OHLCVBar]:
        tf = _timeframe_str(timeframe)
        query = self._build_query(
            measurement=_MEASUREMENT_BARS,
            symbol_id=symbol_id,
            timeframe=tf,
            from_ms=from_ms,
            to_ms=to_ms,
            limit=limit,
        )
        rows = self._table_to_rows(await self._query(query))
        return [self._bar_from_row(r) for r in rows]

    async def get_latest_bar(self, symbol_id: int, timeframe: TimeFrame) -> OHLCVBar | None:
        tf = _timeframe_str(timeframe)
        query = (
            f"SELECT * FROM {_MEASUREMENT_BARS} "
            f"WHERE symbol_id = '{symbol_id}' AND timeframe = '{tf}' "
            f"ORDER BY time DESC LIMIT 1"
        )
        rows = self._table_to_rows(await self._query(query))
        return self._bar_from_row(rows[0]) if rows else None

    async def bar_exists(self, symbol_id: int, timeframe: TimeFrame, timestamp_ms: int) -> bool:
        tf = _timeframe_str(timeframe)
        start = _ms_to_rfc3339(timestamp_ms)
        end = _ms_to_rfc3339(timestamp_ms + 1)
        query = (
            f"SELECT COUNT(close) FROM {_MEASUREMENT_BARS} "
            f"WHERE symbol_id = '{symbol_id}' AND timeframe = '{tf}' "
            f"AND time >= '{start}' AND time < '{end}'"
        )
        rows = self._table_to_rows(await self._query(query))
        if not rows:
            return False
        return any(rows[0].get(c) for c in rows[0] if c.lower().startswith("count"))

    async def count_bars(self, symbol_id: int | None = None) -> int:
        return await self._count_measurement(_MEASUREMENT_BARS, symbol_id)

    # ── Order book storage ─────────────────────────────────────────────────────

    async def store_orderbook(self, snapshot: OrderBookSnapshot) -> None:
        point = self._orderbook_to_point(snapshot)
        await self._write_points([point])

    async def get_latest_orderbook(self, symbol_id: int) -> OrderBookSnapshot | None:
        query = (
            f"SELECT * FROM {_MEASUREMENT_ORDERBOOK} "
            f"WHERE symbol_id = '{symbol_id}' ORDER BY time DESC LIMIT 1"
        )
        rows = self._table_to_rows(await self._query(query))
        return self._orderbook_from_row(rows[0]) if rows else None

    # ── Indicator storage ──────────────────────────────────────────────────────

    async def store_indicator(self, indicator: TechnicalIndicator) -> None:
        point = self._indicator_to_point(indicator)
        await self._write_points([point])

    async def get_latest_indicator(
        self, symbol_id: int, indicator: str, timeframe: TimeFrame
    ) -> TechnicalIndicator | None:
        tf = _timeframe_str(timeframe)
        query = (
            f"SELECT * FROM {_MEASUREMENT_INDICATORS} "
            f"WHERE symbol_id = '{symbol_id}' AND indicator = '{indicator}' "
            f"AND timeframe = '{tf}' ORDER BY time DESC LIMIT 1"
        )
        rows = self._table_to_rows(await self._query(query))
        return self._indicator_from_row(rows[0]) if rows else None

    async def get_indicators(
        self,
        symbol_id: int | None = None,
        indicator_type: str | None = None,
        limit: int = 100,
    ) -> list[TechnicalIndicator]:
        conditions: list[str] = []
        if symbol_id is not None:
            conditions.append(f"symbol_id = '{symbol_id}'")
        if indicator_type is not None:
            conditions.append(f"indicator = '{indicator_type}'")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = (
            f"SELECT * FROM {_MEASUREMENT_INDICATORS} {where} "
            f"ORDER BY time DESC LIMIT {limit}"
        )
        rows = self._table_to_rows(await self._query(query))
        return [self._indicator_from_row(r) for r in rows]

    # ── Signal storage ─────────────────────────────────────────────────────────

    async def store_signal(self, signal: TradingSignal) -> None:
        point = self._signal_to_point(signal)
        await self._write_points([point])

    async def get_signals(
        self,
        symbol_id: int | None = None,
        signal_type: str | None = None,
        limit: int = 100,
    ) -> list[TradingSignal]:
        conditions: list[str] = []
        if symbol_id is not None:
            conditions.append(f"symbol_id = '{symbol_id}'")
        if signal_type is not None:
            conditions.append(f"direction = '{signal_type}'")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = (
            f"SELECT * FROM {_MEASUREMENT_SIGNALS} {where} "
            f"ORDER BY time DESC LIMIT {limit}"
        )
        rows = self._table_to_rows(await self._query(query))
        return [self._signal_from_row(r) for r in rows]

    # ── Market structure storage ───────────────────────────────────────────────

    async def store_market_structure(self, structure: Any) -> None:
        point = self._market_structure_to_point(structure)
        await self._write_points([point])

    # ── Data quality ───────────────────────────────────────────────────────────

    async def store_quality_report(self, report: DataQualityReport) -> None:
        point = self._quality_report_to_point(report)
        await self._write_points([point])

    async def get_latest_quality_report(self, symbol_id: int | None = None) -> DataQualityReport | None:
        condition = f"WHERE symbol_id = '{symbol_id}'" if symbol_id is not None else ""
        query = (
            f"SELECT * FROM {_MEASUREMENT_DATA_QUALITY} {condition} "
            f"ORDER BY time DESC LIMIT 1"
        )
        rows = self._table_to_rows(await self._query(query))
        return self._quality_report_from_row(rows[0]) if rows else None

    # ── Symbol config (sidecar) ────────────────────────────────────────────────

    async def upsert_symbol_config(self, config: SymbolConfig) -> None:
        await self._sidecar_required().upsert_symbol_config(config)

    async def get_symbol_config(self, symbol_id: int) -> SymbolConfig | None:
        return await self._sidecar_required().get_symbol_config(symbol_id)

    async def list_symbol_configs(self) -> list[SymbolConfig]:
        return await self._sidecar_required().list_symbol_configs()

    async def delete_symbol_config(self, symbol_id: int) -> bool:
        return await self._sidecar_required().delete_symbol_config(symbol_id)

    # ── Service heartbeats (sidecar) ───────────────────────────────────────────

    async def insert_service_heartbeat(
        self, service: str, timestamp: datetime, data: dict[str, Any] | None = None
    ) -> None:
        await self._sidecar_required().insert_service_heartbeat(service, timestamp, data)

    async def get_latest_service_heartbeat(self, service: str) -> dict[str, Any] | None:
        return await self._sidecar_required().get_latest_service_heartbeat(service)

    # ── Backfill requests (sidecar) ────────────────────────────────────────────

    async def insert_backfill_request(self, doc: dict[str, Any]) -> None:
        await self._sidecar_required().insert_backfill_request(doc)

    async def get_pending_backfill_request(self) -> dict[str, Any] | None:
        return await self._sidecar_required().get_pending_backfill_request()

    async def update_backfill_request(self, request_id: Any, updates: dict[str, Any]) -> bool:
        return await self._sidecar_required().update_backfill_request(request_id, updates)

    async def count_pending_backfills(self) -> int:
        return await self._sidecar_required().count_pending_backfills()

    # ── Service config (sidecar) ───────────────────────────────────────────────

    async def get_service_config(self) -> dict[str, Any] | None:
        return await self._sidecar_required().get_service_config()

    async def update_service_config(self, updates: dict[str, Any]) -> bool:
        return await self._sidecar_required().update_service_config(updates)

    # ── Stats ──────────────────────────────────────────────────────────────────

    async def get_storage_stats(self) -> dict[str, Any]:
        stats = await self._sidecar_required().get_storage_stats()
        # Replace sidecar time-series counts (likely 0) with InfluxDB counts
        for measurement in (
            _MEASUREMENT_TICKS,
            _MEASUREMENT_BARS,
            _MEASUREMENT_ORDERBOOK,
            _MEASUREMENT_SIGNALS,
            _MEASUREMENT_INDICATORS,
        ):
            try:
                stats[measurement] = await self._count_measurement(measurement)
            except Exception:
                stats[measurement] = 0
        return stats

    # ── Maintenance ────────────────────────────────────────────────────────────

    async def delete_old_records(
        self, collection: str, before_ms: int, symbol_ids: set[int] | None = None
    ) -> int:
        """InfluxDB retention policy handles age-based deletion."""
        logger.debug(
            "delete_old_records is a no-op on InfluxDB (retention policy enforced)"
        )
        return 0

    async def compact_collection(self, collection: str) -> None:
        logger.debug("compact_collection is a no-op on InfluxDB")

    # ── Cached symbol lookup (sidecar) ─────────────────────────────────────────

    async def cache_symbols(self, symbols: list[dict[str, Any]], source: str = "ctrader") -> int:
        return await self._sidecar_required().cache_symbols(symbols, source)

    async def get_cached_symbols(
        self, source: str | None = None, search: str | None = None
    ) -> list[dict[str, Any]]:
        return await self._sidecar_required().get_cached_symbols(source, search)

    async def get_cached_symbol(self, symbol_id: int) -> dict[str, Any] | None:
        return await self._sidecar_required().get_cached_symbol(symbol_id)

    async def get_cached_symbol_by_name(self, name: str) -> dict[str, Any] | None:
        return await self._sidecar_required().get_cached_symbol_by_name(name)

    async def clear_cached_symbols(self, source: str | None = None) -> int:
        return await self._sidecar_required().clear_cached_symbols(source)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _sidecar_required(self) -> SQLiteDatabaseManager:
        if self._sidecar is None:
            raise RuntimeError("InfluxDB sidecar not connected")
        return self._sidecar

    def _point(
        self,
        measurement: str,
        symbol_id: int,
        symbol_name: str,
        timestamp_ms: int,
        fields: dict[str, Any],
        timeframe: str | None = None,
    ) -> Any:
        """Build an InfluxDB Point with consistent tags."""
        from influxdb_client_3 import Point, WritePrecision

        point = Point(measurement).tag("symbol_id", str(symbol_id))
        if symbol_name:
            point = point.tag("symbol_name", symbol_name)
        if timeframe:
            point = point.tag("timeframe", timeframe)
        for key, value in fields.items():
            if value is None:
                continue
            if isinstance(value, bool):
                point = point.field(key, value)
            elif isinstance(value, int):
                point = point.field(key, value)
            elif isinstance(value, float):
                point = point.field(key, value)
            else:
                point = point.field(key, str(value))
        point = point.time(timestamp_ms, WritePrecision.MS)
        return point

    async def _write_points(self, points: list[Any]) -> None:
        """Write points to InfluxDB, applying the byte-rate limiter."""
        if not points or self._client is None:
            return

        size_estimate = self._estimate_size(points)
        if self._write_limiter is not None:
            await self._write_limiter.acquire(size_estimate)

        try:
            self._client.write(points)
        except Exception as exc:
            logger.warning("InfluxDB write failed (%d points): %s", len(points), exc)
            raise

    def _estimate_size(self, points: list[Any]) -> int:
        """Estimate serialized line-protocol size for a list of points."""
        total = 0
        for point in points:
            try:
                line = point.to_line_protocol()
                total += len(line.encode("utf-8")) + 1
            except Exception:
                total += 256
        return total

    async def _query(self, query: str) -> Any:
        if self._client is None:
            raise RuntimeError("InfluxDB client not connected")
        try:
            return self._client.query(query=query, language="influxql")
        except Exception as exc:
            logger.warning("InfluxDB query failed: %s | %s", query, exc)
            raise

    def _table_to_rows(self, table: Any) -> list[dict[str, Any]]:
        """Convert a pyarrow Table (or pandas DataFrame) to a list of row dicts."""
        if table is None:
            return []
        try:
            # pandas DataFrame path
            rows = table.to_dict("records")
            if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                return rows
        except Exception:
            pass
        try:
            pydict = table.to_pydict()
            columns = list(pydict.keys())
            n = len(pydict[columns[0]]) if columns else 0
            return [{col: pydict[col][i] for col in columns} for i in range(n)]
        except Exception:
            return []

    def _build_query(
        self,
        measurement: str,
        symbol_id: int,
        timeframe: str | None = None,
        from_ms: int | None = None,
        to_ms: int | None = None,
        limit: int = 1000,
    ) -> str:
        conditions = [f"symbol_id = '{symbol_id}'"]
        if timeframe:
            conditions.append(f"timeframe = '{timeframe}'")
        if from_ms is not None:
            conditions.append(f"time >= '{_ms_to_rfc3339(from_ms)}'")
        if to_ms is not None:
            conditions.append(f"time <= '{_ms_to_rfc3339(to_ms)}'")
        where = " AND ".join(conditions)
        return f"SELECT * FROM {measurement} WHERE {where} ORDER BY time DESC LIMIT {limit}"

    async def _count_measurement(self, measurement: str, symbol_id: int | None = None) -> int:
        field = "bid" if measurement == _MEASUREMENT_TICKS else "close"
        conditions = [f"symbol_id = '{symbol_id}'"] if symbol_id is not None else []
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT COUNT({field}) FROM {measurement} {where}"
        rows = self._table_to_rows(await self._query(query))
        if not rows:
            return 0
        row = rows[0]
        for key, value in row.items():
            if key.lower().startswith("count") and value is not None:
                return int(value)
        return 0

    # ── Point builders ─────────────────────────────────────────────────────────

    def _tick_to_point(self, tick: TickData) -> Any:
        fields = {
            "bid": tick.bid,
            "ask": tick.ask,
            "bid_volume": tick.bid_volume,
            "ask_volume": tick.ask_volume,
            "digits": tick.digits,
        }
        return self._point(
            _MEASUREMENT_TICKS,
            tick.symbol_id,
            tick.symbol_name,
            tick.timestamp_ms,
            fields,
        )

    def _tick_from_row(self, row: dict[str, Any]) -> TickData:
        ts = self._row_timestamp_ms(row)
        return TickData(
            symbol_id=int(row.get("symbol_id", 0)),
            symbol_name=str(row.get("symbol_name", "")),
            bid=float(row.get("bid", 0.0)),
            ask=float(row.get("ask", 0.0)),
            bid_volume=float(row.get("bid_volume", 0.0) or 0.0),
            ask_volume=float(row.get("ask_volume", 0.0) or 0.0),
            timestamp_ms=ts,
            digits=int(row.get("digits", 5) or 5),
        )

    def _bar_to_point(self, bar: OHLCVBar) -> Any:
        fields = {
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "source": str(bar.source.value if hasattr(bar.source, "value") else bar.source),
        }
        return self._point(
            _MEASUREMENT_BARS,
            bar.symbol_id,
            bar.symbol_name,
            bar.timestamp_ms,
            fields,
            timeframe=_timeframe_str(bar.timeframe),
        )

    def _bar_from_row(self, row: dict[str, Any]) -> OHLCVBar:
        from .models import FeedSource

        ts = self._row_timestamp_ms(row)
        return OHLCVBar(
            symbol_id=int(row.get("symbol_id", 0)),
            symbol_name=str(row.get("symbol_name", "")),
            timeframe=TimeFrame(row.get("timeframe", "1m")),
            open=float(row.get("open", 0.0)),
            high=float(row.get("high", 0.0)),
            low=float(row.get("low", 0.0)),
            close=float(row.get("close", 0.0)),
            volume=float(row.get("volume", 0.0) or 0.0),
            timestamp_ms=ts,
            source=FeedSource(row.get("source", "unknown")),
        )

    def _orderbook_to_point(self, snapshot: OrderBookSnapshot) -> Any:
        best_bid = snapshot.bids[0].price if snapshot.bids else 0.0
        best_ask = snapshot.asks[0].price if snapshot.asks else 0.0
        fields = {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": snapshot.spread,
            "bid_depth": snapshot.bid_depth,
            "ask_depth": snapshot.ask_depth,
            "depth_imbalance": snapshot.depth_imbalance,
            "bids_json": json.dumps(
                [{"price": b.price, "volume": b.volume, "side": b.side, "level": b.level}
                 for b in snapshot.bids]
            ),
            "asks_json": json.dumps(
                [{"price": a.price, "volume": a.volume, "side": a.side, "level": a.level}
                 for a in snapshot.asks]
            ),
            "digits": snapshot.digits,
        }
        return self._point(
            _MEASUREMENT_ORDERBOOK,
            snapshot.symbol_id,
            snapshot.symbol_name,
            snapshot.timestamp_ms,
            fields,
        )

    def _orderbook_from_row(self, row: dict[str, Any]) -> OrderBookSnapshot:
        from .models import OrderBookLevel

        ts = self._row_timestamp_ms(row)
        bids = [OrderBookLevel(**b) for b in json.loads(row.get("bids_json", "[]"))]
        asks = [OrderBookLevel(**a) for a in json.loads(row.get("asks_json", "[]"))]
        return OrderBookSnapshot(
            symbol_id=int(row.get("symbol_id", 0)),
            symbol_name=str(row.get("symbol_name", "")),
            bids=bids,
            asks=asks,
            timestamp_ms=ts,
            digits=int(row.get("digits", 5) or 5),
        )

    def _indicator_to_point(self, indicator: TechnicalIndicator) -> Any:
        value = indicator.value
        fields: dict[str, Any] = {
            "indicator": indicator.indicator,
            "period": indicator.period,
            "value_json": json.dumps(value),
        }
        if isinstance(value, (int, float)):
            fields["value"] = float(value)
        elif isinstance(value, dict):
            for k, v in value.items():
                if isinstance(v, (int, float)):
                    fields[f"value_{k}"] = float(v)
        return self._point(
            _MEASUREMENT_INDICATORS,
            indicator.symbol_id,
            indicator.symbol_name,
            indicator.timestamp_ms,
            fields,
            timeframe=_timeframe_str(indicator.timeframe),
        )

    def _indicator_from_row(self, row: dict[str, Any]) -> TechnicalIndicator:
        ts = self._row_timestamp_ms(row)
        value_json = row.get("value_json", "{}")
        value: float | dict[str, float] = 0.0
        try:
            parsed = json.loads(value_json)
            if isinstance(parsed, dict):
                value = parsed
            else:
                value = float(parsed)
        except Exception:
            value = 0.0
        return TechnicalIndicator(
            symbol_id=int(row.get("symbol_id", 0)),
            symbol_name=str(row.get("symbol_name", "")),
            indicator=str(row.get("indicator", "")),
            value=value,
            timeframe=TimeFrame(row.get("timeframe", "1m")),
            period=int(row.get("period", 0) or 0),
            timestamp_ms=ts,
        )

    def _signal_to_point(self, signal: TradingSignal) -> Any:
        fields = {
            "direction": signal.direction,
            "strength": signal.strength,
            "confidence": signal.confidence,
            "indicators_json": json.dumps(signal.indicators),
        }
        return self._point(
            _MEASUREMENT_SIGNALS,
            signal.symbol_id,
            signal.symbol_name,
            signal.timestamp_ms,
            fields,
            timeframe=_timeframe_str(signal.timeframe),
        )

    def _signal_from_row(self, row: dict[str, Any]) -> TradingSignal:
        ts = self._row_timestamp_ms(row)
        return TradingSignal(
            symbol_id=int(row.get("symbol_id", 0)),
            symbol_name=str(row.get("symbol_name", "")),
            direction=str(row.get("direction", "")),
            strength=float(row.get("strength", 0.0)),
            indicators=json.loads(row.get("indicators_json", "[]")),
            confidence=float(row.get("confidence", 0.0)),
            timestamp_ms=ts,
            timeframe=TimeFrame(row.get("timeframe", "1m")),
        )

    def _market_structure_to_point(self, structure: Any) -> Any:
        fields = {
            "swing_highs_json": json.dumps(structure.swing_highs),
            "swing_lows_json": json.dumps(structure.swing_lows),
            "support_levels_json": json.dumps(structure.support_levels),
            "resistance_levels_json": json.dumps(structure.resistance_levels),
            "trend": structure.trend,
            "volatility_regime": structure.volatility_regime,
        }
        return self._point(
            _MEASUREMENT_MARKET_STRUCTURE,
            structure.symbol_id,
            structure.symbol_name,
            structure.timestamp_ms,
            fields,
            timeframe=_timeframe_str(structure.timeframe),
        )

    def _quality_report_to_point(self, report: DataQualityReport) -> Any:
        ts = int(report.checked_at.timestamp() * 1000)
        fields: dict[str, Any] = {
            "total_records": report.total_records,
            "score": report.score,
            "gap_count": report.gap_count,
            "anomaly_count": report.anomaly_count,
            "issues_json": json.dumps(report.issues),
        }
        if report.last_tick_ms is not None:
            fields["last_tick_ms"] = report.last_tick_ms
        if report.last_bar_ms is not None:
            fields["last_bar_ms"] = report.last_bar_ms
        if report.freshness_seconds is not None:
            fields["freshness_seconds"] = report.freshness_seconds
        return self._point(
            _MEASUREMENT_DATA_QUALITY,
            report.symbol_id,
            report.symbol_name,
            ts,
            fields,
            timeframe=_timeframe_str(report.timeframe),
        )

    def _quality_report_from_row(self, row: dict[str, Any]) -> DataQualityReport:
        ts = self._row_timestamp_ms(row)
        tf = TimeFrame(row.get("timeframe", "1m")) if row.get("timeframe") else None
        return DataQualityReport(
            symbol_id=int(row.get("symbol_id", 0)),
            symbol_name=str(row.get("symbol_name", "")),
            total_records=int(row.get("total_records", 0) or 0),
            score=float(row.get("score", 0.0) or 0.0),
            issues=json.loads(row.get("issues_json", "[]")),
            last_tick_ms=int(row.get("last_tick_ms")) if row.get("last_tick_ms") is not None else None,
            freshness_seconds=float(row.get("freshness_seconds")) if row.get("freshness_seconds") is not None else None,
            timeframe=tf,
            gap_count=int(row.get("gap_count", 0) or 0),
            anomaly_count=int(row.get("anomaly_count", 0) or 0),
            last_bar_ms=int(row.get("last_bar_ms")) if row.get("last_bar_ms") is not None else None,
            checked_at=datetime.fromtimestamp(ts / 1000.0, tz=UTC),
        )

    @staticmethod
    def _row_timestamp_ms(row: dict[str, Any]) -> int:
        """Extract timestamp_ms from a row returned by InfluxDB."""
        time_value = row.get("time")
        if isinstance(time_value, int):
            # nanoseconds
            if time_value > 10**15:
                return time_value // 1_000_000
            return time_value
        if hasattr(time_value, "timestamp"):
            return int(time_value.timestamp() * 1000)
        return int(datetime.now(UTC).timestamp() * 1000)
