"""Session orchestrator — wires all layers together."""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Any

from .auth import TokenData, TokenManager
from .broker_auth import BrokerTokenManager
from .event_bus import AsyncEventBus
from .execution import ExecutionManager
from .market_data import (
    BarClose,
    DepthUpdate,
    ExecutionEvent,
    MarketDataManager,
    SpotTick,
)
from .protocol import CTraderProtocolClient
from .risk import RiskConfig, RiskManager
from .series import SeriesRegistry
from .transport import AsyncTcpTransport, AsyncWsTransport, BaseTransport

# Mapping of cTrader execution event type integers to readable names
_EXECUTION_TYPE_MAP: dict[int, str] = {}
_EXECUTION_TYPE_MAP_BUILT = False


def _get_execution_type_map() -> dict[int, str]:
    """Lazily populate the map from protobuf enum values."""
    global _EXECUTION_TYPE_MAP_BUILT
    if not _EXECUTION_TYPE_MAP_BUILT:
        from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
            ProtoOAExecutionType,
        )
        for name, value in ProtoOAExecutionType.items():
            _EXECUTION_TYPE_MAP[value] = name
        _EXECUTION_TYPE_MAP_BUILT = True
    return _EXECUTION_TYPE_MAP


def _guess_digits(name: str) -> int:
    """Estimate price digits from symbol name convention."""
    upper = name.upper()
    # Forex pairs (6 decimal => 5 digits for relative pricing)
    if len(upper) == 6 and not any(c.isdigit() for c in upper):
        return 5
    # Gold (XAUUSD) => 3 digits
    if "XAU" in upper or "XAG" in upper:
        return 3
    # Crypto (BTCUSD) => 2 digits
    if any(c in upper for c in ("BTC", "ETH")):
        return 2
    # Indices and others => 1 digit
    return 1

logger = logging.getLogger(__name__)

# ── Endpoints ──────────────────────────────────────────────────────────────

DEMO_HOST = "demo.ctraderapi.com"
LIVE_HOST = "live.ctraderapi.com"
PORT = 5035

DEMO_WS_URL = "wss://demo.ctraderapi.com:5036"
LIVE_WS_URL = "wss://live.ctraderapi.com:5036"


class TransportType(Enum):
    TCP = "tcp"
    WS = "ws"


class CTraderSession:
    """Top-level orchestrator that wires all layers together.

    Usage::

        session = CTraderSession(
            client_id="...",
            client_secret="...",
            access_token="...",
            refresh_token="...",
            account_id=12345678,
        )
        await session.start()
        await session.market_data.subscribe_spots(session.account_id, [1])
        ...
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        access_token: str,
        refresh_token: str,
        account_id: int,
        use_live: bool = False,
        transport_type: TransportType = TransportType.TCP,
        token_store_path: str | None = None,
        broker_url: str | None = None,
        grant_id: str | None = None,
        internal_api_key: str | None = None,
    ):
        self._use_live = use_live
        self._transport_type = transport_type
        self.account_id = account_id
        self._token_store_path = token_store_path
        self._broker_url = broker_url
        self._grant_id = grant_id
        self._internal_api_key = internal_api_key

        # Token management
        if broker_url is not None and grant_id is not None:
            self._token_manager = BrokerTokenManager(broker_url, grant_id, internal_api_key)
        elif token_store_path:
            loaded_mgr = TokenManager.load(token_store_path, client_id, client_secret)
            if loaded_mgr.has_tokens:
                self._token_manager = loaded_mgr
            else:
                self._token_manager = TokenManager(
                    client_id,
                    client_secret,
                    token_data=TokenData(
                        access_token=access_token,
                        refresh_token=refresh_token,
                        expires_at=0,
                    ),
                )
                self._token_manager.save(token_store_path)
        else:
            self._token_manager = TokenManager(
                client_id,
                client_secret,
                token_data=TokenData(
                    access_token=access_token,
                    refresh_token=refresh_token,
                    expires_at=0,  # will be set by server on auth
                ),
            )

        # Buses
        self.tick_bus: asyncio.Queue[SpotTick] = asyncio.Queue(maxsize=10_000)
        self.bar_bus: asyncio.Queue[BarClose] = asyncio.Queue(maxsize=5_000)
        self.depth_bus: asyncio.Queue[DepthUpdate] = asyncio.Queue(maxsize=5_000)

        # Series registry
        self.series_registry = SeriesRegistry()

        # Build transport
        if transport_type == TransportType.TCP:
            host = LIVE_HOST if use_live else DEMO_HOST
            self._transport: BaseTransport = AsyncTcpTransport(
                host, PORT, self._on_frame, on_reconnect=self.on_reconnect
            )
        else:
            url = LIVE_WS_URL if use_live else DEMO_WS_URL
            self._transport = AsyncWsTransport(url, self._on_frame)

        # Protocol
        self._protocol = CTraderProtocolClient(
            self._transport, client_id, client_secret
        )

        # Market data
        self.market_data = MarketDataManager(
            self._protocol, self.tick_bus, self.bar_bus, self.depth_bus
        )

        # Event bus (for strategy integration)
        self.event_bus = AsyncEventBus()

        # Execution + Risk (optional — created on demand)
        self._execution: ExecutionManager | None = None
        self._risk: RiskManager | None = None

        # Internal tasks
        self._tasks: list[asyncio.Task[None]] = []

        # Execution event buffer (always-on, so late subscribers get data)
        self._execution_events: list[ExecutionEvent] = []

    @property
    def transport(self) -> BaseTransport:
        return self._transport

    @property
    def protocol(self) -> CTraderProtocolClient:
        return self._protocol

    @property
    def token_manager(self) -> TokenManager | BrokerTokenManager:
        return self._token_manager

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Full startup: connect, authenticate, reconcile, pump buses."""
        # 1. Connect transport
        await self._transport.connect()
        logger.info("Transport connected")

        # 2. Start protocol (heartbeat)
        await self._protocol.start()

        # 3. Authenticate application
        await self._protocol.authenticate_app()
        logger.info("Application authenticated")

        # 4. Ensure we have a fresh access token before account-scoped requests
        await self._ensure_fresh_access_token()

        # 5. Optionally validate account visibility with the current token
        await self._validate_account_access()

        # 5b. Sync enriched account data to the auth broker (best-effort, non-blocking)
        self._sync_accounts_task = asyncio.create_task(self._sync_accounts_to_broker())

        # 6. Authorise account
        await self._protocol.authorize_account(
            self.account_id, self._token_manager.access_token
        )

        # 7. Reconcile (sync open positions/orders)
        await self._protocol.reconcile(self.account_id)
        logger.info("Account reconciled")

        # 8. Fetch symbols and register them
        await self._load_symbols()

        # 7. Start bus pumps
        self._tasks.append(asyncio.create_task(self._pump_bus(self.tick_bus)))
        self._tasks.append(asyncio.create_task(self._pump_bus(self.bar_bus)))
        self._tasks.append(asyncio.create_task(self._pump_bus(self.depth_bus)))

        # 7b. Subscribe to execution events from protocol layer
        self._execution_bus = asyncio.Queue(maxsize=10_000)
        self._tasks.append(asyncio.create_task(self._pump_execution_bus()))
        self._register_execution_event_handler()

        # 7c. Subscribe to trader-updated events (balance/equity changes)
        self._register_trader_updated_handler()

        # 7d. Subscribe to margin-changed events
        self._register_margin_changed_handler()

        # 8. Start token auto-refresh
        await self._token_manager.start_auto_refresh()

        # 9. Update token expiry from server response
        # (The auth response contains expires_in; we set a safe default)
        self._token_manager._token_data.expires_at = (
            time.time() + 2_628_000
        )

        logger.info("Session started for account %d", self.account_id)

    async def _ensure_fresh_access_token(self) -> None:
        """Refresh access token proactively before startup if needed.

        If refresh fails but the current token is still accepted by account
        authorization, startup can continue and account-scoped requests will
        determine whether the token is actually sufficient.
        """
        try:
            await self._token_manager.refresh()
            if self._token_store_path:
                self._token_manager.save(self._token_store_path)
            logger.info("Access token refreshed before startup")
        except Exception as exc:
            logger.warning("Pre-start token refresh failed: %s", exc)

    async def _validate_account_access(self) -> None:
        """Best-effort validation that the access token can see the account."""
        try:
            res = await self._protocol.get_accounts(self._token_manager.access_token)
            error_code = getattr(res, "errorCode", "")
            if error_code:
                description = getattr(res, "description", "")
                logger.warning(
                    "Account-list validation failed: %s (%s)",
                    error_code,
                    description or "no description",
                )
                return

            accounts = getattr(res, "ctidTraderAccount", None)
            if accounts is None:
                accounts = getattr(res, "account", None)
            if accounts is None:
                logger.debug("Account-list validation: no repeated account field on %s", type(res).__name__)
                return

            account_ids = {getattr(a, "ctidTraderAccountId", None) for a in list(accounts)}
            if self.account_id == 0 and len(account_ids) == 1:
                auto_id = next(iter(account_ids))
                if auto_id is not None:
                    self.account_id = auto_id
                    logger.info("Auto-selected account_id %d from account list", self.account_id)
                    return
            if self.account_id not in account_ids:
                logger.warning(
                    "Configured account_id %s not present in account list %s",
                    self.account_id,
                    sorted(a for a in account_ids if a is not None),
                )
        except Exception as exc:
            logger.warning("Account-list validation failed: %s", exc)

    async def _sync_accounts_to_broker(self) -> None:
        """Best-effort sync of enriched account data to the auth broker.

        Called as a background task during start() only when broker mode
        is active (broker_url + grant_id + internal_api_key are set).
        """
        if not (self._broker_url and self._grant_id and self._internal_api_key):
            return
        try:
            from .broker_auth import sync_accounts_to_broker

            await sync_accounts_to_broker(
                protocol=self._protocol,
                access_token=self._token_manager.access_token,
                broker_url=self._broker_url,
                grant_id=self._grant_id,
                internal_api_key=self._internal_api_key,
                selected_account_id=self.account_id,
            )
        except Exception as exc:
            logger.warning("_sync_accounts_to_broker failed: %s", exc)

    async def stop(self) -> None:
        """Clean shutdown."""
        await self._token_manager.stop_auto_refresh()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self._protocol.stop()
        await self._transport.close()
        logger.info("Session stopped")

    async def close(self) -> None:
        """Alias for stop."""
        await self.stop()

    # ── Symbol loading ─────────────────────────────────────────────────────

    async def _load_symbols(self) -> None:
        """Fetch symbol list and register all symbols.

        ProtoOASymbolsListRes is expected to expose repeated light symbols,
        but live environments may return an explicit ProtoOAErrorRes or a
        response object whose repeated field shape differs from `.symbol`.
        This method therefore validates the response before iterating it.
        """
        try:
            res = await self.market_data.get_symbols(self.account_id)

            error_code = getattr(res, "errorCode", "")
            if error_code:
                description = getattr(res, "description", "")
                logger.warning(
                    "Failed to load symbols: %s (%s)",
                    error_code,
                    description or "no description",
                )
                return

            raw_symbols = getattr(res, "symbol", None)
            if raw_symbols is None:
                raw_symbols = getattr(res, "symbols", None)
            if raw_symbols is None:
                fields = []
                if hasattr(res, "DESCRIPTOR") and getattr(res.DESCRIPTOR, "fields", None):
                    fields = [f.name for f in res.DESCRIPTOR.fields]
                logger.warning(
                    "Failed to load symbols: response has no symbol list field; type=%s fields=%s",
                    type(res).__name__,
                    fields,
                )
                return

            light_symbols = list(raw_symbols)
            for sym in light_symbols:
                name = getattr(sym, "symbolName", None) or getattr(sym, "name", None)
                symbol_id = getattr(sym, "symbolId", None) or getattr(sym, "symbol_id", None)
                if not name or symbol_id is None:
                    logger.debug("Skipping malformed symbol payload: %r", sym)
                    continue
                digits = _guess_digits(name)
                self.market_data.register_symbol(symbol_id, name, digits)
            logger.info("Loaded %d symbols (light)", len(light_symbols))

            # Enrich a bounded subset of symbols so startup stays fast while
            # avoiding hardcoded instrument assumptions.
            enrich_ids = [
                getattr(sym, "symbolId", None) or getattr(sym, "symbol_id", None)
                for sym in light_symbols[:200]
            ]
            enrich_ids = [sid for sid in enrich_ids if sid is not None]
            if enrich_ids:
                await self.market_data.enrich_symbols(enrich_ids, self.account_id)
                for sid in enrich_ids[:10]:
                    info = self.market_data._symbols[sid]
                    logger.info(
                        "Enriched %s: digits=%d, pip_size=%.7f",
                        info.name, info.digits, info.pip_size,
                    )
        except Exception as exc:
            logger.warning("Failed to load symbols: %s", exc)

    # ── Bus pumps ──────────────────────────────────────────────────────────

    async def _pump_bus(self, queue: asyncio.Queue) -> None:
        """Forward items from a raw queue to the event bus.

        Items dequeued from the transport-level queue are published to the
        AsyncEventBus for strategy consumption. Bars also update the
        SeriesRegistry automatically.
        """
        while True:
            item = await queue.get()

            # Update series registry for bar events
            if isinstance(item, BarClose):
                self.series_registry.update(
                    item.symbol_id, item.period,
                    {
                        "open": item.open,
                        "high": item.high,
                        "low": item.low,
                        "close": item.close,
                        "volume": item.volume,
                        "timestamp_ms": item.timestamp_ms,
                    },
                )

            # Publish to event bus
            await self.event_bus.publish(item)
            queue.task_done()

    # ── Execution event pump ───────────────────────────────────────────────

    async def _pump_execution_bus(self) -> None:
        """Forward execution events from the protocol layer to the event bus."""
        while True:
            event = await self._execution_bus.get()
            self._execution_events.append(event)
            await self.event_bus.publish(event)
            self._execution_bus.task_done()

    def _register_execution_event_handler(self) -> None:
        """Subscribe to raw protocol execution events and pump them to the bus."""
        from ctrader_open_api.messages.OpenApiMessages_pb2 import (
            ProtoOAExecutionEvent,
        )

        async def _on_execution(msg: Any) -> None:
            # Map numeric executionType to readable name
            exec_type = getattr(msg, "executionType", 0)
            type_map = _get_execution_type_map()
            type_name = type_map.get(exec_type, f"UNKNOWN_{exec_type}")
            order = msg.order if hasattr(msg, "order") and msg.order else None
            position = msg.position if hasattr(msg, "position") and msg.position else None
            error_code = getattr(msg, "errorCode", "")
            raw_vol = getattr(order, "volume", 0) if order else 0
            sym_id = getattr(order, "symbolId", None) if order else None
            if sym_id and sym_id in self.market_data._symbols:
                info = self.market_data._symbols[sym_id]
                event_volume = raw_vol / (info.lot_size * 100) if info.lot_size else raw_vol / 100
            else:
                event_volume = raw_vol / 100
            event = ExecutionEvent(
                event_type=type_name,
                order_id=getattr(order, "orderId", None),
                position_id=getattr(position, "positionId", None),
                symbol_id=sym_id,
                volume=event_volume,
                price=getattr(order, "price", 0.0),
                error_code=error_code if error_code else None,
                raw=msg,
            )
            await self._execution_bus.put(event)

        payload_type = ProtoOAExecutionEvent().payloadType
        self._protocol.subscribe(payload_type, _on_execution)

    @property
    def execution_events(self) -> list[ExecutionEvent]:
        """All execution events captured since session start."""
        return self._execution_events

    # ── Trader-updated event handler ────────────────────────────────────────

    def _register_trader_updated_handler(self) -> None:
        """Subscribe to ProtoOATraderUpdatedEvent for real-time balance changes."""
        from ctrader_open_api.messages.OpenApiMessages_pb2 import (
            ProtoOATraderUpdatedEvent,
        )

        async def _on_trader_updated(msg: Any) -> None:
            trader = getattr(msg, "trader", None)
            if trader:
                balance = getattr(trader, "balance", 0)
                money_digits = getattr(trader, "moneyDigits", 0) or 0
                logger.info(
                    "Trader updated: balance=%s (moneyDigits=%d)",
                    balance / (10 ** money_digits) if money_digits else balance / 100,
                    money_digits,
                )
            await self.event_bus.publish(msg)

        payload_type = ProtoOATraderUpdatedEvent().payloadType
        self._protocol.subscribe(payload_type, _on_trader_updated)

    # ── Margin-changed event handler ───────────────────────────────────────

    def _register_margin_changed_handler(self) -> None:
        """Subscribe to ProtoOAMarginChangedEvent for margin updates."""
        from ctrader_open_api.messages.OpenApiMessages_pb2 import (
            ProtoOAMarginChangedEvent,
        )

        async def _on_margin_changed(msg: Any) -> None:
            position_id = getattr(msg, "positionId", 0)
            used_margin = getattr(msg, "usedMargin", 0)
            money_digits = getattr(msg, "moneyDigits", 0) or 0
            logger.info(
                "Margin changed: position=%d usedMargin=%s",
                position_id,
                used_margin / (10 ** money_digits) if money_digits else used_margin / 100,
            )
            await self.event_bus.publish(msg)

        payload_type = ProtoOAMarginChangedEvent().payloadType
        self._protocol.subscribe(payload_type, _on_margin_changed)

    # ── Frame handler (transport callback) ─────────────────────────────────

    async def _on_frame(self, raw: bytes) -> None:
        """Called by transport when a raw frame arrives."""
        await self._protocol.on_frame(raw)

    # ── Reconnect hook ─────────────────────────────────────────────────────

    async def on_reconnect(self) -> None:
        """Called after transport reconnects — re-auth and replay subs."""
        await self._protocol.authenticate_app()
        await self._protocol.authorize_account(
            self.account_id, self._token_manager.access_token
        )
        await self._protocol.reconcile(self.account_id)
        await self.market_data.replay_subscriptions(self.account_id)
        logger.info("Reconnected and re-subscribed")

    # ── Execution + Risk helpers ───────────────────────────────────────────

    @property
    def execution(self) -> ExecutionManager:
        """Lazy-create the ExecutionManager."""
        if self._execution is None:
            self._execution = ExecutionManager(self._protocol, self.market_data)
        return self._execution

    @property
    def risk(self) -> RiskManager:
        """Lazy-create the RiskManager with default config."""
        if self._risk is None:
            self._risk = RiskManager(RiskConfig())
        return self._risk
