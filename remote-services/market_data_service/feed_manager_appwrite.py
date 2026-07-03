"""Appwrite-native feed manager for the market data service.

Discovers active cTrader grants from Appwrite TablesDB, opens a single
environment-scoped connection (live or demo), and forwards ticks/bars/depth to
the same ingestion pipeline used by the legacy ``FeedManager``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from shared.appwrite_client import create_appwrite_client

from ctrader.account_discovery import AccountDiscovery, AccountRef
from ctrader.env_connection import EnvironmentConnection
from ctrader_client.market_data import BarClose, DepthUpdate, MarketDataManager, SpotTick

from .config import get_settings
from .models import TimeFrame

logger = logging.getLogger(__name__)

TickCallback = Callable[[SpotTick], Awaitable[None]]
BarCallback = Callable[[BarClose], Awaitable[None]]
DepthCallback = Callable[[DepthUpdate], Awaitable[None]]


class AppwriteFeedManager:
    """cTrader feed manager that authenticates through the Appwrite auth layer."""

    def __init__(
        self,
        symbol_registry: Any,
        on_tick: TickCallback,
        on_bar: BarCallback,
        on_depth: DepthCallback,
    ):
        self._registry = symbol_registry
        self._on_tick = on_tick
        self._on_bar = on_bar
        self._on_depth = on_depth

        self._env: EnvironmentConnection | None = None
        self._representative: AccountRef | None = None
        self._discovery: AccountDiscovery | None = None
        self._connected = False
        self._tasks: list[asyncio.Task] = []
        self._subscribed_spots: set[int] = set()
        self._subscribed_bars: dict[int, set[str]] = {}
        self._subscribed_depth: set[int] = set()
        self._last_tick_time: dict[int, float] = {}
        self._event_handlers_registered = False

    @property
    def is_connected(self) -> bool:
        return self._connected and self._env is not None

    @property
    def representative_account_id(self) -> int | None:
        return self._representative.ctid_trader_account_id if self._representative else None

    # ── Connection lifecycle ───────────────────────────────────────────────────

    async def connect(self) -> bool:
        if self._connected:
            return True

        settings = get_settings()
        if not settings.ctrader_use_appwrite_auth:
            logger.warning("Appwrite-native feed mode is not enabled")
            return False
        if not all(
            [
                settings.ctrader_auth_broker_url,
                settings.ctrader_internal_api_key,
                settings.ctrader_client_id,
                settings.ctrader_client_secret,
            ]
        ):
            logger.warning("Appwrite feed mode missing required credentials")
            return False

        client_id = settings.ctrader_client_id
        client_secret = settings.ctrader_client_secret
        internal_url = settings.ctrader_auth_broker_url
        internal_api_key = settings.ctrader_internal_api_key
        assert client_id is not None
        assert client_secret is not None
        assert internal_url is not None
        assert internal_api_key is not None

        database_id = settings.ctrader_auth_database_id or "ctrader_auth"
        appwrite_client, _ = create_appwrite_client(
            endpoint=settings.appwrite_endpoint,
            project_id=settings.appwrite_project_id,
            api_key=settings.appwrite_api_key,
        )

        self._discovery = AccountDiscovery(
            appwrite_client=appwrite_client,
            database_id=database_id,
            slave_accounts_table=settings.slave_accounts_table or "slave_accounts",
        )
        refs = self._discovery.discover()
        env_refs = [r for r in refs if r.is_live == settings.ctrader_use_live]
        if not env_refs:
            logger.error(
                "No active %s cTrader accounts found in Appwrite",
                "live" if settings.ctrader_use_live else "demo",
            )
            return False

        self._representative = self._pick_representative(env_refs, settings.ctrader_appwrite_username)
        logger.info(
            "Appwrite feed representative: grant=%s ctid=%s username=%s",
            self._representative.grant_id,
            self._representative.ctid_trader_account_id,
            self._representative.username,
        )

        self._env = EnvironmentConnection(
            is_live=settings.ctrader_use_live,
            client_id=client_id,
            client_secret=client_secret,
            internal_url=internal_url,
            internal_api_key=internal_api_key,
            transport_type=settings.ctrader_transport_type,
        )

        try:
            await self._env.start()
            await self._env.authorize_account(
                self._representative.grant_id,
                self._representative.ctid_trader_account_id,
            )
        except Exception as exc:
            logger.error("Appwrite feed connection failed: %s", exc)
            await self.disconnect()
            return False

        self._connected = True
        await self._sync_symbols_to_client()
        self._register_event_handlers()
        self._tasks.append(asyncio.create_task(self._watchdog()))

        logger.info(
            "Appwrite feed connected (%s)",
            "live" if settings.ctrader_use_live else "demo",
        )
        return True

    async def disconnect(self) -> None:
        self._connected = False
        if self._event_handlers_registered and self._env:
            try:
                self._env.event_bus.unsubscribe(SpotTick, self._on_tick_event)
                self._env.event_bus.unsubscribe(BarClose, self._on_bar_event)
                self._env.event_bus.unsubscribe(DepthUpdate, self._on_depth_event)
            except Exception as exc:
                logger.debug("Event bus unsubscribe error (non-critical): %s", exc)
        self._event_handlers_registered = False

        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        if self._env:
            try:
                await self._env.stop()
            except Exception as exc:
                logger.warning("Error stopping environment connection: %s", exc)
            self._env = None
        self._representative = None
        logger.info("Appwrite feed disconnected")

    # ── Discovery helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _pick_representative(refs: list[AccountRef], preferred_username: str | None) -> AccountRef:
        if preferred_username:
            for ref in refs:
                if ref.username and ref.username.lower() == preferred_username.lower():
                    return ref
        selected = [r for r in refs if r.selected]
        if selected:
            return selected[0]
        return refs[0]

    # ── Symbol synchronisation ─────────────────────────────────────────────────

    async def _sync_symbols_to_client(self) -> None:
        if not self._env:
            return
        for sym in self._registry.list_all():
            self._env.market_data.register_symbol(
                sym.symbol_id, sym.name, digits=sym.digits
            )
        try:
            active_ids = self._registry.active_symbol_ids()
            if active_ids and self._representative:
                client_known = set(self._env.market_data._symbols.keys())
                enrich_ids = [sid for sid in active_ids if sid in client_known]
                unknown_ids = set(active_ids) - client_known
                if unknown_ids:
                    logger.warning(
                        "Symbols not known to cTrader (skipping enrich): %s",
                        unknown_ids,
                    )
                if enrich_ids:
                    await self._env.market_data.enrich_symbols(
                        enrich_ids, self._representative.ctid_trader_account_id
                    )
                    for sid in enrich_ids:
                        client_info = self._env.market_data._symbols.get(sid)
                        if not client_info:
                            continue
                        sym = self._registry.get(sid)
                        if not sym:
                            continue
                        updates: dict[str, Any] = {}
                        if client_info.digits and sym.digits != client_info.digits:
                            updates["digits"] = client_info.digits
                        if client_info.min_volume and sym.min_volume != client_info.min_volume:
                            updates["min_volume"] = client_info.min_volume
                        if client_info.step_volume and sym.volume_step != client_info.step_volume:
                            updates["volume_step"] = client_info.step_volume
                        if client_info.lot_size and sym.lot_size != client_info.lot_size:
                            updates["lot_size"] = client_info.lot_size
                        if sym.pip_position is None and client_info.digits:
                            updates["pip_position"] = client_info.digits
                        if updates:
                            logger.info(
                                "Updating %s metadata from cTrader: %s",
                                sym.name, updates,
                            )
                            await self._registry.update(sid, **updates)
        except Exception as exc:
            logger.warning("Failed to enrich symbols from cTrader: %s", exc)

    # ── Event-bus integration ──────────────────────────────────────────────────

    def _register_event_handlers(self) -> None:
        if self._event_handlers_registered or not self._env:
            return
        self._env.event_bus.subscribe(SpotTick, self._on_tick_event)
        self._env.event_bus.subscribe(BarClose, self._on_bar_event)
        self._env.event_bus.subscribe(DepthUpdate, self._on_depth_event)
        self._event_handlers_registered = True

    async def _on_tick_event(self, tick: SpotTick) -> None:
        self._last_tick_time[tick.symbol_id] = time.time()
        await self._on_tick(tick)

    async def _on_bar_event(self, bar: BarClose) -> None:
        await self._on_bar(bar)

    async def _on_depth_event(self, depth: DepthUpdate) -> None:
        await self._on_depth(depth)

    # ── Subscriptions ──────────────────────────────────────────────────────────

    async def subscribe_spots(self, symbol_ids: list[int]) -> None:
        if not self._env or not self._connected or not self._representative:
            self._subscribed_spots.update(symbol_ids)
            return
        new_ids = [s for s in symbol_ids if s not in self._subscribed_spots]
        if new_ids:
            try:
                await self._env.market_data.subscribe_spots(
                    self._representative.ctid_trader_account_id, new_ids
                )
                self._subscribed_spots.update(new_ids)
                logger.info("Subscribed spots: %s", new_ids)
            except Exception as exc:
                logger.warning("Spot subscription failed: %s", exc)

    async def subscribe_bars(self, symbol_id: int, period: str) -> None:
        if not self._env or not self._connected or not self._representative:
            self._subscribed_bars.setdefault(symbol_id, set()).add(period)
            return
        if period not in self._subscribed_bars.get(symbol_id, set()):
            try:
                try:
                    tf = TimeFrame(period)
                    ctrader_period = tf.to_ctrader_period()
                except ValueError:
                    ctrader_period = period
                await self._env.market_data.subscribe_live_bars(
                    self._representative.ctid_trader_account_id, symbol_id, ctrader_period
                )
                self._subscribed_bars.setdefault(symbol_id, set()).add(period)
                logger.info("Subscribed bars: %s %s", symbol_id, period)
            except Exception as exc:
                logger.warning("Bar subscription failed: %s", exc)

    async def subscribe_depth(self, symbol_id: int) -> None:
        if not self._env or not self._connected or not self._representative:
            self._subscribed_depth.add(symbol_id)
            return
        if symbol_id not in self._subscribed_depth:
            try:
                await self._env.market_data.subscribe_depth(
                    self._representative.ctid_trader_account_id, symbol_id
                )
                self._subscribed_depth.add(symbol_id)
                logger.info("Subscribed depth: %s", symbol_id)
            except Exception as exc:
                logger.warning("Depth subscription failed: %s", exc)

    async def replay_subscriptions(self) -> None:
        if self._subscribed_spots:
            await self.subscribe_spots(list(self._subscribed_spots))
        for sid, periods in self._subscribed_bars.items():
            for period in periods:
                await self.subscribe_bars(sid, period)
        for sid in self._subscribed_depth:
            await self.subscribe_depth(sid)

    # ── Watchdog ───────────────────────────────────────────────────────────────

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

    # ── Historical data helpers ────────────────────────────────────────────────

    async def fetch_historical_bars(
        self, symbol_id: int, period: str, from_ms: int, to_ms: int
    ) -> list[BarClose]:
        if not self._env or not self._connected or not self._representative:
            return []
        try:
            try:
                tf = TimeFrame(period)
                ctrader_period = tf.to_ctrader_period()
            except ValueError:
                ctrader_period = period
            res = await self._env.market_data.get_historical_trendbars(
                self._representative.ctid_trader_account_id,
                symbol_id,
                ctrader_period,
                from_ms,
                to_ms,
            )
            bars = []
            sym = self._registry.get(symbol_id)
            client_sym = self._env.market_data._symbols.get(symbol_id)
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
        if not self._env or not self._connected or not self._representative:
            return []
        try:
            res = await self._env.market_data.get_historical_ticks(
                self._representative.ctid_trader_account_id,
                symbol_id,
                from_ms,
                to_ms,
            )
            ticks = []
            sym = self._registry.get(symbol_id)
            client_sym = self._env.market_data._symbols.get(symbol_id)
            digits = client_sym.digits if client_sym else (sym.digits if sym else 5)
            name = client_sym.name if client_sym else (sym.name if sym else str(symbol_id))
            for td in getattr(res, "tickData", []):
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

    async def get_available_symbols(self) -> list[dict[str, Any]]:
        if not self._env or not self._connected or not self._representative:
            return []
        try:
            res = await self._env.market_data.get_symbols(
                self._representative.ctid_trader_account_id
            )
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
        if not self._env or not self._connected or not self._representative:
            return None
        try:
            for sid, info in self._env.market_data._symbols.items():
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
            res = await self._env.market_data.get_symbols(
                self._representative.ctid_trader_account_id
            )
            for sym in getattr(res, "symbol", []):
                if getattr(sym, "symbolName", "").upper() == name.upper():
                    sid = getattr(sym, "symbolId", 0)
                    try:
                        await self._env.market_data.enrich_symbols(
                            [sid], self._representative.ctid_trader_account_id
                        )
                        enriched = self._env.market_data._symbols.get(sid)
                        if enriched:
                            return {
                                "symbol_id": sid,
                                "name": enriched.name,
                                "digits": enriched.digits,
                                "pip_size": enriched.pip_size,
                                "min_volume": enriched.min_volume,
                                "step_volume": enriched.step_volume,
                                "lot_size": enriched.lot_size,
                            }
                    except Exception:
                        pass
                    return {
                        "symbol_id": sid,
                        "name": getattr(sym, "symbolName", ""),
                        "digits": 5,
                    }
        except Exception as exc:
            logger.warning("Failed to resolve symbol %s: %s", name, exc)
        return None
