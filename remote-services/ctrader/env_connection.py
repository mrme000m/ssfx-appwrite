"""Environment-scoped cTrader transport — one connection per environment (live/demo).

Multiple ``ctidTraderAccountId`` accounts are authorized on the same underlying
TCP/WS transport via ``ProtoOAAccountAuthReq``.  Account events (execution,
trader, margin, reconcile) are normalised and published to an ``AsyncEventBus``
as ``AccountEvent`` envelopes so downstream consumers know which grant/account
produced them.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from ctrader_client.appwrite_auth import AppwriteMultiTokenClient
from ctrader_client.event_bus import AsyncEventBus
from ctrader_client.market_data import (
    BarClose,
    DepthUpdate,
    ExecutionEvent,
    MarketDataManager,
    SpotTick,
)
from ctrader_client.protocol import CTraderProtocolClient
from ctrader_client.transport import AsyncTcpTransport, AsyncWsTransport, BaseTransport

logger = logging.getLogger(__name__)

DEMO_HOST = "demo.ctraderapi.com"
LIVE_HOST = "live.ctraderapi.com"
PORT = 5035
DEMO_WS_URL = "wss://demo.ctraderapi.com:5036"
LIVE_WS_URL = "wss://live.ctraderapi.com:5036"


@dataclass(slots=True)
class AccountAuthState:
    """Runtime authorisation state for one cTrader account on this transport."""

    grant_id: str
    ctid_trader_account_id: int
    is_live: bool
    authorized_at: float = field(default_factory=time.time)
    last_token_refresh: float | None = None


@dataclass(slots=True)
class AccountEvent:
    """Normalised account event emitted by ``EnvironmentConnection``."""

    event_type: str
    grant_id: str
    ctid_trader_account_id: int
    is_live: bool
    timestamp_ms: int
    payload: dict[str, Any]
    raw: Any | None = None


class EnvironmentConnection:
    """One cTrader transport per environment with multi-account authorisation.

    Args:
        is_live: ``True`` for live environment, ``False`` for demo.
        client_id: cTrader OAuth client ID.
        client_secret: cTrader OAuth client secret.
        internal_url: Base URL of the ``ctrader-internal`` Appwrite Function.
        internal_api_key: Secret key for ``ctrader-internal``.
        transport_type: ``"tcp"`` or ``"ws"``.
    """

    def __init__(
        self,
        is_live: bool,
        client_id: str,
        client_secret: str,
        internal_url: str,
        internal_api_key: str,
        transport_type: str = "tcp",
    ):
        self._is_live = is_live
        self._client_id = client_id
        self._client_secret = client_secret
        self._internal_url = internal_url.rstrip("/")
        self._internal_api_key = internal_api_key
        self._transport_type = transport_type.lower()

        self._token_client = AppwriteMultiTokenClient(internal_url, internal_api_key)

        # Account state keyed by ctidTraderAccountId
        self._authorized_accounts: dict[int, AccountAuthState] = {}
        self._lock = asyncio.Lock()
        self._symbols_loaded = False

        # Buses for market-data events
        self._tick_bus: asyncio.Queue[SpotTick] = asyncio.Queue(maxsize=10_000)
        self._bar_bus: asyncio.Queue[BarClose] = asyncio.Queue(maxsize=5_000)
        self._depth_bus: asyncio.Queue[DepthUpdate] = asyncio.Queue(maxsize=5_000)

        # Public event bus for downstream consumers
        self.event_bus = AsyncEventBus()

        # Transport + protocol
        self._transport = self._build_transport()
        self._protocol = CTraderProtocolClient(
            self._transport, client_id, client_secret
        )
        self._market_data = MarketDataManager(
            self._protocol, self._tick_bus, self._bar_bus, self._depth_bus
        )

        # Internal tasks
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self._refresh_task: asyncio.Task | None = None
        self._ready_event = asyncio.Event()

    @property
    def is_live(self) -> bool:
        return self._is_live

    @property
    def ready_event(self) -> asyncio.Event:
        return self._ready_event

    @property
    def transport(self) -> BaseTransport:
        return self._transport

    @property
    def protocol(self) -> CTraderProtocolClient:
        return self._protocol

    @property
    def market_data(self) -> MarketDataManager:
        return self._market_data

    @property
    def authorized_account_ids(self) -> list[int]:
        return list(self._authorized_accounts.keys())

    def _build_transport(self) -> BaseTransport:
        if self._transport_type == "ws":
            url = LIVE_WS_URL if self._is_live else DEMO_WS_URL
            return AsyncWsTransport(url, self._on_frame)
        host = LIVE_HOST if self._is_live else DEMO_HOST
        return AsyncTcpTransport(
            host, PORT, self._on_frame, on_reconnect=self._on_reconnect
        )

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Connect transport, authenticate application, and start bus pumps."""
        if self._running:
            return
        self._running = True

        await self._transport.connect()
        await self._protocol.start()
        await self._protocol.authenticate_app()
        self._ready_event.set()
        logger.info("Environment %s application authenticated", self._env_label())

        self._register_account_event_handlers()
        self._start_bus_pumps()
        self._refresh_task = asyncio.create_task(self._token_refresh_loop())

    async def stop(self) -> None:
        """Clean shutdown."""
        self._running = False
        if self._refresh_task:
            self._refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._refresh_task
            self._refresh_task = None

        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        await self._protocol.stop()
        await self._transport.close()
        logger.info("Environment %s stopped", self._env_label())

    # ── Account authorisation ──────────────────────────────────────────────────

    async def authorize_account(self, grant_id: str, ctid: int) -> Any:
        """Authorise a single account on the shared transport."""
        async with self._lock:
            if ctid in self._authorized_accounts:
                logger.debug("Account %d already authorised on %s", ctid, self._env_label())
                return self._authorized_accounts[ctid]

        access_token = await self._token_client.refresh(grant_id)

        result = await self._protocol.authorize_account(ctid, access_token)
        error_code = getattr(result, "errorCode", "")
        if error_code:
            description = getattr(result, "description", "")
            raise RuntimeError(
                f"Account auth failed for {ctid} on {self._env_label()}: "
                f"{error_code} ({description or 'no description'})"
            )

        await self._protocol.reconcile(ctid)

        async with self._lock:
            state = AccountAuthState(
                grant_id=grant_id,
                ctid_trader_account_id=ctid,
                is_live=self._is_live,
                last_token_refresh=time.time(),
            )
            self._authorized_accounts[ctid] = state

        logger.info(
            "Authorised account %d (grant=%s) on %s",
            ctid, grant_id, self._env_label(),
        )

        if not self._symbols_loaded:
            await self._load_symbols(ctid)

        return result

    async def remove_account(self, ctid: int) -> None:
        """Logout and forget an account."""
        async with self._lock:
            state = self._authorized_accounts.pop(ctid, None)
        if state is None:
            return
        try:
            await self._protocol.logout(ctid)
            logger.info("Logged out account %d from %s", ctid, self._env_label())
        except Exception as exc:
            logger.warning("Logout failed for account %d: %s", ctid, exc)

    async def get_account_state(self, ctid: int) -> AccountAuthState | None:
        return self._authorized_accounts.get(ctid)

    # ── Token refresh ──────────────────────────────────────────────────────────

    async def _token_refresh_loop(self) -> None:
        """Proactively refresh tokens for active grants before expiry."""
        while self._running:
            try:
                await asyncio.sleep(60)
                if not self._authorized_accounts:
                    continue
                grants = {
                    state.grant_id: state.ctid_trader_account_id
                    for state in self._authorized_accounts.values()
                }
                for grant_id in grants:
                    try:
                        await self._token_client.refresh(grant_id, buffer_seconds=600)
                        async with self._lock:
                            for state in self._authorized_accounts.values():
                                if state.grant_id == grant_id:
                                    state.last_token_refresh = time.time()
                    except Exception as exc:
                        logger.warning(
                            "Token refresh failed for grant %s on %s: %s",
                            grant_id, self._env_label(), exc,
                        )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Token refresh loop error on %s: %s", self._env_label(), exc)

    # ── Reconnect handling ─────────────────────────────────────────────────────

    async def _on_reconnect(self) -> None:
        """Re-auth app and all known accounts after transport reconnect."""
        self._ready_event.clear()
        logger.info("Reconnecting %s environment...", self._env_label())
        try:
            await self._protocol.authenticate_app()
        except Exception as exc:
            logger.error("App re-auth failed on %s: %s", self._env_label(), exc)
            return

        accounts = list(self._authorized_accounts.values())
        for state in accounts:
            try:
                access_token = await self._token_client.refresh(state.grant_id)
                await self._protocol.authorize_account(
                    state.ctid_trader_account_id, access_token
                )
                await self._protocol.reconcile(state.ctid_trader_account_id)
                logger.info(
                    "Re-authorised account %d on %s",
                    state.ctid_trader_account_id, self._env_label(),
                )
            except Exception as exc:
                logger.error(
                    "Re-authorisation failed for account %d on %s: %s",
                    state.ctid_trader_account_id, self._env_label(), exc,
                )

        if accounts:
            representative = accounts[0].ctid_trader_account_id
            try:
                await self._market_data.replay_subscriptions(representative)
            except Exception as exc:
                logger.warning("Subscription replay failed on %s: %s", self._env_label(), exc)

        self._ready_event.set()

        await self.event_bus.publish(
            AccountEvent(
                event_type="connection_state",
                grant_id="",
                ctid_trader_account_id=0,
                is_live=self._is_live,
                timestamp_ms=int(time.time() * 1000),
                payload={"state": "reconnected", "env": self._env_label()},
            )
        )

    # ── Frame / market-data pumps ──────────────────────────────────────────────

    async def _on_frame(self, raw: bytes) -> None:
        await self._protocol.on_frame(raw)

    def _start_bus_pumps(self) -> None:
        self._tasks.append(asyncio.create_task(self._pump_queue(self._tick_bus)))
        self._tasks.append(asyncio.create_task(self._pump_queue(self._bar_bus)))
        self._tasks.append(asyncio.create_task(self._pump_queue(self._depth_bus)))

    async def _pump_queue(self, queue: asyncio.Queue[Any]) -> None:
        while self._running:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            try:
                await self.event_bus.publish(item)
            except Exception as exc:
                logger.warning("Event bus publish failed on %s: %s", self._env_label(), exc)
            finally:
                queue.task_done()

    # ── Account event handlers ─────────────────────────────────────────────────

    def _register_account_event_handlers(self) -> None:
        self._register_execution_handler()
        self._register_trader_updated_handler()
        self._register_margin_changed_handler()
        self._register_reconcile_handler()

    def _register_execution_handler(self) -> None:
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAExecutionEvent
        from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOAExecutionType

        type_map = {v: k for k, v in ProtoOAExecutionType.items()}

        async def _on_execution(msg: Any) -> None:
            ctid = getattr(msg, "ctidTraderAccountId", 0)
            state = self._authorized_accounts.get(ctid)
            grant_id = state.grant_id if state else ""
            exec_type = type_map.get(
                getattr(msg, "executionType", 0), "UNKNOWN"
            )
            order = msg.order if hasattr(msg, "order") and msg.order else None
            position = msg.position if hasattr(msg, "position") and msg.position else None
            sym_id = getattr(order, "symbolId", None) if order else None
            info = self._market_data._symbols.get(sym_id) if sym_id else None
            raw_vol = getattr(order, "volume", 0) if order else 0
            if info and info.lot_size:
                volume = raw_vol / (info.lot_size * 100)
            else:
                volume = raw_vol / 100

            event = ExecutionEvent(
                event_type=exec_type,
                order_id=getattr(order, "orderId", None),
                position_id=getattr(position, "positionId", None),
                symbol_id=sym_id,
                volume=volume,
                price=getattr(order, "price", 0.0),
                error_code=getattr(msg, "errorCode", None) or None,
                raw=msg,
            )
            await self.event_bus.publish(
                AccountEvent(
                    event_type="execution_event",
                    grant_id=grant_id,
                    ctid_trader_account_id=ctid,
                    is_live=self._is_live,
                    timestamp_ms=int(time.time() * 1000),
                    payload={
                        "event_type": event.event_type,
                        "order_id": event.order_id,
                        "position_id": event.position_id,
                        "symbol_id": event.symbol_id,
                        "volume": event.volume,
                        "price": event.price,
                        "error_code": event.error_code,
                    },
                    raw=msg,
                )
            )

        self._protocol.subscribe(ProtoOAExecutionEvent().payloadType, _on_execution)

    def _register_trader_updated_handler(self) -> None:
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOATraderUpdatedEvent

        async def _on_trader_updated(msg: Any) -> None:
            ctid = getattr(msg, "ctidTraderAccountId", 0)
            state = self._authorized_accounts.get(ctid)
            grant_id = state.grant_id if state else ""
            trader = getattr(msg, "trader", None)
            payload: dict[str, Any] = {}
            if trader:
                money_digits = getattr(trader, "moneyDigits", 0) or 0
                divisor = 10 ** money_digits if money_digits else 100
                payload = {
                    "balance": getattr(trader, "balance", 0) / divisor,
                    "equity": getattr(trader, "equity", 0) / divisor,
                    "money_digits": money_digits,
                }
                logger.info(
                    "Trader updated on %s account %d: balance=%s equity=%s",
                    self._env_label(), ctid, payload["balance"], payload["equity"],
                )
            await self.event_bus.publish(
                AccountEvent(
                    event_type="trader_updated",
                    grant_id=grant_id,
                    ctid_trader_account_id=ctid,
                    is_live=self._is_live,
                    timestamp_ms=int(time.time() * 1000),
                    payload=payload,
                    raw=msg,
                )
            )

        self._protocol.subscribe(ProtoOATraderUpdatedEvent().payloadType, _on_trader_updated)

    def _register_margin_changed_handler(self) -> None:
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAMarginChangedEvent

        async def _on_margin_changed(msg: Any) -> None:
            ctid = getattr(msg, "ctidTraderAccountId", 0)
            state = self._authorized_accounts.get(ctid)
            grant_id = state.grant_id if state else ""
            money_digits = getattr(msg, "moneyDigits", 0) or 0
            divisor = 10 ** money_digits if money_digits else 100
            payload = {
                "position_id": getattr(msg, "positionId", 0),
                "used_margin": getattr(msg, "usedMargin", 0) / divisor,
                "money_digits": money_digits,
            }
            await self.event_bus.publish(
                AccountEvent(
                    event_type="margin_changed",
                    grant_id=grant_id,
                    ctid_trader_account_id=ctid,
                    is_live=self._is_live,
                    timestamp_ms=int(time.time() * 1000),
                    payload=payload,
                    raw=msg,
                )
            )

        self._protocol.subscribe(ProtoOAMarginChangedEvent().payloadType, _on_margin_changed)

    def _register_reconcile_handler(self) -> None:
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAReconcileRes
        from google.protobuf.json_format import MessageToDict

        async def _on_reconcile(msg: Any) -> None:
            ctid = getattr(msg, "ctidTraderAccountId", 0)
            state = self._authorized_accounts.get(ctid)
            grant_id = state.grant_id if state else ""
            payload = {
                "position_count": len(getattr(msg, "position", [])),
                "order_count": len(getattr(msg, "order", [])),
                "positions": [MessageToDict(p, preserving_proto_field_name=True) for p in getattr(msg, "position", [])],
                "orders": [MessageToDict(o, preserving_proto_field_name=True) for o in getattr(msg, "order", [])],
            }
            await self.event_bus.publish(
                AccountEvent(
                    event_type="reconcile",
                    grant_id=grant_id,
                    ctid_trader_account_id=ctid,
                    is_live=self._is_live,
                    timestamp_ms=int(time.time() * 1000),
                    payload=payload,
                    raw=msg,
                )
            )

        self._protocol.subscribe(ProtoOAReconcileRes().payloadType, _on_reconcile)

    # ── Symbol loading ─────────────────────────────────────────────────────────

    async def _load_symbols(self, account_id: int) -> None:
        """Fetch and register symbol metadata using the first authorised account."""
        try:
            res = await self._market_data.get_symbols(account_id)
            error_code = getattr(res, "errorCode", "")
            if error_code:
                logger.warning(
                    "Failed to load symbols on %s: %s (%s)",
                    self._env_label(), error_code, getattr(res, "description", "")
                )
                return

            raw_symbols = getattr(res, "symbol", None) or getattr(res, "symbols", None)
            if raw_symbols is None:
                logger.warning("Symbol list response missing symbol field on %s", self._env_label())
                return

            for sym in raw_symbols:
                name = getattr(sym, "symbolName", None) or getattr(sym, "name", None)
                sid = getattr(sym, "symbolId", None) or getattr(sym, "symbol_id", None)
                if not name or sid is None:
                    continue
                self._market_data.register_symbol(sid, name, digits=self._guess_digits(name))

            logger.info(
                "Loaded %d symbols on %s", len(list(raw_symbols)), self._env_label()
            )
            self._symbols_loaded = True
        except Exception as exc:
            logger.warning("Symbol loading failed on %s: %s", self._env_label(), exc)

    @staticmethod
    def _guess_digits(name: str) -> int:
        upper = name.upper()
        if len(upper) == 6 and not any(c.isdigit() for c in upper):
            return 5
        if "XAU" in upper or "XAG" in upper:
            return 3
        if any(c in upper for c in ("BTC", "ETH")):
            return 2
        return 1

    def _env_label(self) -> str:
        return "live" if self._is_live else "demo"
