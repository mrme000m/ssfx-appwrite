"""OpenPI-compatible REST API server for Market Data Service.

This module provides a REST/HTTP API for configuring and managing the
Market Data Service. All configuration is persisted to the configured
database (SQLite by default, MongoDB optional).

Usage:
    python -m market_data_service api-server
    # or directly
    cd src && python api_server.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api_models import (
    AuthBrokerRequest,
    AuthBrokerStartUrlResponse,
    AuthDirectRequest,
    AuthStatusResponse,
    CachedSymbolResponse,
    CacheRefreshResponse,
    DatabaseResetRequest,
    DatabaseResetResponse,
    FeedConnectRequest,
    FeedSubscribeRequest,
    GapRepairRequest,
    GapRepairResponse,
    GapReport,
    HealthResponse,
    OperationResponse,
    ServiceConfig,
    StatsResponse,
    SymbolConfigUpdate,
    SymbolCreate,
    SymbolResponse,
    SymbolUpdate,
)
from .auth_middleware import get_current_user
from .config import get_settings
from .database import db_manager
from .models import (
    FeedSource,
    OHLCVBar,
    SymbolConfig,
    SymbolInfo,
    SymbolStatus,
    TickData,
    TimeFrame,
)
from .symbol_registry import SymbolRegistry

logger = logging.getLogger(__name__)

# Gold Quantitative Analysis (optional — gracefully degrades if not available)
try:
    from .gold_quant_engine import GoldQuantEngine
    from .gold_quant_engine.context_builder import AgentContextBuilder
    _GOLD_ENGINE: GoldQuantEngine | None = GoldQuantEngine()
except Exception as _gold_exc:
    logger.warning("GoldQuantEngine not available: %s", _gold_exc)
    _GOLD_ENGINE = None

# ── Global State ───────────────────────────────────────────────────────────────

_start_time = time.time()
_registry = SymbolRegistry()
_config_cache: ServiceConfig | None = None
_config_lock = asyncio.Lock()

# Data Service control API base URL (runs alongside the Data Service on port 9000)
DS_BASE_URL = f"http://127.0.0.1:{get_settings().ds_control_port}"


def _update_env(key: str, value: str) -> None:
    """Update a single key in the workspace .env file."""
    env_file = Path(__file__).resolve().parent.parent / "config" / "dataservice.env"
    lines = env_file.read_text().splitlines() if env_file.exists() else []
    updated = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            updated = True
            break
    if not updated:
        lines.append(f"{key}={value}")
    env_file.write_text("\n".join(lines) + "\n")


async def _ds_get(path: str) -> dict[str, Any]:
    """Proxy GET to the Data Service control API."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{DS_BASE_URL}{path}")
        resp.raise_for_status()
        return resp.json()


async def _ds_post(path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Proxy POST to the Data Service control API."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{DS_BASE_URL}{path}", json=body or {})
        resp.raise_for_status()
        return resp.json()


async def _broker_get(broker_url: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Proxy GET to the cTrader auth broker."""
    base = broker_url.rstrip("/")
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(f"{base}{path}", params=params)
        resp.raise_for_status()
        return resp.json()


async def _get_feed_stats() -> dict[str, Any]:
    """Fetch feed statistics from the Data Service control API."""
    try:
        stats = await _ds_get("/feed/status")
        return {
            "connected": stats.get("connected", False),
            "feed_source": stats.get("feed_source", "unknown"),
            "subscribed_spots": stats.get("subscribed_spots", 0),
            "subscribed_bars": stats.get("subscribed_bars", 0),
            "subscribed_depth": stats.get("subscribed_depth", 0),
        }
    except httpx.HTTPError:
        return {
            "connected": False,
            "feed_source": "unknown",
            "subscribed_spots": 0,
            "subscribed_bars": 0,
            "subscribed_depth": 0,
        }


# ── Config Persistence ─────────────────────────────────────────────────────────

async def load_service_config() -> ServiceConfig:
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    doc = await db_manager.get_service_config()
    if doc:
        _config_cache = ServiceConfig(**doc)
    else:
        _config_cache = ServiceConfig()
        await db_manager.update_service_config(_config_cache.model_dump(mode="json"))
    return _config_cache


async def save_service_config(config: ServiceConfig) -> ServiceConfig:
    global _config_cache
    async with _config_lock:
        config.updated_at = datetime.now(UTC)
        await db_manager.update_service_config(config.model_dump(mode="json"))
        _config_cache = config
    return config


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db_manager.connect()
    await _registry.load_from_db()
    await load_service_config()
    logger.info("OpenPI API server started")
    yield
    try:
        await db_manager.disconnect()
    except Exception:
        pass
    logger.info("OpenPI API server stopped")


# ── FastAPI App ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Market Data Service API",
    description="OpenPI-compatible REST API for Market Data Service management",
    version="0.2.0",
    lifespan=lifespan,
)

_CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "https://app.mrme.tech,https://ds.mrme.tech,https://dataservice-site.appwrite.host,http://localhost:9002,http://127.0.0.1:9002",
    ).split(",")
    if origin.strip()
]
if os.getenv("CORS_ALLOW_ALL", "").lower() in ("1", "true"):
    _CORS_ORIGINS = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Root-path rewrite for Cloudflare tunnel compatibility ───────────────────────
# The public API is canonically under /api/v1, but the tunnel exposes the service
# at dataservice.mrme.tech with no path prefix. Rewrite root-level public requests
# so they hit the /api/v1 router without duplicating every route.
@app.middleware("http")
async def rewrite_root_api_paths(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/v1") or path.startswith("/admin"):
        return await call_next(request)
    if path in ("/docs", "/openapi.json", "/redoc"):
        return await call_next(request)
    request.scope["path"] = f"/api/v1{path}"
    return await call_next(request)


# ── Routers ────────────────────────────────────────────────────────────────────

public_router = APIRouter(prefix="/api/v1")
admin_router = APIRouter(prefix="/api/v1", dependencies=[Depends(get_current_user)])


# ── Static Site ────────────────────────────────────────────────────────────────
SITE_DIR = (Path(__file__).parent.parent / "site").resolve()

# Mount the public config/admin site at the root (skip if the directory is missing)
# NOTE: this mount is intentionally deferred until after API routers are included.
SITE_DIR = (Path(__file__).parent.parent / "site").resolve()


@app.middleware("http")
async def add_request_id(request, call_next):
    import uuid
    request_id = str(uuid.uuid4())[:8]
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.middleware("http")
async def cors_on_errors(request, call_next):
    """Ensure CORS headers are present even on unhandled exceptions."""
    origin = request.headers.get("Origin", "*")
    try:
        response = await call_next(request)
    except Exception as exc:
        logger.exception("Unhandled API error: %s", exc)
        response = JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error", "error": str(exc)},
        )
    response.headers.setdefault("Access-Control-Allow-Origin", origin)
    response.headers.setdefault("Access-Control-Allow-Credentials", "true")
    return response


# ── Health & Stats ─────────────────────────────────────────────────────────────

@public_router.get("/health", response_model=HealthResponse, tags=["Health"])
async def get_health():
    feed_stats = await _get_feed_stats()
    syms = await db_manager.list_symbols(status="active")
    return HealthResponse(
        status="healthy" if db_manager.is_connected else "degraded",
        db_connected=db_manager.is_connected,
        feed_connected=feed_stats.get("connected", False),
        uptime_seconds=time.time() - _start_time,
        active_symbols=len(syms),
        subscribed_spots=feed_stats.get("subscribed_spots", 0),
        subscribed_bars=feed_stats.get("subscribed_bars", 0),
        subscribed_depth=feed_stats.get("subscribed_depth", 0),
        feed_source=feed_stats.get("feed_source", "unknown"),
    )


@public_router.get("/stats", response_model=StatsResponse, tags=["Health"])
async def get_stats():
    feed_stats = await _get_feed_stats()
    syms = await db_manager.list_symbols(status="active")
    stats = await db_manager.get_storage_stats()
    queued = await db_manager.count_pending_backfills()
    return StatsResponse(
        active_symbols=len(syms),
        db_connected=db_manager.is_connected,
        uptime_seconds=time.time() - _start_time,
        feed_connected=feed_stats.get("connected", False),
        feed_source=feed_stats.get("feed_source", "unknown"),
        tick_count=stats.get("ticks", 0),
        bar_count=stats.get("bars", 0),
        orderbook_count=stats.get("orderbook", 0),
        signal_count=stats.get("signals", 0),
        indicator_count=stats.get("indicators", 0),
        queued_backfills=queued,
    )


# ── Configuration ─────────────────────────────────────────────────────────────

@admin_router.get("/config", response_model=ServiceConfig, tags=["Configuration"])
async def get_config():
    """Get current service configuration."""
    return await load_service_config()


@admin_router.put("/config", response_model=ServiceConfig, tags=["Configuration"])
async def update_config(config: ServiceConfig):
    """Update service configuration. Changes are persisted to the database."""
    for tf in config.default_bar_timeframes:
        try:
            TimeFrame(tf)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid timeframe: {tf}")
    if config.feed_source not in [s for s in FeedSource]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid feed source: {config.feed_source}")
    config.updated_by = "api"
    saved = await save_service_config(config)
    _apply_runtime_config(config)
    return saved


@admin_router.post("/config/reset", response_model=ServiceConfig, tags=["Configuration"])
async def reset_config():
    """Reset service configuration to defaults."""
    global _config_cache
    _config_cache = None
    return await save_service_config(ServiceConfig(updated_by="api_reset"))


# ── Symbols ───────────────────────────────────────────────────────────────────

@public_router.get("/symbols", response_model=list[SymbolResponse], tags=["Symbols"])
async def list_symbols(status_filter: SymbolStatus | None = None):
    symbols = _registry.list_all(status_filter)
    return await _build_symbol_responses(symbols)


@public_router.get("/symbols/ctrader", tags=["Symbols"])
async def list_ctrader_symbols():
    """Return cTrader's available symbol list (id, name, description, enabled).

    Also caches the result to the local database for offline lookup.
    """
    try:
        result = await _ds_get("/symbols/available")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Data Service unreachable: {e}")
    # Cache to local DB
    symbols = result.get("symbols", [])
    if symbols:
        try:
            count = await db_manager.cache_symbols(symbols, source="ctrader")
            logger.info("Cached %d symbols from cTrader", count)
        except Exception as exc:
            logger.warning("Failed to cache symbols: %s", exc)
    return result


@public_router.get("/symbols/resolve", tags=["Symbols"])
async def resolve_symbol_name(name: str):
    """Resolve a symbol name to its cTrader ID and metadata.

    Returns 404 if the symbol is not found in cTrader's symbol list.
    """
    try:
        result = await _ds_get(f"/symbols/resolve?name={name}")
        return result
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(status_code=404, detail=f"Symbol '{name}' not found in cTrader")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Data Service error: {e}")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Data Service unreachable: {e}")


@public_router.get("/symbols/cached", response_model=list[CachedSymbolResponse], tags=["Symbols"])
async def list_cached_symbols(
    source: str | None = Query(default=None),
    search: str | None = Query(default=None),
):
    """Return locally cached symbol list.

    Useful when the Data Service is offline but you still want to browse
    available instruments.
    """
    docs = await db_manager.get_cached_symbols(source=source, search=search)
    return [CachedSymbolResponse(**d) for d in docs]


@public_router.get("/symbols/cached/{symbol_id}", response_model=CachedSymbolResponse, tags=["Symbols"])
async def get_cached_symbol(symbol_id: int):
    """Return a single cached symbol by ID."""
    doc = await db_manager.get_cached_symbol(symbol_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Cached symbol {symbol_id} not found")
    return CachedSymbolResponse(**doc)


@admin_router.post("/symbols/cache/refresh", response_model=CacheRefreshResponse, tags=["Symbols"])
async def refresh_symbol_cache():
    """Force a refresh of the local symbol cache from the currently connected feed.

    Requires the Data Service to be connected.
    """
    try:
        result = await _ds_get("/symbols/available")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Data Service unreachable: {e}")
    symbols = result.get("symbols", [])
    if not symbols:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No symbols returned from feed")
    try:
        count = await db_manager.cache_symbols(symbols, source="ctrader")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Cache write failed: {exc}")
    return CacheRefreshResponse(
        success=True,
        source="ctrader",
        cached_count=count,
        message=f"Cached {count} symbols from cTrader",
    )


@admin_router.post("/symbols", response_model=SymbolResponse, status_code=status.HTTP_201_CREATED, tags=["Symbols"])
async def create_symbol(data: SymbolCreate):
    existing = _registry.get(data.symbol_id)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Symbol ID {data.symbol_id} already exists")

    # Validate against cTrader if feed is connected and name is provided
    try:
        ds_stats = await _ds_get("/feed/status")
        if ds_stats.get("connected") and data.name:
            resolved = await _ds_get(f"/symbols/resolve?name={data.name}")
            if resolved and resolved.get("symbol_id") != data.symbol_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Symbol name '{data.name}' maps to cTrader ID {resolved['symbol_id']}, but you provided {data.symbol_id}. Use the correct ID.",
                )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Symbol '{data.name}' is not known to cTrader. Please check the symbol name or add it manually with a custom ID.",
            )
    except httpx.HTTPError:
        pass  # Data Service not available, skip validation

    info = SymbolInfo(
        symbol_id=data.symbol_id,
        name=data.name,
        digits=data.digits,
        status=data.status,
        description=data.description,
        asset_class=data.asset_class,
        lot_size=data.lot_size,
        exchange=data.exchange,
        pip_position=data.pip_position,
        tick_size=data.tick_size,
        min_volume=data.min_volume,
        max_volume=data.max_volume,
        volume_step=data.volume_step,
        measurement_units=data.measurement_units,
    )
    await _registry.add(info)
    service_config = await load_service_config()
    cfg = SymbolConfig(symbol_id=data.symbol_id, name=data.name, enabled=True, feed_sources=[service_config.feed_source])
    await _registry.set_config(cfg)
    return await _build_symbol_response(info)


@public_router.get("/symbols/{symbol_id}", response_model=SymbolResponse, tags=["Symbols"])
async def get_symbol(symbol_id: int):
    info = _registry.get(symbol_id)
    if not info:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol_id} not found")
    return await _build_symbol_response(info)


@admin_router.put("/symbols/{symbol_id}", response_model=SymbolResponse, tags=["Symbols"])
async def update_symbol(symbol_id: int, data: SymbolUpdate):
    existing = _registry.get(symbol_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol_id} not found")
    update_fields = data.model_dump(exclude_none=True)
    if update_fields:
        await _registry.update(symbol_id, **update_fields)
    # Refresh from DB so registry has latest state
    updated_info = await db_manager.get_symbol(symbol_id)
    if updated_info:
        _registry._symbols[symbol_id] = updated_info
        _registry._by_name[updated_info.name] = symbol_id
    info = _registry.get(symbol_id)
    return await _build_symbol_response(info)


@admin_router.delete("/symbols/{symbol_id}", response_model=OperationResponse, tags=["Symbols"])
async def delete_symbol(symbol_id: int):
    existing = _registry.get(symbol_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol_id} not found")
    ok = await _registry.remove(symbol_id)
    await db_manager.delete_symbol_config(symbol_id)
    return OperationResponse(success=ok, message=f"Symbol {symbol_id} ({existing.name}) removed")


@admin_router.put("/symbols/{symbol_id}/config", response_model=SymbolConfig, tags=["Symbols"])
async def update_symbol_config(symbol_id: int, data: SymbolConfigUpdate):
    info = _registry.get(symbol_id)
    if not info:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol_id} not found")
    for tf in data.bar_timeframes:
        try:
            TimeFrame(tf)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid timeframe: {tf}")
    feed_sources = [FeedSource(fs) for fs in data.feed_sources] if data.feed_sources else []
    cfg = SymbolConfig(
        symbol_id=symbol_id, name=info.name, enabled=data.enabled,
        collect_ticks=data.collect_ticks, collect_bars=data.collect_bars,
        collect_depth=data.collect_depth,
        bar_timeframes=[TimeFrame(tf) for tf in data.bar_timeframes],
        feed_sources=feed_sources,
    )
    await _registry.set_config(cfg)
    try:
        ds_stats = await _ds_get("/feed/status")
        if ds_stats.get("connected"):
            await _apply_symbol_subscriptions(symbol_id, data)
    except httpx.HTTPError:
        pass
    return cfg


# ── Feed Control ───────────────────────────────────────────────────────────────

@public_router.get("/feed/status", tags=["Feed"])
async def get_feed_status():
    try:
        stats = await _ds_get("/feed/status")
    except httpx.HTTPError:
        stats = {"connected": False, "feed_source": "unknown", "subscribed_spots": 0, "subscribed_bars": 0, "subscribed_depth": 0}
    hb = await db_manager.get_latest_service_heartbeat("data-service")
    ds_alive = False
    last_hb = None
    if hb:
        ts_str = hb.get("timestamp")
        if ts_str:
            if isinstance(ts_str, str):
                ts = datetime.fromisoformat(ts_str)
            else:
                ts = ts_str
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            last_hb = (datetime.now(UTC) - ts).total_seconds()
            ds_alive = last_hb < 60
    return {
        "connected": stats.get("connected", False),
        "feed_source": stats.get("feed_source", "unknown"),
        "subscribed_spots": stats.get("subscribed_spots", 0),
        "subscribed_bars": stats.get("subscribed_bars", 0),
        "subscribed_depth": stats.get("subscribed_depth", 0),
        "data_service_alive": ds_alive,
        "last_heartbeat_seconds_ago": round(last_hb, 1) if last_hb is not None else None,
    }


@admin_router.post("/feed/connect", response_model=OperationResponse, tags=["Feed"])
async def connect_feed(data: FeedConnectRequest | None = None):
    body = {"symbols": data.symbols, "reconnect": data.reconnect} if data else {}
    try:
        result = await _ds_post("/feed/connect", body)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Data Service unreachable: {e}")
    return OperationResponse(success=result.get("success", False), message=result.get("message", ""))


@admin_router.post("/feed/disconnect", response_model=OperationResponse, tags=["Feed"])
async def disconnect_feed():
    try:
        result = await _ds_post("/feed/disconnect", {})
    except httpx.HTTPError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Data Service unreachable: {e}")
    return OperationResponse(success=result.get("success", False), message=result.get("message", ""))


@admin_router.post("/feed/subscribe", response_model=OperationResponse, tags=["Feed"])
async def subscribe_symbols(data: FeedSubscribeRequest):
    body = {
        "symbols": data.symbols,
        "subscribe_ticks": data.subscribe_ticks,
        "subscribe_bars": data.subscribe_bars,
        "subscribe_depth": data.subscribe_depth,
        "bar_timeframes": data.bar_timeframes,
    }
    try:
        result = await _ds_post("/feed/subscribe", body)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Data Service unreachable: {e}")
    if not result.get("success"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result.get("message", ""))
    return OperationResponse(success=True, message=result.get("message", ""), data=result.get("data"))


@admin_router.post("/feed/unsubscribe", response_model=OperationResponse, tags=["Feed"])
async def unsubscribe_symbols(symbols: list[int] = Body(..., embed=True)):
    try:
        result = await _ds_post("/feed/unsubscribe", {"symbols": symbols})
    except httpx.HTTPError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Data Service unreachable: {e}")
    return OperationResponse(success=result.get("success", False), message=result.get("message", ""))


# ── Data Retrieval ─────────────────────────────────────────────────────────────

@public_router.get("/data/{symbol}", tags=["Data"])
async def get_price(symbol: str):
    sym = _registry.resolve(symbol)
    if not sym:
        raise HTTPException(status_code=404, detail=f"Symbol not found: {symbol}")
    tick = await db_manager.get_latest_tick(sym.symbol_id)
    # Get previous tick for change calculation
    prev_tick = None
    if tick:
        prev_ticks = await db_manager.get_ticks(sym.symbol_id, limit=2)
        if len(prev_ticks) > 1:
            prev_tick = prev_ticks[1]  # second most recent
    result = {
        "symbol": sym.name,
        "symbol_id": sym.symbol_id,
        "status": "no data" if not tick else "ok",
        "digits": sym.digits,
    }
    if tick:
        bid_change = None
        bid_change_pct = None
        if prev_tick and prev_tick.bid != 0:
            bid_change = tick.bid - prev_tick.bid
            bid_change_pct = (bid_change / prev_tick.bid) * 100
        result.update({
            "bid": tick.bid,
            "ask": tick.ask,
            "spread": tick.spread,
            "bid_change": bid_change,
            "bid_change_pct": bid_change_pct,
            "timestamp_ms": tick.timestamp_ms,
            "bid_volume": tick.bid_volume,
            "ask_volume": tick.ask_volume,
        })
    return result


@public_router.get("/data/{symbol}/bars", tags=["Data"])
async def get_bars(
    symbol: str,
    timeframe: str = Query(default="1h"),
    from_ms: int | None = Query(default=None),
    to_ms: int | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=10000),
):
    sym = _registry.resolve(symbol)
    if not sym:
        raise HTTPException(status_code=404, detail=f"Symbol not found: {symbol}")
    try:
        tf = TimeFrame(timeframe)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe: {timeframe}")
    bars = await db_manager.get_bars(sym.symbol_id, tf, from_ms, to_ms, limit)
    return {"symbol": sym.name, "timeframe": tf.value, "count": len(bars), "bars": [_bar_to_dict(b) for b in bars]}


@public_router.get("/data/{symbol}/ticks", tags=["Data"])
async def get_ticks(
    symbol: str,
    from_ms: int | None = Query(default=None),
    to_ms: int | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=50000),
):
    sym = _registry.resolve(symbol)
    if not sym:
        raise HTTPException(status_code=404, detail=f"Symbol not found: {symbol}")
    ticks = await db_manager.get_ticks(sym.symbol_id, from_ms, to_ms, limit)
    return {"symbol": sym.name, "count": len(ticks), "ticks": [_tick_to_dict(t) for t in ticks]}


@public_router.get("/data/{symbol}/depth", tags=["Data"])
async def get_depth(symbol: str):
    sym = _registry.resolve(symbol)
    if not sym:
        raise HTTPException(status_code=404, detail=f"Symbol not found: {symbol}")
    ob = await db_manager.get_latest_orderbook(sym.symbol_id)
    if ob:
        return {
            "symbol": sym.name,
            "bid_levels": len(ob.bids),
            "ask_levels": len(ob.asks),
            "bid_volume": ob.bid_depth,
            "ask_volume": ob.ask_depth,
            "spread": ob.spread,
            "depth_imbalance": ob.depth_imbalance,
            "timestamp_ms": ob.timestamp_ms,
            "digits": ob.digits,
            "bids": [{"price": b.price, "volume": b.volume, "level": b.level} for b in ob.bids],
            "asks": [{"price": a.price, "volume": a.volume, "level": a.level} for a in ob.asks],
        }
    return {"symbol": sym.name, "status": "no depth data"}


# ── Indicators ─────────────────────────────────────────────────────────────────

@public_router.get("/indicators/{symbol}", tags=["Indicators"])
async def get_symbol_indicators(
    symbol: str,
    timeframe: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
):
    """Get latest technical indicators for a symbol.

    Optionally filter by timeframe (e.g. 1h, 4h, 1d).
    """
    sym = _registry.resolve(symbol)
    if not sym:
        raise HTTPException(status_code=404, detail=f"Symbol not found: {symbol}")
    tf = TimeFrame(timeframe) if timeframe else None
    docs = await db_manager.get_indicators(symbol_id=sym.symbol_id, indicator_type=None, limit=limit)
    return {
        "symbol": sym.name,
        "symbol_id": sym.symbol_id,
        "count": len(docs),
        "indicators": [
            {
                "indicator": d.indicator,
                "value": d.value,
                "symbol_name": d.symbol_name or sym.name,
                "timeframe": d.timeframe.value if hasattr(d.timeframe, "value") else str(d.timeframe),
                "period": d.period,
                "timestamp_ms": d.timestamp_ms,
            }
            for d in docs
            if tf is None or (d.timeframe.value if hasattr(d.timeframe, "value") else str(d.timeframe)) == tf.value
        ],
    }


@public_router.get("/signals", tags=["Signals"])
async def list_signals(
    symbol: str | None = Query(default=None),
    direction: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Get latest trading signals.

    Optionally filter by symbol or direction (BUY, SELL, NEUTRAL).
    """
    sym_id = None
    if symbol:
        sym = _registry.resolve(symbol)
        if not sym:
            raise HTTPException(status_code=404, detail=f"Symbol not found: {symbol}")
        sym_id = sym.symbol_id
    docs = await db_manager.get_signals(symbol_id=sym_id, signal_type=direction.upper() if direction else None, limit=limit)
    return {
        "count": len(docs),
        "signals": [
            {
                "symbol_name": d.symbol_name or "",
                "direction": d.direction,
                "strength": d.strength,
                "confidence": d.confidence,
                "indicators": d.indicators,
                "timeframe": d.timeframe.value if hasattr(d.timeframe, "value") else str(d.timeframe),
                "timestamp_ms": d.timestamp_ms,
            }
            for d in docs
        ],
    }


@public_router.get("/signals/{symbol}", tags=["Signals"])
async def get_symbol_signals(
    symbol: str,
    direction: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
):
    """Get latest trading signals for a specific symbol."""
    sym = _registry.resolve(symbol)
    if not sym:
        raise HTTPException(status_code=404, detail=f"Symbol not found: {symbol}")
    docs = await db_manager.get_signals(symbol_id=sym.symbol_id, signal_type=direction.upper() if direction else None, limit=limit)
    return {
        "symbol": sym.name,
        "symbol_id": sym.symbol_id,
        "count": len(docs),
        "signals": [
            {
                "direction": d.direction,
                "strength": d.strength,
                "confidence": d.confidence,
                "indicators": d.indicators,
                "timeframe": d.timeframe.value if hasattr(d.timeframe, "value") else str(d.timeframe),
                "timestamp_ms": d.timestamp_ms,
            }
            for d in docs
        ],
    }


# ── Quality & Gaps ─────────────────────────────────────────────────────────────

@public_router.get("/quality", tags=["Quality"])
async def run_quality_check():
    from .data_quality import DataQualityEngine
    engine = DataQualityEngine()
    reports = await engine.run_full_check()
    unique_symbols = set(getattr(r, 'symbol_id', None) for r in reports)
    unique_symbols.discard(None)
    return {"symbols_checked": len(unique_symbols), "reports": [_quality_report_to_dict(r) for r in reports]}


@public_router.get("/quality/{symbol}", tags=["Quality"])
async def check_symbol_quality(symbol: str, timeframe: str | None = Query(default=None)):
    sym = _registry.resolve(symbol)
    if not sym:
        raise HTTPException(status_code=404, detail=f"Symbol not found: {symbol}")
    from .data_quality import DataQualityEngine
    engine = DataQualityEngine()
    reports = []
    if timeframe:
        try:
            tf = TimeFrame(timeframe)
            report = await engine.check_bar_quality(sym.symbol_id, sym.name, tf)
            reports.append(_quality_report_to_dict(report))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid timeframe: {timeframe}")
    else:
        reports.append(_quality_report_to_dict(await engine.check_tick_quality(sym.symbol_id, sym.name)))
        for tf in [TimeFrame.M1, TimeFrame.H1, TimeFrame.D1]:
            reports.append(_quality_report_to_dict(await engine.check_bar_quality(sym.symbol_id, sym.name, tf)))
    return {"symbol": sym.name, "symbol_id": sym.symbol_id, "reports": reports}


@public_router.get("/gaps", response_model=list[GapReport], tags=["Gaps"])
async def detect_gaps(
    symbol_ids: list[int] | None = Query(default=None),
    timeframes: list[str] = Query(default=None),
    from_ms: int | None = Query(default=None),
    to_ms: int | None = Query(default=None),
):
    now_ms = int(time.time() * 1000)
    from_ms = from_ms or (now_ms - 7 * 24 * 3600 * 1000)
    to_ms = to_ms or now_ms
    tf_list = [TimeFrame(tf) for tf in (timeframes or [TimeFrame.H1.value])]
    symbols = await db_manager.list_symbols(status="active")
    if symbol_ids:
        symbols = [s for s in symbols if s.symbol_id in symbol_ids]
    gap_reports: list[GapReport] = []
    for sym in symbols:
        for tf in tf_list:
            bars = await db_manager.get_bars(sym.symbol_id, tf, from_ms=from_ms, to_ms=to_ms, limit=10000)
            if len(bars) < 2:
                continue
            gaps = _find_gaps(bars, tf.milliseconds)
            for gs, ge in gaps:
                gap_reports.append(GapReport(symbol_id=sym.symbol_id, symbol_name=sym.name, timeframe=tf.value, expected_from_ms=gs, expected_to_ms=ge, gap_duration_ms=ge - gs))
    return gap_reports


@admin_router.post("/gaps/repair", response_model=list[GapRepairResponse], tags=["Gaps"])
async def repair_gaps(data: GapRepairRequest):
    try:
        ds_stats = await _ds_get("/feed/status")
        if not ds_stats.get("connected"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Feed not connected. Use POST /feed/connect first.")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Data Service unreachable: {e}")
    now_ms = int(time.time() * 1000)
    from_ms = data.from_ms or (now_ms - 7 * 24 * 3600 * 1000)
    to_ms = data.to_ms or now_ms
    symbols = await db_manager.list_symbols(status="active")
    if data.symbol_ids:
        symbols = [s for s in symbols if s.symbol_id in data.symbol_ids]
    results: list[GapRepairResponse] = []
    for sym in symbols:
        for tf_str in data.timeframes:
            try:
                tf = TimeFrame(tf_str)
            except ValueError:
                continue
            bars = await db_manager.get_bars(sym.symbol_id, tf, from_ms, to_ms, limit=10000)
            gaps = _find_gaps(bars, tf.milliseconds) if bars else []
            if not gaps:
                results.append(GapRepairResponse(symbol_id=sym.symbol_id, symbol_name=sym.name, timeframe=tf.value, gaps_detected=0, bars_filled=0, status="no_gaps"))
                continue
            if data.dry_run:
                results.append(GapRepairResponse(symbol_id=sym.symbol_id, symbol_name=sym.name, timeframe=tf.value, gaps_detected=len(gaps), bars_filled=0, status="dry_run", message=f"Would repair {len(gaps)} gaps"))
                continue
            bars_filled = 0
            for gs, ge in gaps:
                try:
                    result = await _ds_post("/gaps/fetch", {
                        "symbol_id": sym.symbol_id,
                        "timeframe": tf.value,
                        "from_ms": gs,
                        "to_ms": ge,
                    })
                    for bar_dict in result.get("bars", []):
                        ts = bar_dict.get("timestamp_ms", 0)
                        exists = await db_manager.bar_exists(sym.symbol_id, tf, ts)
                        if not exists:
                            ohlcv = OHLCVBar(
                                symbol_id=sym.symbol_id,
                                symbol_name=sym.name,
                                timeframe=tf,
                                open=bar_dict["open"],
                                high=bar_dict["high"],
                                low=bar_dict["low"],
                                close=bar_dict["close"],
                                volume=bar_dict["volume"],
                                timestamp_ms=ts,
                                source=FeedSource.BACKFILL,
                            )
                            await db_manager.store_bars([ohlcv])
                            bars_filled += 1
                except httpx.HTTPError:
                    pass
            results.append(GapRepairResponse(symbol_id=sym.symbol_id, symbol_name=sym.name, timeframe=tf.value, gaps_detected=len(gaps), bars_filled=bars_filled, status="completed" if bars_filled > 0 else "skipped"))
    return results


# ── Database Operations ────────────────────────────────────────────────────────

@admin_router.post("/database/reset", response_model=DatabaseResetResponse, tags=["Database"])
async def reset_database(data: DatabaseResetRequest):
    if not data.confirm:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Must set confirm=true to execute reset")
    removed = 0
    collections_to_clear = data.collections or ["ticks", "bars", "orderbook", "indicators", "signals", "market_structure", "data_quality", "backfill_requests"]
    preserved_symbols = set()
    if data.preserve_symbols:
        syms = await db_manager.list_symbols()
        preserved_symbols = {s.symbol_id for s in syms}
    for coll_name in collections_to_clear:
        try:
            before = datetime.now(UTC).timestamp() * 1000 + 86400000  # far future
            deleted = await db_manager.delete_old_records(coll_name, int(before), preserved_symbols if coll_name in ("ticks", "bars") else None)
            removed += deleted
            logger.info("Cleared collection %s: %d docs", coll_name, deleted)
        except Exception as exc:
            logger.warning("Failed to clear %s: %s", coll_name, exc)
    await db_manager.update_service_config({})
    return DatabaseResetResponse(collections_cleared=collections_to_clear, symbols_preserved=data.preserve_symbols, configs_preserved=data.preserve_configs, documents_removed=removed, status="success")


@public_router.get("/symbols/{symbol_id}/config", response_model=SymbolConfig, tags=["Symbols"])
async def get_symbol_config(symbol_id: int):
    """Get per-symbol configuration (enabled, collection settings, timeframes)."""
    info = _registry.get(symbol_id)
    if not info:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol_id} not found")
    cfg = _registry.get_config(symbol_id)
    if cfg is None:
        cfg = SymbolConfig(symbol_id=symbol_id, name=info.name)
    return cfg


# ── Database Operations ────────────────────────────────────────────────────────────

@admin_router.post("/database/reindex", response_model=OperationResponse, tags=["Database"])
async def reindex_database():
    await db_manager.ensure_indexes()
    return OperationResponse(success=True, message="Database indexes recreated")


@admin_router.post("/database/compact", response_model=OperationResponse, tags=["Database"])
async def compact_database():
    try:
        for coll_name in ["ticks", "bars", "orderbook"]:
            await db_manager.compact_collection(coll_name)
        return OperationResponse(success=True, message="Database compaction completed")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Compaction failed: {exc}")


# ── Version ───────────────────────────────────────────────────────────────────

@public_router.get("/version", tags=["Info"])
async def get_version():
    return {"version": "0.1.0", "service": "market-data-service", "api_version": "1.0.0", "openpi_compatible": True}


# ── Authentication ─────────────────────────────────────────────────────────────

@public_router.get("/auth/status", response_model=AuthStatusResponse, tags=["Authentication"])
async def get_auth_status():
    """Return current cTrader authentication status."""
    settings = get_settings()
    feed_stats = await _get_feed_stats()

    # Load token expiry from tokens.json if available
    token_expires_at = None
    token_days_remaining = None
    from .config import BASE_DIR
    token_store = BASE_DIR / ".tmp_rovodev_ctrader_tokens.json"
    if token_store.exists():
        try:
            import json
            data = json.loads(token_store.read_text())
            token_expires_at = data.get("expires_at")
            if token_expires_at:
                token_days_remaining = round((token_expires_at - time.time()) / 86400, 2)
        except Exception:
            pass

    # Check ~/.ctrader-mcp/tokens.json for alternate token
    ctrader_mcp_tokens = Path.home() / ".ctrader-mcp" / "tokens.json"
    if ctrader_mcp_tokens.exists() and not token_days_remaining:
        try:
            import json
            data = json.loads(ctrader_mcp_tokens.read_text())
            token_expires_at = data.get("expires_at")
            if token_expires_at:
                token_days_remaining = round((token_expires_at - time.time()) / 86400, 2)
        except Exception:
            pass

    broker_url = settings.ctrader_auth_broker_url or ""
    grant_id = settings.ctrader_auth_grant_id
    selected_account_id: int | None = None
    account_balance: float | None = None
    account_balance_digits: int | None = None

    # Ask the broker for the selected account id (ctid) if we are in broker mode
    if broker_url:
        try:
            grant_info = await _broker_get(
                broker_url, "/internal/grant/latest", params={"user_id": "cds_admin"}
            )
            raw_selected = grant_info.get("selected_account_id")
            if raw_selected is not None:
                try:
                    selected_account_id = int(raw_selected)
                except (ValueError, TypeError):
                    selected_account_id = None
        except Exception as exc:
            logger.warning("Failed to fetch broker grant info: %s", exc)

    # Fetch live balance for the selected/account id via the broker
    balance_ctid = settings.ctrader_account_id or selected_account_id
    if broker_url and grant_id and balance_ctid:
        try:
            balance_info = await _broker_get(
                broker_url,
                "/internal/ctrader/account-balance",
                params={"grant_id": grant_id, "ctid_trader_account_id": balance_ctid},
            )
            raw_balance = balance_info.get("balance")
            raw_digits = balance_info.get("moneyDigits")
            if raw_balance is not None and raw_digits is not None:
                digits = int(raw_digits)
                account_balance = round(float(raw_balance) / (10 ** digits), digits)
                account_balance_digits = digits
        except Exception as exc:
            logger.warning("Failed to fetch account balance from broker: %s", exc)

    return AuthStatusResponse(
        mode=settings.auth_mode,
        has_credentials=settings.has_ctrader_credentials,
        client_id=settings.ctrader_client_id,
        client_secret=settings.ctrader_client_secret,
        account_id=settings.ctrader_account_id,
        grant_id=grant_id,
        selected_account_id=selected_account_id,
        host_type="live" if settings.ctrader_use_live else "demo",
        broker_url=broker_url,
        has_grant_id=bool(grant_id),
        has_access_token=bool(settings.ctrader_access_token),
        has_refresh_token=bool(settings.ctrader_refresh_token),
        token_expires_at=token_expires_at,
        token_days_remaining=token_days_remaining,
        connected=feed_stats.get("connected", False),
        can_refresh=bool(settings.ctrader_refresh_token) or (
            bool(settings.ctrader_auth_broker_url) and bool(settings.ctrader_auth_grant_id)
        ),
        account_balance=account_balance,
        account_balance_digits=account_balance_digits,
    )


@admin_router.post("/auth/broker", response_model=AuthStatusResponse, tags=["Authentication"])
async def set_auth_broker(data: AuthBrokerRequest):
    """Set broker-based auth credentials and save to .env + config.yml."""
    from .config import save_yaml_config

    _update_env("CTRADER_AUTH_BROKER_URL", data.broker_url)
    _update_env("CTRADER_AUTH_GRANT_ID", data.grant_id)
    # Clear raw tokens to avoid conflicts
    _update_env("CTRADER_ACCESS_TOKEN", "")
    _update_env("CTRADER_REFRESH_TOKEN", "")
    if data.account_id is not None:
        _update_env("CTRADER_ACCOUNT_ID", str(data.account_id))
    if data.client_id:
        _update_env("CTRADER_CLIENT_ID", data.client_id)
    if data.client_secret:
        _update_env("CTRADER_CLIENT_SECRET", data.client_secret)

    # Also persist to the workspace YAML config
    yaml_updates: dict[str, object] = {
        "CTRADER_AUTH_BROKER_URL": data.broker_url,
        "CTRADER_AUTH_GRANT_ID": data.grant_id,
    }
    if data.account_id is not None:
        yaml_updates["CTRADER_ACCOUNT_ID"] = data.account_id
    if data.client_id:
        yaml_updates["CTRADER_CLIENT_ID"] = data.client_id
    if data.client_secret:
        yaml_updates["CTRADER_CLIENT_SECRET"] = data.client_secret
    save_yaml_config(yaml_updates)

    # Clear the cached settings so the new values are picked up
    from .config import get_settings as _gs
    if hasattr(_gs, "cache_clear"):
        _gs.cache_clear()

    return await get_auth_status()


@admin_router.post("/auth/direct", response_model=AuthStatusResponse, tags=["Authentication"])
async def set_auth_direct(data: AuthDirectRequest):
    """Set direct OAuth credentials and save to .env."""
    _update_env("CTRADER_CLIENT_ID", data.client_id)
    _update_env("CTRADER_CLIENT_SECRET", data.client_secret)
    _update_env("CTRADER_ACCESS_TOKEN", data.access_token)
    _update_env("CTRADER_REFRESH_TOKEN", data.refresh_token)
    _update_env("CTRADER_ACCOUNT_ID", str(data.account_id))
    # Clear broker credentials to avoid conflicts
    _update_env("CTRADER_AUTH_BROKER_URL", "")
    _update_env("CTRADER_AUTH_GRANT_ID", "")

    from .config import get_settings as _gs
    if hasattr(_gs, "cache_clear"):
        _gs.cache_clear()

    return await get_auth_status()


@public_router.get("/auth/broker/start-url", response_model=AuthBrokerStartUrlResponse, tags=["Authentication"])
async def get_broker_start_url():
    """Get the URL to start the broker OAuth flow in a browser."""
    settings = get_settings()
    broker_url = (settings.ctrader_auth_broker_url or "").rstrip("/")
    user_id = "cds_admin"
    auth_url = f"{broker_url}/auth/ctrader/start?user_id={user_id}"
    return AuthBrokerStartUrlResponse(
        auth_url=auth_url,
        broker_url=broker_url,
        user_id=user_id,
        instructions="Open this URL in a browser, login to cTrader, grant access. The admin UI polls for the grant_id automatically.",
    )


@public_router.get("/auth/broker/grant-latest", tags=["Authentication"])
async def get_broker_grant_latest(
    user_id: str = Query(..., description="User ID used to start the OAuth flow"),
    broker_url: str = Query(..., description="Auth broker base URL"),
):
    """Poll the broker for the latest grant_id after OAuth completes.

    Proxies to the broker's ``/internal/grant/latest`` endpoint. Returns 404
    (with ``grant_not_found``) while the user has not yet completed the cTrader
    approval — the admin UI retries until it gets a grant_id.
    """
    base = broker_url.rstrip("/")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{base}/internal/grant/latest", params={"user_id": user_id})
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="grant_not_found")
    if not resp.is_success:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


@public_router.get("/auth/broker/accounts", tags=["Authentication"])
async def get_broker_accounts(
    grant_id: str = Query(..., description="Grant ID from the broker"),
    broker_url: str = Query(..., description="Auth broker base URL"),
):
    """Fetch available cTrader trading accounts for a grant.

    Proxies to the broker's ``/internal/ctrader/accounts`` endpoint. The
    broker refreshes the access token if needed and lists accounts via
    the cTrader Open API.
    """
    base = broker_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{base}/internal/ctrader/accounts", params={"grant_id": grant_id})
    if not resp.is_success:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


# ── MCP Discovery ──────────────────────────────────────────────────────────────

@public_router.get("/mcp/tools", tags=["MCP Discovery"])
async def list_mcp_tools():
    """Return all available MCP tool signatures for programmatic client generation."""
    return {
        "tools": [
            {"name": "batch_call", "description": "Execute multiple tool calls in a single request", "inputSchema": {"type": "object", "properties": {"calls": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "arguments": {"type": "object"}}, "required": ["name"]}}}, "required": ["calls"]}},
            {"name": "add_symbol", "description": "Register a new trading instrument with metadata", "inputSchema": {"type": "object", "properties": {"symbol_id": {"type": "integer"}, "name": {"type": "string"}, "digits": {"type": "integer", "default": 5}, "pip_size": {"type": "number"}, "asset_class": {"type": "string", "default": "forex"}, "description": {"type": "string"}, "exchange": {"type": "string"}, "lot_size": {"type": "integer"}}, "required": ["symbol_id", "name"]}},
            {"name": "remove_symbol", "description": "Remove a symbol from the registry", "inputSchema": {"type": "object", "properties": {"symbol_id": {"type": "integer"}}, "required": ["symbol_id"]}},
            {"name": "list_symbols", "description": "List all registered symbols", "inputSchema": {"type": "object", "properties": {"status": {"type": "string", "enum": ["active", "inactive", "suspended"]}}}},
            {"name": "update_symbol", "description": "Update symbol metadata", "inputSchema": {"type": "object", "properties": {"symbol_id": {"type": "integer"}, "fields": {"type": "object"}}, "required": ["symbol_id", "fields"]}},
            {"name": "get_price", "description": "Get current price quote for a symbol", "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}},
            {"name": "get_ticks", "description": "Get historical tick data with time filtering", "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}, "from_ms": {"type": "integer"}, "to_ms": {"type": "integer"}, "limit": {"type": "integer", "default": 1000}}, "required": ["symbol"]}},
            {"name": "get_bars", "description": "Get OHLCV bars for a symbol and timeframe", "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}, "timeframe": {"type": "string", "enum": ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]}, "from_ms": {"type": "integer"}, "to_ms": {"type": "integer"}, "limit": {"type": "integer", "default": 500}}, "required": ["symbol", "timeframe"]}},
            {"name": "get_orderbook", "description": "Get latest order book snapshot for a symbol", "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}},
            {"name": "get_indicators", "description": "Calculate and retrieve technical indicators for a symbol", "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}, "timeframe": {"type": "string", "default": "1h"}}, "required": ["symbol"]}},
            {"name": "get_signals", "description": "Generate trading signals for a symbol", "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}, "timeframe": {"type": "string", "default": "1h"}}, "required": ["symbol"]}},
            {"name": "get_market_structure", "description": "Detect market structure (supports, resistances, swings)", "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}, "timeframe": {"type": "string", "default": "1h"}}, "required": ["symbol"]}},
            {"name": "check_quality", "description": "Run data quality check for a symbol", "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}, "timeframe": {"type": "string"}}}},
            {"name": "backfill_gaps", "description": "Backfill missing historical data gaps (requires active Data Service)", "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}, "timeframe": {"type": "string"}, "from_ms": {"type": "integer"}, "to_ms": {"type": "integer"}}, "required": ["symbol", "timeframe", "from_ms", "to_ms"]}},
            {"name": "set_symbol_config", "description": "Set symbol-specific configuration (Data Service will apply)", "inputSchema": {"type": "object", "properties": {"symbol_id": {"type": "integer"}, "name": {"type": "string"}, "enabled": {"type": "boolean"}, "collect_ticks": {"type": "boolean"}, "collect_bars": {"type": "boolean"}, "collect_depth": {"type": "boolean"}, "bar_timeframes": {"type": "array", "items": {"type": "string"}}}, "required": ["symbol_id", "name"]}},
            {"name": "get_stats", "description": "Get system and database statistics", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "get_feed_status", "description": "Get Data Service feed connection status", "inputSchema": {"type": "object", "properties": {}}},
        ]
    }


@public_router.get("/mcp/resources", tags=["MCP Discovery"])
async def list_mcp_resources():
    """Return all available MCP resource URIs to avoid hard-coding paths."""
    syms = _registry.list_active()
    resources = [
        {"uri": "marketdata://stats", "name": "System Statistics", "mimeType": "application/json", "description": "Database performance and storage metrics"},
        {"uri": "marketdata://symbols", "name": "Symbol Catalog", "mimeType": "application/json", "description": "Complete symbol registry with metadata"},
    ]
    for sym in syms:
        resources.append({"uri": f"marketdata://price/{sym.name}", "name": f"{sym.name} Price Snapshot", "mimeType": "application/json", "description": f"Latest price for {sym.name}"})
        for tf in ["1m", "1h", "1d"]:
            resources.append({"uri": f"marketdata://bars/{sym.name}/{tf}", "name": f"{sym.name} {tf} Bars", "mimeType": "application/json", "description": f"OHLCV bars for {sym.name} {tf}"})
    return {"resources": resources}


# ── Context Endpoint (AI agents) ───────────────────────────────────────────────

@public_router.get("/context/{symbol}", tags=["Context"])
async def get_market_context(symbol: str):
    """Return an aggregated market context snapshot for a symbol."""
    sym = _registry.resolve(symbol)
    if not sym:
        raise HTTPException(status_code=404, detail=f"Symbol not found: {symbol}")

    tick = await db_manager.get_latest_tick(sym.symbol_id)
    bars: dict[str, dict[str, Any]] = {}
    for tf in [TimeFrame.M1, TimeFrame.H1, TimeFrame.D1]:
        bar = await db_manager.get_latest_bar(sym.symbol_id, tf)
        if bar:
            bars[tf.value] = _bar_to_dict(bar)

    indicator_docs = await db_manager.get_indicators(symbol_id=sym.symbol_id, limit=20)
    indicators = [
        {
            "indicator": d.indicator,
            "value": d.value,
            "timeframe": d.timeframe.value if hasattr(d.timeframe, "value") else str(d.timeframe),
            "period": d.period,
            "timestamp_ms": d.timestamp_ms,
        }
        for d in indicator_docs
    ]

    signal_docs = await db_manager.get_signals(symbol_id=sym.symbol_id, limit=5)
    signals = [
        {
            "direction": d.direction,
            "strength": d.strength,
            "confidence": d.confidence,
            "indicators": d.indicators,
            "timeframe": d.timeframe.value if hasattr(d.timeframe, "value") else str(d.timeframe),
            "timestamp_ms": d.timestamp_ms,
        }
        for d in signal_docs
    ]

    quality_reports = []
    from .data_quality import DataQualityEngine
    engine = DataQualityEngine()
    quality_reports.append(_quality_report_to_dict(await engine.check_tick_quality(sym.symbol_id, sym.name)))
    for tf in [TimeFrame.M1, TimeFrame.H1, TimeFrame.D1]:
        quality_reports.append(_quality_report_to_dict(await engine.check_bar_quality(sym.symbol_id, sym.name, tf)))

    return {
        "symbol": sym.name,
        "symbol_id": sym.symbol_id,
        "timestamp_ms": int(time.time() * 1000),
        "tick": _tick_to_dict(tick) if tick else None,
        "bars": bars,
        "indicators": indicators,
        "signals": signals,
        "quality": quality_reports,
    }


@admin_router.post("/internal/config-changed", tags=["Internal"])
async def trigger_config_reload_endpoint():
    """Wake the Data Service config watch loop after an Appwrite config change."""
    try:
        await _ds_post("/internal/config-changed", {})
    except httpx.HTTPError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Data Service unreachable: {e}")
    return OperationResponse(success=True, message="Config reload triggered")


# ── Gold Quantitative Analysis Endpoints ───────────────────────────────────────

async def _fetch_gold_snapshot() -> dict[str, Any]:
    """Fetch gold quant snapshot from the data service daemon, with local fallback."""
    try:
        return await _ds_get("/gold/quant")
    except Exception as exc:
        logger.debug("Could not fetch gold snapshot from data service daemon: %s", exc)
    if _GOLD_ENGINE is None:
        raise HTTPException(status_code=503, detail="Gold Quant Engine not available")
    snapshot = await _GOLD_ENGINE.get_snapshot()
    builder = AgentContextBuilder()
    return builder.build_compact_dict(snapshot)


@public_router.get("/gold/quant", tags=["Gold Quant"])
async def get_gold_quant() -> dict[str, Any]:
    """Full quantitative snapshot for XAUUSD (MTF + order flow + levels + decisions)."""
    return await _fetch_gold_snapshot()


@public_router.get("/gold/mtf", tags=["Gold Quant"])
async def get_gold_mtf() -> dict[str, Any]:
    """Multi-timeframe confluence only."""
    snapshot = await _fetch_gold_snapshot()
    return {
        "symbol": snapshot.get("symbol"),
        "timestamp_ms": snapshot.get("timestamp_ms"),
        "multi_timeframe": snapshot.get("multi_timeframe", {}),
    }


@public_router.get("/gold/orderflow", tags=["Gold Quant"])
async def get_gold_orderflow() -> dict[str, Any]:
    """Tick volume and order flow metrics only."""
    snapshot = await _fetch_gold_snapshot()
    return {
        "symbol": snapshot.get("symbol"),
        "timestamp_ms": snapshot.get("timestamp_ms"),
        "order_flow": snapshot.get("order_flow", {}),
    }


@public_router.get("/gold/levels", tags=["Gold Quant"])
async def get_gold_levels() -> dict[str, Any]:
    """Key structural levels only."""
    snapshot = await _fetch_gold_snapshot()
    return {
        "symbol": snapshot.get("symbol"),
        "timestamp_ms": snapshot.get("timestamp_ms"),
        "key_levels": snapshot.get("key_levels", {}),
    }


@public_router.get("/gold/decision", tags=["Gold Quant"])
async def get_gold_decision() -> dict[str, Any]:
    """Agent decision matrix (short + long + limit)."""
    snapshot = await _fetch_gold_snapshot()
    return {
        "symbol": snapshot.get("symbol"),
        "timestamp_ms": snapshot.get("timestamp_ms"),
        "short_entry": snapshot.get("decision", {}).get("short_entry", {}),
        "long_entry": snapshot.get("decision", {}).get("long_entry", {}),
        "limit_order": snapshot.get("decision", {}).get("limit_order", {}),
        "phase_guidance": snapshot.get("decision", {}).get("phase_guidance", {}),
    }


@public_router.get("/gold/prompt", tags=["Gold Quant"])
async def get_gold_prompt() -> dict[str, Any]:
    """LLM-ready prompt text derived from the current gold quant snapshot."""
    snapshot = await _fetch_gold_snapshot()
    return {"symbol": snapshot.get("symbol"), "prompt": snapshot.get("agent_prompt", "")}


# ── Router Inclusion & Static Site Mount ───────────────────────────────────────

app.include_router(public_router)
app.include_router(admin_router)

# Mount the config/admin site under /admin so the root path is free for the
# public API (used by the Cloudflare tunnel ingress for dataservice.mrme.tech).
if SITE_DIR.is_dir():
    app.mount("/admin", StaticFiles(directory=str(SITE_DIR), html=True), name="site")
else:
    logger.warning("Site directory not found at %s; static UI will not be served", SITE_DIR)


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _build_symbol_responses(symbols: list[SymbolInfo]) -> list[SymbolResponse]:
    return [await _build_symbol_response(s) for s in symbols]


async def _build_symbol_response(info: SymbolInfo) -> SymbolResponse:
    cfg = _registry.get_config(info.symbol_id)
    tick = await db_manager.get_latest_tick(info.symbol_id)
    freshness = (time.time() - (tick.timestamp_ms / 1000.0)) if tick else None
    last_bars: dict[str, int] = {}
    for tf in [TimeFrame.M1, TimeFrame.H1, TimeFrame.D1]:
        bar = await db_manager.get_latest_bar(info.symbol_id, tf)
        if bar:
            last_bars[tf.value] = bar.timestamp_ms
    return SymbolResponse(symbol=info, config=cfg, last_tick_ms=tick.timestamp_ms if tick else None, last_bar_ms=last_bars, data_freshness_seconds=freshness)


async def _apply_symbol_subscriptions(symbol_id: int, data: SymbolConfigUpdate) -> None:
    body = {
        "symbols": [symbol_id],
        "subscribe_ticks": data.collect_ticks,
        "subscribe_bars": data.collect_bars,
        "subscribe_depth": data.collect_depth,
        "bar_timeframes": data.bar_timeframes,
    }
    try:
        await _ds_post("/feed/subscribe", body)
    except httpx.HTTPError:
        pass


def _apply_runtime_config(config: ServiceConfig) -> None:
    """Apply service config changes to the running API server process.

    The Data Service (port 9000) has its own ``_config_watch_loop`` that
    reloads from DB independently. This function handles the parts that
    affect the API server process itself.
    """
    # Log level
    logging.getLogger().setLevel(getattr(logging, config.log_level, logging.INFO))
    # Log the config change for observability
    logger.info(
        "Runtime config applied: feed_source=%s, tick_buffer=%d, bar_buffer=%d, depth_buffer=%d, log_level=%s",
        config.feed_source,
        config.tick_buffer_size,
        config.bar_buffer_size,
        config.depth_buffer_size,
        config.log_level,
    )


def _find_gaps(bars: list[OHLCVBar], tf_ms: int) -> list[tuple[int, int]]:
    if len(bars) < 2:
        return []
    # Deduplicate by aligned timestamp — cTrader may send multiple updates
    # for the same bar with slightly different timestamps. Keep the latest.
    seen: dict[int, OHLCVBar] = {}
    for bar in bars:
        aligned = (bar.timestamp_ms // tf_ms) * tf_ms
        if aligned not in seen or bar.timestamp_ms > seen[aligned].timestamp_ms:
            seen[aligned] = bar
    # Sort by aligned timestamp (dict keys) descending, not original timestamp
    aligned_ts_list = sorted(seen.keys(), reverse=True)
    if len(aligned_ts_list) < 2:
        return []
    gaps: list[tuple[int, int]] = []
    for i in range(1, len(aligned_ts_list)):
        prev_ts, curr_ts = aligned_ts_list[i - 1], aligned_ts_list[i]  # descending
        expected = prev_ts - tf_ms
        diff = abs(curr_ts - expected)
        if diff > 1:  # strict check after dedup+alignment
            # Compute actual gap boundaries (exclude the existing bars themselves)
            gap_start = curr_ts + tf_ms
            gap_end = prev_ts - tf_ms
            if gap_end > gap_start:
                # Merge with previous gap range if contiguous
                if gaps and gap_start <= gaps[-1][1] + tf_ms:
                    gaps[-1] = (gaps[-1][0], max(gaps[-1][1], gap_end))
                else:
                    gaps.append((gap_start, gap_end))
    return gaps


def _bar_to_dict(bar: OHLCVBar) -> dict[str, Any]:
    return {"symbol_id": bar.symbol_id, "symbol_name": bar.symbol_name, "timeframe": bar.timeframe.value, "open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close, "volume": bar.volume, "timestamp_ms": bar.timestamp_ms}


def _tick_to_dict(tick: TickData) -> dict[str, Any]:
    return {"symbol_id": tick.symbol_id, "symbol_name": tick.symbol_name, "bid": tick.bid, "ask": tick.ask, "bid_volume": tick.bid_volume, "ask_volume": tick.ask_volume, "timestamp_ms": tick.timestamp_ms, "spread": tick.spread}


def _quality_report_to_dict(report: Any) -> dict[str, Any]:
    if hasattr(report, "model_dump"):
        return report.model_dump(mode="json", exclude_none=True)
    return {"symbol_id": getattr(report, "symbol_id", None), "symbol_name": getattr(report, "symbol_name", None), "total_records": getattr(report, "total_records", 0), "score": getattr(report, "score", 0.0), "issues": getattr(report, "issues", [])}


# ── Entry Point ────────────────────────────────────────────────────────────────

def main() -> None:
    import uvicorn
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    port = settings.api_port
    logger.info("Starting OpenPI API server on %s:%d", settings.server_host, port)
    uvicorn.run(app, host=settings.server_host, port=port, log_level=settings.log_level.lower())


if __name__ == "__main__":
    main()
