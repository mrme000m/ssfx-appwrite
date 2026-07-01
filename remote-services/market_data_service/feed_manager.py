"""Intelligent feed management — cTrader connection lifecycle and subscriptions."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from .auth import AuthManager
from .config import get_settings
from .models import FeedSource, TimeFrame
from .symbol_registry import SymbolRegistry
from .util import setup_ctrader_import_path

logger = logging.getLogger(__name__)

# ── cTrader client import ────────────────────────────────────────────────────
# Uses shared import path setup for consistency across service modules

CTRADER_CLIENT_AVAILABLE = False

setup_ctrader_import_path()

try:
    from ctrader_client import (
        BarClose,
        CTraderSession,
        DepthUpdate,
        MarketDataManager,
        SpotTick,
        TransportType,
    )
    CTRADER_CLIENT_AVAILABLE = True
    logger.info("cTrader client imported successfully")
except ImportError as exc:
    logger.warning("cTrader client not available: %s", exc)


class FeedManager:
    """Manages external data feed connections with auto-reconnect and backfill.

    Uses the cTrader Open API v3 client's event bus for receiving market data
    events, avoiding queue-consumption race conditions with the session's
    internal pumps.
    """

    def __init__(self, symbol_registry: SymbolRegistry) -> None:
        self._registry = symbol_registry
        self._session: Any = None
        self._auth_mgr: AuthManager | None = None
        self._tasks: list[asyncio.Task] = []
        self._connected = False
        self._connecting = False
        self._connect_task: asyncio.Task | None = None
        self._subscribed_spots: set[int] = set()
        self._subscribed_bars: dict[int, set[str]] = {}
        self._subscribed_depth: set[int] = set()
        self._tick_callbacks: list[asyncio.Queue] = []
        self._bar_callbacks: list[asyncio.Queue] = []
        self._depth_callbacks: list[asyncio.Queue] = []
        self._last_tick_time: dict[int, float] = {}
        self._reconnect_task: asyncio.Task | None = None
        self._event_handlers_registered = False

    @property
    def is_connected(self) -> bool:
        return self._connected and self._session is not None

    @property
    def feed_source(self) -> FeedSource:
        return FeedSource.CTRADER if self.is_connected else FeedSource.MANUAL

    # ── Connection lifecycle ─────────────────────────────────────────────────

    async def connect(self) -> bool:
        if not CTRADER_CLIENT_AVAILABLE:
            logger.warning("cTrader client not available — feed connection skipped")
            return False
        if self._connected:
            return True
        if self._connecting and self._connect_task is not None:
            logger.info("Waiting for pending connection to complete...")
            try:
                await asyncio.wait_for(self._connect_task, timeout=30.0)
            except TimeoutError:
                logger.warning("Connection wait timed out")
            return self._connected

        # Clear the settings cache so we pick up credentials saved by the
        # admin UI (e.g. a new grant_id after OAuth) since process start.
        from .config import get_settings as _gs
        if hasattr(_gs, "cache_clear"):
            _gs.cache_clear()

        settings = get_settings()
        if not settings.has_ctrader_credentials:
            logger.warning("cTrader credentials not configured")
            return False

        self._connecting = True
        self._connect_task = asyncio.create_task(self._do_connect())
        try:
            result = await self._connect_task
            return result
        except asyncio.CancelledError:
            logger.warning("Connection was cancelled")
            self._connecting = False
            return False

    async def _do_connect(self) -> bool:
        """Core connection logic — must be run inside a task."""
        settings = get_settings()
        try:
            # Initialize auth manager based on configuration
            self._auth_mgr = AuthManager(
                broker_url=settings.ctrader_auth_broker_url,
                grant_id=settings.ctrader_auth_grant_id,
                client_id=settings.ctrader_client_id,
                client_secret=settings.ctrader_client_secret,
                access_token=settings.ctrader_access_token,
                refresh_token=settings.ctrader_refresh_token,
                internal_api_key=settings.ctrader_internal_api_key,
                appwrite_mode=settings.ctrader_use_appwrite_auth,
            )
            await self._auth_mgr.initialize()
            logger.info("Auth initialized (%s mode)", self._auth_mgr.mode)

            transport = TransportType.TCP
            if settings.ctrader_transport_type.lower() == "ws":
                transport = TransportType.WS

            self._session = CTraderSession(
                account_id=settings.ctrader_account_id,
                client_id=settings.ctrader_client_id,
                client_secret=settings.ctrader_client_secret,
                access_token=self._auth_mgr.access_token if self._auth_mgr.mode == "raw" else None,
                token_manager=self._auth_mgr.token_manager,
                use_live=settings.ctrader_use_live,
                transport_type=transport,
            )
            await self._session.start()
            self._connected = True
            self._connecting = False

            # Start auto-refresh for token lifecycle management
            if self._auth_mgr.mode != "raw":
                await self._auth_mgr.start_auto_refresh()

            # Register our symbols with the client's MarketDataManager so
            # real-time price conversion uses correct digits / pip sizes.
            await self._sync_symbols_to_client()

            # Subscribe to the session's event bus instead of competing
            # with the session's internal queue pumps for items.
            self._register_event_handlers()

            # Start watchdog
            self._tasks.append(asyncio.create_task(self._watchdog()))

            logger.info(
                "Feed connected to cTrader (%s)",
                "live" if settings.ctrader_use_live else "demo",
            )
            return True

        except Exception as exc:
            logger.error("Feed connection failed: %s", exc)
            self._connecting = False
            self._schedule_reconnect()
            return False

    async def disconnect(self) -> None:
        self._connected = False
        # Stop auth auto-refresh
        if self._auth_mgr:
            await self._auth_mgr.stop_auto_refresh()
            self._auth_mgr = None
        # Unregister event handlers to avoid leaks across reconnects
        if self._event_handlers_registered and self._session:
            try:
                self._session.event_bus.unsubscribe(SpotTick, self._on_tick_event)
                self._session.event_bus.unsubscribe(BarClose, self._on_bar_event)
                self._session.event_bus.unsubscribe(DepthUpdate, self._on_depth_event)
            except Exception as exc:
                logger.debug("Event bus unsubscribe error (non-critical): %s", exc)
        self._event_handlers_registered = False
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        if self._session:
            try:
                await self._session.close()
            except Exception as exc:
                logger.warning("Error closing session: %s", exc)
            self._session = None
        if self._reconnect_task:
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task
            self._reconnect_task = None
        logger.info("Feed disconnected")

    def _schedule_reconnect(self) -> None:
        if self._reconnect_task is None or self._reconnect_task.done():
            settings = get_settings()
            if settings.ctrader_client_id:
                self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        """Exponential-backoff reconnect with subscription replay."""
        settings = get_settings()
        delay = settings.reconnect_delay
        max_delay = settings.max_reconnect_delay
        while not self._connected:
            logger.info("Feed reconnecting in %.1fs...", delay)
            await asyncio.sleep(delay)
            if await self.connect():
                await self._replay_subscriptions()
                return
            delay = min(delay * 2, max_delay)

    # ── Symbol synchronisation ───────────────────────────────────────────────

    async def _sync_symbols_to_client(self) -> None:
        """Register service registry symbols with the cTrader client.

        The client's MarketDataManager needs symbol metadata (name, digits)
        to correctly convert relative integer prices to floats in real-time
        events. We also pull fresh metadata from cTrader when available.
        """
        if not self._session:
            return

        # 1. Push our registry symbols into the client
        for sym in self._registry.list_all():
            self._session.market_data.register_symbol(
                sym.symbol_id, sym.name, digits=sym.digits
            )

        # 2. Pull enriched metadata from cTrader for active symbols
        try:
            active_ids = self._registry.active_symbol_ids()
            if active_ids:
                # Only enrich symbols that cTrader actually knows about
                # (session._load_symbols() populates market_data._symbols)
                client_known = set(self._session.market_data._symbols.keys())
                enrich_ids = [sid for sid in active_ids if sid in client_known]
                unknown_ids = set(active_ids) - client_known
                if unknown_ids:
                    logger.warning(
                        "Symbols not known to cTrader (skipping enrich): %s",
                        unknown_ids,
                    )
                if enrich_ids:
                    await self._session.market_data.enrich_symbols(
                        enrich_ids, self._session.account_id
                    )
                    # Update our registry with cTrader's authoritative metadata
                    for sid in enrich_ids:
                        client_info = self._session.market_data._symbols.get(sid)
                        if not client_info:
                            continue
                        sym = self._registry.get(sid)
                        if not sym:
                            continue
                        updates: dict[str, Any] = {}
                        if client_info.digits and sym.digits != client_info.digits:
                            updates["digits"] = client_info.digits
                        # Sync volume and lot metadata
                        if client_info.min_volume and sym.min_volume != client_info.min_volume:
                            updates["min_volume"] = client_info.min_volume
                        if client_info.step_volume and sym.volume_step != client_info.step_volume:
                            updates["volume_step"] = client_info.step_volume
                        if client_info.lot_size and sym.lot_size != client_info.lot_size:
                            updates["lot_size"] = client_info.lot_size
                        # pip_position defaults to digits if not set
                        if sym.pip_position is None and client_info.digits:
                            updates["pip_position"] = client_info.digits
                        if updates:
                            logger.info(
                                "Updating %s metadata from cTrader: %s",
                                sym.name,
                                updates,
                            )
                            await self._registry.update(sid, **updates)
        except Exception as exc:
            logger.warning("Failed to enrich symbols from cTrader: %s", exc)

    # ── Event-bus integration (replaces queue pumps) ─────────────────────────

    def _register_event_handlers(self) -> None:
        """Subscribe to the session's event bus for market data events.

        Using the event bus avoids a race condition where both the session's
        internal _pump_bus() tasks and our own pump tasks compete for the
        same asyncio.Queue items (Queue.get() only delivers to one consumer).
        """
        if self._event_handlers_registered or not self._session:
            return
        self._session.event_bus.subscribe(SpotTick, self._on_tick_event)
        self._session.event_bus.subscribe(BarClose, self._on_bar_event)
        self._session.event_bus.subscribe(DepthUpdate, self._on_depth_event)
        self._event_handlers_registered = True
        logger.debug("Registered event-bus handlers for ticks/bars/depth")

    async def _on_tick_event(self, tick: SpotTick) -> None:
        """Handle SpotTick events from the event bus."""
        self._last_tick_time[tick.symbol_id] = time.time()
        await self._distribute_tick(tick)

    async def _on_bar_event(self, bar: BarClose) -> None:
        """Handle BarClose events from the event bus."""
        await self._distribute_bar(bar)

    async def _on_depth_event(self, depth: DepthUpdate) -> None:
        """Handle DepthUpdate events from the event bus."""
        await self._distribute_depth(depth)

    # ── Subscriptions ────────────────────────────────────────────────────────

    async def subscribe_spots(self, symbol_ids: list[int]) -> None:
        if not self._session or not self._connected:
            self._subscribed_spots.update(symbol_ids)
            return
        new_ids = [s for s in symbol_ids if s not in self._subscribed_spots]
        if new_ids:
            try:
                await self._session.market_data.subscribe_spots(
                    self._session.account_id, new_ids
                )
                self._subscribed_spots.update(new_ids)
                logger.info("Subscribed spots: %s", new_ids)
            except Exception as exc:
                logger.warning("Spot subscription failed: %s", exc)

    async def subscribe_bars(self, symbol_id: int, period: str) -> None:
        if not self._session or not self._connected:
            self._subscribed_bars.setdefault(symbol_id, set()).add(period)
            return
        if period not in self._subscribed_bars.get(symbol_id, set()):
            try:
                # Convert internal timeframe format (e.g. '1m') to cTrader enum name ('M1')
                try:
                    tf = TimeFrame(period)
                    ctrader_period = tf.to_ctrader_period()
                except ValueError:
                    ctrader_period = period
                await self._session.market_data.subscribe_live_bars(
                    self._session.account_id, symbol_id, ctrader_period
                )
                self._subscribed_bars.setdefault(symbol_id, set()).add(period)
                logger.info("Subscribed bars: %s %s", symbol_id, period)
            except Exception as exc:
                logger.warning("Bar subscription failed: %s", exc)

    async def subscribe_depth(self, symbol_id: int) -> None:
        if not self._session or not self._connected:
            self._subscribed_depth.add(symbol_id)
            return
        if symbol_id not in self._subscribed_depth:
            try:
                await self._session.market_data.subscribe_depth(
                    self._session.account_id, symbol_id
                )
                self._subscribed_depth.add(symbol_id)
                logger.info("Subscribed depth: %s", symbol_id)
            except Exception as exc:
                logger.warning("Depth subscription failed: %s", exc)

    async def _replay_subscriptions(self) -> None:
        if self._subscribed_spots:
            await self.subscribe_spots(list(self._subscribed_spots))
        for sid, periods in self._subscribed_bars.items():
            for period in periods:
                await self.subscribe_bars(sid, period)
        for sid in self._subscribed_depth:
            await self.subscribe_depth(sid)

    async def unsubscribe_all(self, symbol_id: int) -> None:
        self._subscribed_spots.discard(symbol_id)
        self._subscribed_bars.pop(symbol_id, None)
        self._subscribed_depth.discard(symbol_id)

    # ── Callback registration ────────────────────────────────────────────────

    def register_tick_callback(self, queue: asyncio.Queue) -> None:
        self._tick_callbacks.append(queue)

    def register_bar_callback(self, queue: asyncio.Queue) -> None:
        self._bar_callbacks.append(queue)

    def register_depth_callback(self, queue: asyncio.Queue) -> None:
        self._depth_callbacks.append(queue)

    # ── Distribution ─────────────────────────────────────────────────────────

    async def _distribute_tick(self, tick: SpotTick) -> None:
        for queue in self._tick_callbacks:
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(tick)

    async def _distribute_bar(self, bar: BarClose) -> None:
        for queue in self._bar_callbacks:
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(bar)

    async def _distribute_depth(self, depth: DepthUpdate) -> None:
        for queue in self._depth_callbacks:
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(depth)

    # ── Watchdog ─────────────────────────────────────────────────────────────

    async def _watchdog(self) -> None:
        settings = get_settings()
        threshold = settings.stale_threshold_seconds
        while self._connected:
            await asyncio.sleep(threshold)
            now = time.time()
            stale = [
                sid
                for sid, last in self._last_tick_time.items()
                if now - last > threshold * 2
            ]
            if stale:
                logger.warning("Stale feeds detected for symbols: %s", stale)

    # ── Historical data helpers ──────────────────────────────────────────────

    async def fetch_historical_bars(
        self, symbol_id: int, period: str, from_ms: int, to_ms: int
    ) -> list[BarClose]:
        if not self._session or not self._connected:
            return []
        try:
            # Convert internal timeframe format (e.g. '1m') to cTrader enum name ('M1')
            try:
                tf = TimeFrame(period)
                ctrader_period = tf.to_ctrader_period()
            except ValueError:
                ctrader_period = period
            res = await self._session.market_data.get_historical_trendbars(
                self._session.account_id, symbol_id, ctrader_period, from_ms, to_ms
            )
            bars = []
            sym = self._registry.get(symbol_id)
            # Prefer cTrader client metadata (most authoritative)
            client_sym = (
                self._session.market_data._symbols.get(symbol_id)
                if self._session
                else None
            )
            digits = client_sym.digits if client_sym else (sym.digits if sym else 5)
            name = client_sym.name if client_sym else (sym.name if sym else str(symbol_id))
            for tb in getattr(res, "trendbar", []):
                low = MarketDataManager.price_from_relative(tb.low, digits)
                bars.append(
                    BarClose(
                        symbol_id=symbol_id,
                        symbol_name=name,
                        period=period,
                        open=MarketDataManager.price_from_relative(
                            tb.low + tb.deltaOpen, digits
                        ),
                        high=MarketDataManager.price_from_relative(
                            tb.low + tb.deltaHigh, digits
                        ),
                        low=low,
                        close=MarketDataManager.price_from_relative(
                            tb.low + tb.deltaClose, digits
                        ),
                        volume=MarketDataManager.volume_from_api(tb.volume),
                        timestamp_ms=getattr(tb, "utcTimestampInMinutes", 0) * 60_000,
                    )
                )
            return bars
        except Exception as exc:
            logger.warning("Historical bars fetch failed: %s", exc)
            return []

    async def fetch_historical_ticks(
        self, symbol_id: int, from_ms: int, to_ms: int
    ) -> list[SpotTick]:
        if not self._session or not self._connected:
            return []
        try:
            res = await self._session.market_data.get_historical_ticks(
                self._session.account_id, symbol_id, from_ms, to_ms
            )
            ticks = []
            sym = self._registry.get(symbol_id)
            client_sym = (
                self._session.market_data._symbols.get(symbol_id)
                if self._session
                else None
            )
            digits = client_sym.digits if client_sym else (sym.digits if sym else 5)
            name = client_sym.name if client_sym else (sym.name if sym else str(symbol_id))
            for td in getattr(res, "tickData", []):
                # ProtoOATickData has only `timestamp` and `tick` (single price),
                # NOT bid/ask. See protobuf DESCRIPTOR:
                #   timestamp: required #1
                #   tick:      required #2
                price = MarketDataManager.price_from_relative(td.tick, digits)
                ticks.append(
                    SpotTick(
                        symbol_id=symbol_id,
                        symbol_name=name,
                        bid=price,
                        ask=price,
                        timestamp_ms=td.timestamp,
                    )
                )
            return ticks
        except Exception as exc:
            logger.warning("Historical ticks fetch failed: %s", exc)
            return []

    def get_subscribed_spots_count(self) -> int:
        return len(self._subscribed_spots)

    def get_subscribed_bars_count(self) -> int:
        return sum(len(p) for p in self._subscribed_bars.values())

    def get_subscribed_depth_count(self) -> int:
        return len(self._subscribed_depth)

    async def get_available_symbols(self) -> list[dict[str, Any]]:
        """Return cTrader's symbol list (id, name, description, enabled).

        Requires an active cTrader session. Returns empty list if not connected.
        """
        if not self._session or not self._connected:
            return []
        try:
            res = await self._session.market_data.get_symbols(self._session.account_id)
            symbols = []
            for sym in getattr(res, "symbol", []):
                symbols.append({
                    "symbol_id": getattr(sym, "symbolId", 0),
                    "name": getattr(sym, "symbolName", ""),
                    "description": getattr(sym, "description", ""),
                    "enabled": getattr(sym, "enabled", False),
                    "base_asset_id": getattr(sym, "baseAssetId", None),
                    "quote_asset_id": getattr(sym, "quoteAssetId", None),
                })
            return symbols
        except Exception as exc:
            logger.warning("Failed to fetch available symbols: %s", exc)
            return []

    async def resolve_symbol_by_name(self, name: str) -> dict[str, Any] | None:
        """Resolve a symbol name to its cTrader metadata.

        Returns dict with symbol_id, name, digits, description, etc. or None.
        """
        if not self._session or not self._connected:
            return None
        try:
            # First check the client's cache
            for sid, info in self._session.market_data._symbols.items():
                if info.name.upper() == name.upper():
                    return {
                        "symbol_id": sid,
                        "name": info.name,
                        "digits": info.digits,
                        "pip_size": info.pip_size,
                        "min_volume": info.min_volume,
                        "step_volume": info.step_volume,
                        "lot_size": info.lot_size,
                    }
            # Fetch from cTrader server
            res = await self._session.market_data.get_symbols(self._session.account_id)
            for sym in getattr(res, "symbol", []):
                if getattr(sym, "symbolName", "").upper() == name.upper():
                    sid = getattr(sym, "symbolId", 0)
                    # Try to enrich with full metadata
                    try:
                        await self._session.market_data.enrich_symbols([sid], self._session.account_id)
                        info = self._session.market_data._symbols.get(sid)
                        if info:
                            return {
                                "symbol_id": sid,
                                "name": info.name,
                                "digits": info.digits,
                                "pip_size": info.pip_size,
                                "min_volume": info.min_volume,
                                "step_volume": info.step_volume,
                                "lot_size": info.lot_size,
                            }
                    except Exception:
                        pass
                    # Return light symbol info
                    return {
                        "symbol_id": sid,
                        "name": getattr(sym, "symbolName", ""),
                        "digits": 5,
                        "description": getattr(sym, "description", ""),
                    }
            return None
        except Exception as exc:
            logger.warning("Failed to resolve symbol %s: %s", name, exc)
            return None
