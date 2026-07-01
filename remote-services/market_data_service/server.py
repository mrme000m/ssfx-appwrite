"""MCP Market Data Service — lightweight server, stateless, reads/writes MongoDB only.

The actual data ingestion, feed management, and analytics are handled by the
standalone Data Service (`data_service.py`). This MCP server only:
- Reads market data from MongoDB
- Manages symbol registry and configs in MongoDB
- Exposes tools and resources for AI agent consumption
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
from mcp.types import Resource, TextContent, Tool

from .analytics import AnalyticsEngine
from .config import get_settings
from .database import db_manager
from .models import (
    DataQualityReport,
    SymbolConfig,
    SymbolInfo,
    SymbolStatus,
    TimeFrame,
)
from .symbol_registry import SymbolRegistry

logger = logging.getLogger(__name__)

# ── Global state ─────────────────────────────────────────────────────────────

symbol_registry = SymbolRegistry()
analytics = AnalyticsEngine()
_start_time = time.time()


# ── MCP Server ───────────────────────────────────────────────────────────────

server = Server("market-data-service")


@server.list_resources()
async def list_resources() -> list[Resource]:
    """Expose URI-addressable market data resources."""
    resources: list[Resource] = [
        Resource(
            uri="marketdata://stats",
            name="System Statistics",
            mimeType="application/json",
            description="Database performance and storage metrics",
        ),
        Resource(
            uri="marketdata://symbols",
            name="Symbol Catalog",
            mimeType="application/json",
            description="Complete symbol registry with metadata",
        ),
    ]
    for sym in symbol_registry.list_active():
        resources.append(Resource(
            uri=f"marketdata://price/{sym.name}",
            name=f"{sym.name} Price Snapshot",
            mimeType="application/json",
            description=f"Latest price for {sym.name}",
        ))
        for tf in [TimeFrame.M1, TimeFrame.H1, TimeFrame.D1]:
            resources.append(Resource(
                uri=f"marketdata://bars/{sym.name}/{tf.value}",
                name=f"{sym.name} {tf.value} Bars",
                mimeType="application/json",
                description=f"OHLCV bars for {sym.name} {tf.value}",
            ))
    return resources


@server.read_resource()
async def read_resource(uri: Any) -> list[ReadResourceContents]:
    """Read a market data resource by URI."""
    uri_str = str(uri)
    mime = "application/json"

    if uri_str == "marketdata://stats":
        stats = await db_manager.get_storage_stats()
        syms = await db_manager.list_symbols(status="active")
        health = {
            "active_symbols": len(syms),
            "db_connected": db_manager.is_connected,
            "uptime_seconds": time.time() - _start_time,
            **stats,
        }
        return [ReadResourceContents(
            content=_json_dumps(health),
            mime_type=mime,
        )]

    if uri_str == "marketdata://symbols":
        symbols = symbol_registry.list_all()
        return [ReadResourceContents(
            content=_json_dumps([_to_json(s) for s in symbols]),
            mime_type=mime,
        )]

    if uri_str.startswith("marketdata://price/"):
        symbol_name = uri_str.split("/")[-1]
        sym = symbol_registry.get_by_name(symbol_name)
        if not sym:
            return [ReadResourceContents(
                content=_json_dumps({"error": f"Symbol {symbol_name} not found"}),
                mime_type=mime,
            )]
        tick = await db_manager.get_latest_tick(sym.symbol_id)
        if tick:
            return [ReadResourceContents(
                content=_json_dumps(_to_json(tick)),
                mime_type=mime,
            )]
        return [ReadResourceContents(
            content=_json_dumps({"symbol": symbol_name, "bid": None, "ask": None, "status": "no data"}),
            mime_type=mime,
        )]

    if uri_str.startswith("marketdata://bars/"):
        parts = uri_str.replace("marketdata://bars/", "").split("/")
        if len(parts) >= 2:
            symbol_name = parts[0]
            tf = parts[1]
            sym = symbol_registry.get_by_name(symbol_name)
            if sym:
                bars = await db_manager.get_bars(sym.symbol_id, TimeFrame(tf), limit=100)
                return [ReadResourceContents(
                    content=_json_dumps([_to_json(b) for b in bars]),
                    mime_type=mime,
                )]
        return [ReadResourceContents(
            content=_json_dumps({"error": "Invalid bars URI"}),
            mime_type=mime,
        )]

    return [ReadResourceContents(
        content=_json_dumps({"error": f"Unknown resource: {uri_str}"}),
        mime_type=mime,
    )]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        # Batch
        Tool(
            name="batch_call",
            description="Execute multiple tool calls in a single request",
            inputSchema={
                "type": "object",
                "properties": {
                    "calls": {
                        "type": "array",
                        "description": "List of tool calls to execute sequentially",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Tool name"},
                                "arguments": {"type": "object", "description": "Tool arguments"},
                            },
                            "required": ["name"],
                        },
                    },
                },
                "required": ["calls"],
            },
        ),
        # Symbol Lifecycle
        Tool(
            name="add_symbol",
            description="Register a new trading instrument with metadata",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol_id": {"type": "integer"},
                    "name": {"type": "string"},
                    "digits": {"type": "integer", "default": 5},
                    "pip_size": {"type": "number"},
                    "asset_class": {"type": "string", "default": "forex"},
                    "description": {"type": "string"},
                    "exchange": {"type": "string"},
                    "lot_size": {"type": "integer"},
                },
                "required": ["symbol_id", "name"],
            },
        ),
        Tool(
            name="remove_symbol",
            description="Remove a symbol from the registry",
            inputSchema={
                "type": "object",
                "properties": {"symbol_id": {"type": "integer"}},
                "required": ["symbol_id"],
            },
        ),
        Tool(
            name="list_symbols",
            description="List all registered symbols",
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["active", "inactive", "suspended"]},
                },
            },
        ),
        Tool(
            name="update_symbol",
            description="Update symbol metadata",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol_id": {"type": "integer"},
                    "fields": {"type": "object"},
                },
                "required": ["symbol_id", "fields"],
            },
        ),
        Tool(
            name="get_market_context",
            description="Return an aggregated market context snapshot for a symbol (tick, bars, indicators, signals, quality)",
            inputSchema={
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
                "required": ["symbol"],
            },
        ),
        # Data Retrieval
        Tool(
            name="get_price",
            description="Get current price quote for a symbol",
            inputSchema={
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
                "required": ["symbol"],
            },
        ),
        Tool(
            name="get_ticks",
            description="Get historical tick data with time filtering",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "from_ms": {"type": "integer"},
                    "to_ms": {"type": "integer"},
                    "limit": {"type": "integer", "default": 1000},
                },
                "required": ["symbol"],
            },
        ),
        Tool(
            name="get_bars",
            description="Get OHLCV bars for a symbol and timeframe",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "timeframe": {"type": "string", "enum": [t.value for t in TimeFrame]},
                    "from_ms": {"type": "integer"},
                    "to_ms": {"type": "integer"},
                    "limit": {"type": "integer", "default": 500},
                },
                "required": ["symbol", "timeframe"],
            },
        ),
        Tool(
            name="get_orderbook",
            description="Get latest order book snapshot for a symbol",
            inputSchema={
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
                "required": ["symbol"],
            },
        ),
        # Analytics
        Tool(
            name="get_indicators",
            description="Calculate and retrieve technical indicators for a symbol",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "timeframe": {"type": "string", "default": "1h"},
                },
                "required": ["symbol"],
            },
        ),
        Tool(
            name="get_signals",
            description="Generate trading signals for a symbol",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "timeframe": {"type": "string", "default": "1h"},
                },
                "required": ["symbol"],
            },
        ),
        Tool(
            name="get_market_structure",
            description="Detect market structure (supports, resistances, swings)",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "timeframe": {"type": "string", "default": "1h"},
                },
                "required": ["symbol"],
            },
        ),
        # Data Quality
        Tool(
            name="check_quality",
            description="Run data quality check for a symbol",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "timeframe": {"type": "string"},
                },
            },
        ),
        Tool(
            name="backfill_gaps",
            description="Backfill missing historical data gaps (requires active Data Service)",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "timeframe": {"type": "string"},
                    "from_ms": {"type": "integer"},
                    "to_ms": {"type": "integer"},
                },
                "required": ["symbol", "timeframe", "from_ms", "to_ms"],
            },
        ),
        # Config
        Tool(
            name="set_symbol_config",
            description="Set symbol-specific configuration (Data Service will apply)",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol_id": {"type": "integer"},
                    "name": {"type": "string"},
                    "enabled": {"type": "boolean"},
                    "collect_ticks": {"type": "boolean"},
                    "collect_bars": {"type": "boolean"},
                    "collect_depth": {"type": "boolean"},
                    "bar_timeframes": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["symbol_id", "name"],
            },
        ),
        Tool(
            name="get_stats",
            description="Get system and database statistics",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_feed_status",
            description="Get Data Service feed connection status",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


async def _execute_tool(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Execute a single tool and return the raw result dict."""
    args = arguments or {}
    result: dict[str, Any] = {}

    # ── Batch ────────────────────────────────────────────────────────────
    if name == "batch_call":
        calls = args.get("calls", [])
        if not isinstance(calls, list):
            return {"error": "'calls' must be a list"}
        results: list[dict[str, Any]] = []
        for i, call in enumerate(calls):
            call_name = call.get("name", "")
            call_args = call.get("arguments", {})
            if call_name == "batch_call":
                results.append({
                    "index": i,
                    "name": call_name,
                    "status": "error",
                    "error": "Nested batch_call is not allowed",
                })
                continue
            try:
                sub = await _execute_tool(call_name, call_args)
                results.append({"index": i, "name": call_name, "status": "success", "data": sub})
            except Exception as exc:
                logger.exception("Batch sub-tool %s failed", call_name)
                results.append({"index": i, "name": call_name, "status": "error", "error": str(exc)})
        return {"results": results, "count": len(results)}

    # ── Symbol Lifecycle ─────────────────────────────────────────────────
    if name == "add_symbol":
        info = SymbolInfo(**args)
        await symbol_registry.add(info)
        result = {"success": True, "symbol": _to_json(info)}

    elif name == "remove_symbol":
        ok = await symbol_registry.remove(args["symbol_id"])
        result = {"success": ok}

    elif name == "list_symbols":
        status = args.get("status")
        syms = symbol_registry.list_all(
            SymbolStatus(status) if status else None
        )
        result = {"symbols": [_to_json(s) for s in syms]}

    elif name == "update_symbol":
        ok = await symbol_registry.update(args["symbol_id"], **args.get("fields", {}))
        result = {"success": ok}

    elif name == "get_market_context":
        symbol = args.get("symbol", "")
        sym = symbol_registry.resolve(symbol)
        if not sym:
            result = {"error": f"Symbol {symbol} not found"}
        else:
            from .data_quality import DataQualityEngine

            tick = await db_manager.get_latest_tick(sym.symbol_id)
            bars: dict[str, dict[str, Any]] = {}
            for tf in [TimeFrame.M1, TimeFrame.H1, TimeFrame.D1]:
                bar = await db_manager.get_latest_bar(sym.symbol_id, tf)
                if bar:
                    bars[tf.value] = _to_json(bar)
            indicator_docs = await db_manager.get_indicators(symbol_id=sym.symbol_id, limit=20)
            indicators = [_to_json(d) for d in indicator_docs]
            signal_docs = await db_manager.get_signals(symbol_id=sym.symbol_id, limit=5)
            signals = [_to_json(d) for d in signal_docs]
            engine = DataQualityEngine()
            quality_reports = [
                _to_json(await engine.check_tick_quality(sym.symbol_id, sym.name)),
                *[
                    _to_json(await engine.check_bar_quality(sym.symbol_id, sym.name, tf))
                    for tf in [TimeFrame.M1, TimeFrame.H1, TimeFrame.D1]
                ],
            ]
            result = {
                "symbol": sym.name,
                "symbol_id": sym.symbol_id,
                "timestamp_ms": int(time.time() * 1000),
                "tick": _to_json(tick) if tick else None,
                "bars": bars,
                "indicators": indicators,
                "signals": signals,
                "quality": quality_reports,
            }

    # ── Data Retrieval ───────────────────────────────────────────────────
    elif name == "get_price":
        sym = symbol_registry.resolve(args["symbol"])
        if sym:
            tick = await db_manager.get_latest_tick(sym.symbol_id)
            result = _to_json(tick) if tick else {"status": "no data"}
        else:
            result = {"error": "Symbol not found"}

    elif name == "get_ticks":
        sym = symbol_registry.resolve(args["symbol"])
        if sym:
            ticks = await db_manager.get_ticks(
                sym.symbol_id,
                from_ms=args.get("from_ms"),
                to_ms=args.get("to_ms"),
                limit=args.get("limit", 1000),
            )
            result = {"ticks": [_to_json(t) for t in ticks]}
        else:
            result = {"error": "Symbol not found"}

    elif name == "get_bars":
        sym = symbol_registry.resolve(args["symbol"])
        if sym:
            bars = await db_manager.get_bars(
                sym.symbol_id,
                TimeFrame(args["timeframe"]),
                from_ms=args.get("from_ms"),
                to_ms=args.get("to_ms"),
                limit=args.get("limit", 500),
            )
            result = {"bars": [_to_json(b) for b in bars]}
        else:
            result = {"error": "Symbol not found"}

    elif name == "get_orderbook":
        sym = symbol_registry.resolve(args["symbol"])
        if sym:
            ob = await db_manager.get_latest_orderbook(sym.symbol_id)
            result = _to_json(ob) if ob else {"status": "no data"}
        else:
            result = {"error": "Symbol not found"}

    # ── Analytics ────────────────────────────────────────────────────────
    elif name == "get_indicators":
        sym = symbol_registry.resolve(args["symbol"])
        tf = TimeFrame(args.get("timeframe", "1h"))
        if sym:
            bars = await db_manager.get_bars(sym.symbol_id, tf, limit=200)
            indicators = await analytics.compute_all_indicators(sym.symbol_id, sym.name, tf, bars)
            result = {"indicators": [_to_json(i) for i in indicators]}
        else:
            result = {"error": "Symbol not found"}

    elif name == "get_signals":
        sym = symbol_registry.resolve(args["symbol"])
        tf = TimeFrame(args.get("timeframe", "1h"))
        if sym:
            bars = await db_manager.get_bars(sym.symbol_id, tf, limit=200)
            signals = await analytics.generate_signals(sym.symbol_id, sym.name, tf, bars)
            result = {"signals": [_to_json(s) for s in signals]}
        else:
            result = {"error": "Symbol not found"}

    elif name == "get_market_structure":
        sym = symbol_registry.resolve(args["symbol"])
        tf = TimeFrame(args.get("timeframe", "1h"))
        if sym:
            bars = await db_manager.get_bars(sym.symbol_id, tf, limit=200)
            structure = await analytics.detect_market_structure(sym.symbol_id, sym.name, tf, bars)
            result = _to_json(structure) if structure else {"status": "insufficient data"}
        else:
            result = {"error": "Symbol not found"}

    # ── Data Quality ─────────────────────────────────────────────────────
    elif name == "check_quality":
        sym = symbol_registry.resolve(args.get("symbol", ""))
        tf_str = args.get("timeframe")
        if sym and tf_str:
            report = await _check_bar_quality(sym.symbol_id, sym.name, TimeFrame(tf_str))
            result = _to_json(report)
        elif sym:
            report = await _check_tick_quality(sym.symbol_id, sym.name)
            result = _to_json(report)
        else:
            reports = await _run_full_quality_check()
            result = {"reports": [_to_json(r) for r in reports]}

    elif name == "backfill_gaps":
        sym = symbol_registry.resolve(args["symbol"])
        tf = TimeFrame(args["timeframe"])
        if sym:
            req_doc = {
                "symbol_id": sym.symbol_id,
                "symbol_name": sym.name,
                "timeframe": tf.value,
                "from_ms": args["from_ms"],
                "to_ms": args["to_ms"],
                "status": "pending",
                "requested_at": datetime.now(UTC),
            }
            await db_manager.insert_backfill_request(req_doc)
            # Check if Data Service is alive
            hb = await db_manager.get_latest_service_heartbeat("data-service")
            ds_alive = False
            if hb:
                ts_str = hb.get("timestamp")
                if ts_str:
                    if isinstance(ts_str, str):
                        ts = datetime.fromisoformat(ts_str)
                    else:
                        ts = ts_str
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=UTC)
                    age = (datetime.now(UTC) - ts).total_seconds()
                    ds_alive = age < 60
            result = {
                "status": "queued",
                "symbol": sym.name,
                "timeframe": tf.value,
                "data_service_alive": ds_alive,
                "note": "Backfill request queued. The Data Service will process it when the feed is connected." if ds_alive else "Data Service is not running — start it with: python -m market_data_service data-service",
            }
        else:
            result = {"error": "Symbol not found"}

    # ── Config ───────────────────────────────────────────────────────────
    elif name == "set_symbol_config":
        cfg = SymbolConfig(**args)
        await symbol_registry.set_config(cfg)
        result = {"success": True, "config": _to_json(cfg)}

    elif name == "get_stats":
        stats = await db_manager.get_storage_stats()
        syms = await db_manager.list_symbols(status="active")
        result = {
            "active_symbols": len(syms),
            "db_connected": db_manager.is_connected,
            "uptime_seconds": time.time() - _start_time,
            **stats,
        }

    elif name == "get_feed_status":
        hb = await db_manager.get_latest_service_heartbeat("data-service")
        if hb:
            ts_str = hb.get("timestamp")
            if ts_str:
                if isinstance(ts_str, str):
                    ts = datetime.fromisoformat(ts_str)
                else:
                    ts = ts_str
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                age = (datetime.now(UTC) - ts).total_seconds()
                result = {
                    "data_service_running": age < 60,
                    "data_service_last_seen_seconds_ago": round(age, 1),
                    "feed_connected": hb.get("feed_connected", False),
                    "feed_source": hb.get("feed_source", "unknown"),
                    "active_symbols": hb.get("active_symbols", 0),
                    "subscribed_spots": hb.get("subscribed_spots", 0),
                    "subscribed_bars": hb.get("subscribed_bars", 0),
                    "subscribed_depth": hb.get("subscribed_depth", 0),
                    "db_connected": db_manager.is_connected,
                }
            else:
                result = {
                    "data_service_running": False,
                    "note": "No Data Service heartbeat found. Start it with: python -m market_data_service data-service",
                    "db_connected": db_manager.is_connected,
                    "active_symbols": len(symbol_registry.list_active()),
                }
        else:
            result = {
                "data_service_running": False,
                "note": "No Data Service heartbeat found. Start it with: python -m market_data_service data-service",
                "db_connected": db_manager.is_connected,
                "active_symbols": len(symbol_registry.list_active()),
            }

    else:
        result = {"error": f"Unknown tool: {name}"}

    return result


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
    try:
        result = await _execute_tool(name, arguments)
    except Exception as exc:
        logger.exception("Tool %s failed", name)
        result = {"error": str(exc)}
    return [TextContent(type="text", text=_json_dumps(result))]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _to_json(obj: Any) -> Any:
    """Serialize a Pydantic model to JSON-compatible dict, excluding None values."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", exclude_none=True)
    return obj


def _json_dumps(obj: Any) -> str:
    """Serialize to JSON with consistent ISO-8601 datetime formatting (always Z suffix)."""

    def _default(o: Any) -> Any:
        if isinstance(o, datetime):
            # Normalize naive datetimes to UTC; strip +00:00 in favour of Z
            if o.tzinfo is None:
                o = o.replace(tzinfo=UTC)
            return o.isoformat().replace("+00:00", "Z")
        # Fallback for other non-serializable types (ObjectId, Decimal128, etc.)
        return str(o)

    return json.dumps(obj, indent=2, default=_default)


async def _check_tick_quality(symbol_id: int, symbol_name: str) -> dict:
    latest = await db_manager.get_latest_tick(symbol_id)
    total = await db_manager.count_ticks(symbol_id)
    issues = []
    score = 1.0
    freshness = None
    last_ms = None
    if latest:
        last_ms = latest.timestamp_ms
        freshness = time.time() - (last_ms / 1000.0)
        if freshness > get_settings().stale_threshold_seconds:
            issues.append(f"Stale ticks: {freshness:.1f}s since last tick")
            score -= 0.3
    else:
        issues.append("No tick data found")
        score -= 0.5
    return dict(
        symbol_id=symbol_id, symbol_name=symbol_name,
        total_records=total, last_tick_ms=last_ms,
        freshness_seconds=freshness, score=max(score, 0.0), issues=issues,
    )


async def _check_bar_quality(symbol_id: int, symbol_name: str, timeframe: TimeFrame) -> dict:
    bars = await db_manager.get_bars(symbol_id, timeframe, limit=1000)
    total = await db_manager.count_bars(symbol_id)
    issues = []
    gap_count = 0
    anomaly_count = 0
    last_ms = bars[0].timestamp_ms if bars else None
    freshness = None
    score = 1.0
    if bars:
        freshness = time.time() - (last_ms / 1000.0) if last_ms else None
        tf_ms = timeframe.milliseconds
        for i in range(1, len(bars)):
            prev, curr = bars[i], bars[i - 1]
            expected = curr.timestamp_ms - tf_ms
            if prev.timestamp_ms != expected:
                gap_count += 1
            if prev.high < prev.low:
                anomaly_count += 1
                issues.append(f"High<Low at {prev.timestamp_ms}")
        if gap_count > 0:
            issues.append(f"{gap_count} gaps detected")
            score -= min(gap_count * 0.05, 0.4)
        if anomaly_count > 0:
            issues.append(f"{anomaly_count} OHLC anomalies")
            score -= min(anomaly_count * 0.05, 0.3)
    else:
        issues.append(f"No {timeframe.value} bar data found")
        score -= 0.5
    return dict(
        symbol_id=symbol_id, symbol_name=symbol_name, timeframe=timeframe,
        total_records=total, gap_count=gap_count, anomaly_count=anomaly_count,
        last_bar_ms=last_ms, freshness_seconds=freshness,
        score=max(score, 0.0), issues=issues,
    )


async def _run_full_quality_check() -> list[DataQualityReport]:
    symbols = await db_manager.list_symbols(status="active")
    reports = []
    for sym in symbols:
        reports.append(await _check_tick_quality(sym.symbol_id, sym.name))
        for tf in [TimeFrame.M1, TimeFrame.H1, TimeFrame.D1]:
            reports.append(await _check_bar_quality(sym.symbol_id, sym.name, tf))
    return reports


# ── Lifecycle ────────────────────────────────────────────────────────────────

_mcp_bg_tasks: list[asyncio.Task] = []


async def _reload_registry_loop() -> None:
    """Periodically reload symbol registry from DB to reflect Data Service changes."""
    while True:
        await asyncio.sleep(30)
        try:
            await symbol_registry.load_from_db()
            logger.debug("Symbol registry reloaded from DB")
        except Exception as exc:
            logger.warning("Registry reload failed: %s", exc)


async def _init_services() -> None:
    await db_manager.connect()
    await symbol_registry.load_from_db()
    logger.info("MCP Server initialized (lightweight mode)")


async def _shutdown_services() -> None:
    for task in _mcp_bg_tasks:
        task.cancel()
    await asyncio.gather(*_mcp_bg_tasks, return_exceptions=True)
    _mcp_bg_tasks.clear()
    await db_manager.disconnect()
    logger.info("MCP Server shut down")


@asynccontextmanager
async def app_lifespan(_server: Server) -> AsyncIterator[dict[str, Any]]:
    await _init_services()
    _mcp_bg_tasks.append(asyncio.create_task(_reload_registry_loop()))
    yield {}
    await _shutdown_services()


server.lifespan = app_lifespan  # type: ignore[assignment]


# ── Entry points ─────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=getattr(logging, get_settings().log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(_run())


async def _run() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    main()
