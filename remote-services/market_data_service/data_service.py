"""Standalone Data Service — continuous feed ingestion, analytics, and quality monitoring.

This service runs independently of the MCP server. It:
- Maintains the cTrader feed connection
- Ingests ticks, bars, and order book data into the database (SQLite/MongoDB)
- Runs periodic analytics and signal generation
- Performs data quality monitoring and gap backfilling
- Watches symbol configs AND service config for runtime changes

The MCP server is stateless and only reads/writes the database.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .analytics import AnalyticsEngine
from .api_models import FeedConnectRequest, FeedSubscribeRequest
from .appwrite_config import fetch_service_config, write_env_from_config
from .config import get_settings
from .data_ingestion import DataIngestionEngine
from .data_quality import DataQualityEngine
from .database import db_manager
from .feed_manager import FeedManager
from .models import TimeFrame
from .symbol_registry import SymbolRegistry

logger = logging.getLogger(__name__)


class MarketDataService:
    """Standalone market data service with continuous operation."""

    def __init__(self) -> None:
        self._registry = SymbolRegistry()
        self._feed = FeedManager(self._registry)
        self._ingestion = DataIngestionEngine(self._registry)
        self._analytics = AnalyticsEngine()
        self._quality = DataQualityEngine(self._feed)
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._shutdown_event = asyncio.Event()
        self._config_reload_event = asyncio.Event()
        self._last_appwrite_updated_at: str | None = None

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start all subsystems."""
        if self._running:
            return
        self._running = True

        # Database
        await db_manager.connect()
        await self._registry.load_from_db()

        # Ingestion
        await self._ingestion.start()

        # Quality monitoring
        await self._quality.start()

        # Wire feed → ingestion
        self._feed.register_tick_callback(self._ingestion.get_tick_queue())
        self._feed.register_bar_callback(self._ingestion.get_bar_queue())
        self._feed.register_depth_callback(self._ingestion.get_depth_queue())

        # Background tasks
        self._tasks.append(asyncio.create_task(self._auto_connect_loop()))
        self._tasks.append(asyncio.create_task(self._analytics_loop()))
        self._tasks.append(asyncio.create_task(self._config_watch_loop()))
        self._tasks.append(asyncio.create_task(self._heartbeat_loop()))
        self._tasks.append(asyncio.create_task(self._backfill_loop()))

        logger.info("Market Data Service started")

    async def stop(self) -> None:
        """Graceful shutdown."""
        if not self._running:
            return
        self._running = False
        self._shutdown_event.set()

        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        await self._feed.disconnect()
        await self._ingestion.stop()
        await self._quality.stop()
        await db_manager.disconnect()

        logger.info("Market Data Service stopped")

    async def _shutdown_signal(self, sig: signal.Signals) -> None:
        logger.info("Received signal %s — shutting down", sig.name)
        await self.stop()

    def install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self._shutdown_signal(s)))
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                signal.signal(sig, lambda s, frame: asyncio.create_task(self._shutdown_signal(signal.Signals(s))))

    def trigger_config_reload(self) -> None:
        """Wake the config watch loop immediately.

        Safe to call from the same event loop; for cross-thread triggers use
        ``asyncio.run_coroutine_threadsafe``.
        """
        self._config_reload_event.set()
        logger.debug("Config reload triggered")

    # ── Background loops ─────────────────────────────────────────────────────

    async def _auto_connect_loop(self) -> None:
        """Maintain persistent feed connection with auto-reconnect."""
        settings = get_settings()
        while self._running:
            if not self._feed.is_connected and settings.has_ctrader_credentials:
                logger.info("Auto-connecting feed...")
                ok = await self._feed.connect()
                if ok:
                    # Reload registry so we pick up symbols added via API while disconnected
                    await self._registry.load_from_db()
                    # Auto-subscribe to configured symbols
                    for sym in self._registry.list_active():
                        cfg = self._registry.get_config(sym.symbol_id)
                        if cfg is None or not cfg.enabled:
                            continue
                        if cfg.collect_ticks:
                            await self._feed.subscribe_spots([sym.symbol_id])
                        if cfg.collect_bars:
                            for tf in cfg.bar_timeframes:
                                await self._feed.subscribe_bars(sym.symbol_id, tf.value)
                        if cfg.collect_depth:
                            await self._feed.subscribe_depth(sym.symbol_id)
            # Sleep with early wake on shutdown
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=30.0)

    async def _analytics_loop(self) -> None:
        """Periodically compute indicators and signals for active symbols."""
        while self._running:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=60.0)
            if not self._running:
                break
            try:
                await self._run_analytics_cycle()
            except Exception as exc:
                logger.warning("Analytics cycle failed: %s", exc)

    async def _run_analytics_cycle(self) -> None:
        for sym in self._registry.list_active():
            cfg = self._registry.get_config(sym.symbol_id)
            if cfg is None or not cfg.enabled:
                continue
            timeframes = cfg.bar_timeframes or [TimeFrame.M1]
            for tf in timeframes:
                bars = await db_manager.get_bars(sym.symbol_id, tf, limit=200)
                if len(bars) >= 50:
                    await self._analytics.compute_all_indicators(
                        sym.symbol_id, sym.name, tf, bars
                    )
                    await self._analytics.generate_signals(
                        sym.symbol_id, sym.name, tf, bars
                    )
                    await self._analytics.detect_market_structure(
                        sym.symbol_id, sym.name, tf, bars
                    )

    async def _config_watch_loop(self) -> None:
        """Watch for config changes and apply them.

        Wakes immediately when ``trigger_config_reload()`` is called, otherwise
        polls every 10 seconds. When Appwrite credentials are available, the
        canonical ``service_config`` row is fetched and persisted to ``.env``
        so the running service picks up credential / runtime changes.
        """
        from .api_models import ServiceConfig
        from .config import get_settings as _gs
        last_config_update: str | None = None
        while self._running:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._config_reload_event.wait(), timeout=10.0)
            if not self._running:
                break
            # Consume the event so we don't re-trigger immediately.
            self._config_reload_event.clear()
            try:
                # Clear the settings cache so we pick up credentials saved by
                # the admin UI (e.g. a new grant_id after OAuth) since the
                # process started. The cache is lru_cache'd at module level.
                if hasattr(_gs, "cache_clear"):
                    _gs.cache_clear()

                # Pull the canonical config from Appwrite when available.
                settings = _gs()
                if settings.appwrite_project_id and settings.appwrite_api_key:
                    try:
                        appwrite_config, updated_at = fetch_service_config()
                        if updated_at and updated_at != self._last_appwrite_updated_at:
                            self._last_appwrite_updated_at = updated_at
                            write_env_from_config(appwrite_config)
                            logger.info("Config watch: applied Appwrite service config")
                    except Exception as exc:
                        logger.debug("Appwrite config fetch failed: %s", exc)

                # Reload registry from DB so we pick up symbols/configs changed via API
                await self._registry.load_from_db()
                await self._apply_pending_subscriptions()

                # Reload service config from DB to pick up runtime tuning changes
                doc = await db_manager.get_service_config()
                if doc:
                    config = ServiceConfig(**doc)
                    updated_at = str(doc.get("updated_at", ""))
                    if updated_at != last_config_update:
                        last_config_update = updated_at
                        self._apply_service_config(config)
            except Exception as exc:
                logger.warning("Config watch loop failed: %s", exc)

    def _apply_service_config(self, config: Any) -> None:
        """Apply runtime service config changes (buffer sizes, log level, etc.)."""
        # Log level
        logging.getLogger().setLevel(getattr(logging, config.log_level, logging.INFO))

        # Buffer sizes — update the ingestion engine queues if they expose limits
        if hasattr(self._ingestion, "tick_queue") and hasattr(self._ingestion.tick_queue, "maxsize"):
            new_size = config.tick_buffer_size
            current = self._ingestion.tick_queue.maxsize
            if current != new_size:
                logger.info("Config watch: tick_buffer_size %d → %d", current, new_size)

        if hasattr(self._ingestion, "bar_queue") and hasattr(self._ingestion.bar_queue, "maxsize"):
            new_size = config.bar_buffer_size
            current = self._ingestion.bar_queue.maxsize
            if current != new_size:
                logger.info("Config watch: bar_buffer_size %d → %d", current, new_size)

        if hasattr(self._ingestion, "depth_queue") and hasattr(self._ingestion.depth_queue, "maxsize"):
            new_size = config.depth_buffer_size
            current = self._ingestion.depth_queue.maxsize
            if current != new_size:
                logger.info("Config watch: depth_buffer_size %d → %d", current, new_size)

        logger.debug("Config watch: applied service config (updated_by=%s)", getattr(config, "updated_by", "?"))

    async def _apply_pending_subscriptions(self) -> None:
        """Apply any symbol subscriptions that haven't been sent to the feed."""
        if not self._feed.is_connected:
            return
        for sym in self._registry.list_active():
            cfg = self._registry.get_config(sym.symbol_id)
            if cfg is None or not cfg.enabled:
                continue
            if cfg.collect_ticks:
                await self._feed.subscribe_spots([sym.symbol_id])
            if cfg.collect_bars:
                for tf in cfg.bar_timeframes:
                    await self._feed.subscribe_bars(sym.symbol_id, tf.value)
            if cfg.collect_depth:
                await self._feed.subscribe_depth(sym.symbol_id)

    async def _heartbeat_loop(self) -> None:
        """Write periodic heartbeat for MCP server health checks."""
        while self._running:
            try:
                await db_manager.insert_service_heartbeat(
                    service="data-service",
                    timestamp=datetime.now(UTC),
                    data={
                        "status": "running",
                        "feed_connected": self._feed.is_connected,
                        "feed_source": self._feed.feed_source.value,
                        "active_symbols": len(self._registry.list_active()),
                        "subscribed_spots": self._feed.get_subscribed_spots_count(),
                        "subscribed_bars": self._feed.get_subscribed_bars_count(),
                        "subscribed_depth": self._feed.get_subscribed_depth_count(),
                    },
                )
            except Exception as exc:
                logger.warning("Heartbeat write failed: %s", exc)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=10.0)

    async def _backfill_loop(self) -> None:
        """Poll for pending backfill requests and execute them."""
        while self._running:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=30.0)
            if not self._running:
                break
            try:
                await self._process_backfill_requests()
            except Exception as exc:
                logger.warning("Backfill loop failed: %s", exc)

    async def _process_backfill_requests(self) -> None:
        """Process one pending backfill request."""
        if not self._feed.is_connected:
            return
        req = await db_manager.get_pending_backfill_request()
        if not req:
            return
        try:
            from .models import TimeFrame
            bars = await self._feed.fetch_historical_bars(
                req["symbol_id"], req["timeframe"], req["from_ms"], req["to_ms"]
            )
            filled = 0
            for bar in bars:
                from .models import FeedSource, OHLCVBar
                ohlcv = OHLCVBar(
                    symbol_id=bar.symbol_id,
                    symbol_name=bar.symbol_name,
                    timeframe=TimeFrame(req["timeframe"]),
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    timestamp_ms=bar.timestamp_ms,
                    source=FeedSource.BACKFILL,
                )
                exists = await db_manager.bar_exists(bar.symbol_id, TimeFrame(req["timeframe"]), bar.timestamp_ms)
                if not exists:
                    await db_manager.store_bars([ohlcv])
                    filled += 1
            await db_manager.update_backfill_request(
                req["id"],
                {"status": "completed", "filled": filled, "completed_at": datetime.now(UTC).isoformat()},
            )
            logger.info("Backfill completed: %s %s filled=%d", req["symbol_name"], req["timeframe"], filled)
        except Exception as exc:
            logger.warning("Backfill request failed: %s", exc)
            await db_manager.update_backfill_request(
                req["id"],
                {"status": "failed", "error": str(exc), "completed_at": datetime.now(UTC).isoformat()},
            )

    # ── Public API ───────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def feed_connected(self) -> bool:
        return self._feed.is_connected

    def get_feed_stats(self) -> dict[str, Any]:
        return {
            "connected": self._feed.is_connected,
            "feed_source": self._feed.feed_source.value,
            "subscribed_spots": self._feed.get_subscribed_spots_count(),
            "subscribed_bars": self._feed.get_subscribed_bars_count(),
            "subscribed_depth": self._feed.get_subscribed_depth_count(),
        }


# Singleton instance
# ── Control API (port 9000) ────────────────────────────────────────────────────
# Simple HTTP API for the OpenPI REST server to proxy feed operations.
# Both run in the same process so we access _feed directly.

class FeedControlStatus(BaseModel):
    connected: bool
    feed_source: str
    subscribed_spots: int
    subscribed_bars: int
    subscribed_depth: int


def get_control_app() -> FastAPI:
    service = get_data_service()

    control_app = FastAPI(title="Data Service Control API")
    control_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @control_app.get("/feed/status", response_model=FeedControlStatus, tags=["Feed"])
    async def feed_status() -> FeedControlStatus:
        return FeedControlStatus(
            connected=service._feed.is_connected,
            feed_source=service._feed.feed_source.value,
            subscribed_spots=len(service._feed._subscribed_spots),
            subscribed_bars=service._feed.get_subscribed_bars_count(),
            subscribed_depth=len(service._feed._subscribed_depth),
        )

    @control_app.post("/feed/connect", tags=["Feed"])
    async def feed_connect(req: FeedConnectRequest | None = None):
        if service._feed.is_connected and not (req and req.reconnect):
            return {"success": True, "message": "Already connected to market data feed"}
        ok = await service._feed.connect()
        if not ok:
            return {"success": False, "message": "Failed to connect to feed"}
        # Auto-subscribe if symbols provided
        if req and req.symbols:
            for sym_id in req.symbols:
                await service._feed.subscribe_spots([sym_id])
        return {"success": True, "message": "Connected to market data feed"}

    @control_app.post("/feed/disconnect", tags=["Feed"])
    async def feed_disconnect():
        await service._feed.disconnect()
        return {"success": True, "message": "Disconnected from market data feed"}

    @control_app.post("/feed/subscribe", tags=["Feed"])
    async def feed_subscribe(req: FeedSubscribeRequest):
        if not service._feed.is_connected:
            return {"success": False, "message": "Feed not connected"}
        for sym_id in req.symbols:
            if req.subscribe_ticks:
                await service._feed.subscribe_spots([sym_id])
            if req.subscribe_bars:
                for tf in req.bar_timeframes:
                    await service._feed.subscribe_bars(sym_id, tf)
            if req.subscribe_depth:
                await service._feed.subscribe_depth(sym_id)
        return {"success": True, "message": f"Subscribed to {len(req.symbols)} symbols", "data": {"symbols": req.symbols}}

    @control_app.post("/feed/unsubscribe", tags=["Feed"])
    async def feed_unsubscribe(body: dict):
        symbols = body.get("symbols", [])
        for sym_id in symbols:
            await service._feed.unsubscribe_all(sym_id)
        return {"success": True, "message": f"Unsubscribed from {len(symbols)} symbols"}

    @control_app.get("/health", tags=["Health"])
    async def ds_health():
        return {
            "status": "healthy" if service._feed.is_connected else "degraded",
            "feed_connected": service._feed.is_connected,
        }

    @control_app.post("/gaps/fetch", tags=["Gaps"])
    async def fetch_historical(body: dict):
        symbol_id = body.get("symbol_id")
        tf = body.get("timeframe", "1h")
        from_ms = body.get("from_ms")
        to_ms = body.get("to_ms")
        bars = await service._feed.fetch_historical_bars(symbol_id, tf, from_ms, to_ms)
        return {
            "symbol_id": symbol_id,
            "timeframe": tf,
            "bars": [{"open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume, "timestamp_ms": b.timestamp_ms} for b in bars],
        }

    @control_app.get("/symbols/available", tags=["Symbols"])
    async def available_symbols():
        symbols = await service._feed.get_available_symbols()
        return {"symbols": symbols, "count": len(symbols)}

    @control_app.get("/symbols/resolve", tags=["Symbols"])
    async def resolve_symbol(name: str):
        result = await service._feed.resolve_symbol_by_name(name)
        if not result:
            raise HTTPException(status_code=404, detail=f"Symbol '{name}' not found in cTrader")
        return result

    @control_app.post("/internal/config-changed", tags=["Internal"])
    async def config_changed():
        """Wake the config watch loop immediately."""
        service.trigger_config_reload()
        return {"success": True, "message": "Config reload triggered"}

    return control_app


# ── Service singleton ──────────────────────────────────────────────────────────

_data_service: MarketDataService | None = None


def get_data_service() -> MarketDataService:
    global _data_service
    if _data_service is None:
        _data_service = MarketDataService()
    return _data_service


# ── Entry point ──────────────────────────────────────────────────────────────

async def _run_service() -> None:
    import uvicorn
    service = get_data_service()
    service.install_signal_handlers()
    await service.start()

    # Start control API server on ds_control_port (9000)
    settings = get_settings()
    control_app = get_control_app()
    config = uvicorn.Config(control_app, host="127.0.0.1", port=settings.ds_control_port, log_level="warning", access_log=False)
    control_server = uvicorn.Server(config)
    logger.info("Control API listening on 127.0.0.1:%d", settings.ds_control_port)
    # Run control server and main loop concurrently
    control_task = asyncio.create_task(control_server.serve())
    while service.is_running:
        await asyncio.sleep(1)
    control_task.cancel()
    await asyncio.gather(control_task, return_exceptions=True)


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(_run_service())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)


if __name__ == "__main__":
    main()
