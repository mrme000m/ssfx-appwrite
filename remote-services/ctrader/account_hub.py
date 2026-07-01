"""Account Hub — persistent cTrader connections for ALL authenticated slave accounts.

On startup, reads all active slave_accounts from Appwrite TablesDB and creates
persistent cTrader TCP/WS connections for each (both demo and live). Subscribes
to execution events, trader updates, margin changes, and balance events. Fans
out real-time data to WebSocket clients via a pub/sub event system.

Architecture:
    Appwrite TablesDB (slave_accounts)
          |
    AccountHub.start() — reads all active accounts
          |
    Per-account CTraderSession (TCP to ctraderapi.com)
          |
    Event bus (asyncio.Queue per subscription)
          |
    WebSocket clients (dashboard, command center, dataservice)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from appwrite.client import Client
from appwrite.services.tables_db import TablesDB

from ctrader_client import CTraderSession, TransportType
from ctrader_client.appwrite_auth import AppwriteMultiTokenClient, AppwriteTokenManager
from ctrader_client.broker_auth import sync_accounts_to_broker
from ctrader_client.market_data import ExecutionEvent

logger = logging.getLogger(__name__)


class AccountConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


@dataclass
class AccountInfo:
    grant_id: str
    username: str | None
    role: str
    status: str
    ctid_trader_account_id: int
    is_live: bool
    appwrite_user_id: str | None


@dataclass
class AccountConnection:
    info: AccountInfo
    session: CTraderSession | None = None
    state: AccountConnectionState = AccountConnectionState.DISCONNECTED
    last_event_at: float = 0.0
    error_count: int = 0
    positions: list[dict[str, Any]] = field(default_factory=list)
    balance: float = 0.0
    equity: float = 0.0
    margin: float = 0.0
    tasks: list[asyncio.Task] = field(default_factory=list)


class AccountHub:
    """Manages persistent cTrader connections and fans out events."""

    def __init__(
        self,
        appwrite_client: Client,
        database_id: str,
        slave_accounts_table: str,
        internal_url: str,
        internal_api_key: str,
        client_id: str,
        client_secret: str,
        poll_interval: float = 30.0,
        reconnect_base: float = 5.0,
        reconnect_max: float = 60.0,
    ):
        self._appwrite = appwrite_client
        self._tables = TablesDB(appwrite_client)
        self._database_id = database_id
        self._slave_accounts_table = slave_accounts_table
        self._internal_url = internal_url.rstrip("/")
        self._internal_api_key = internal_api_key
        self._client_id = client_id
        self._client_secret = client_secret
        self._poll_interval = poll_interval
        self._reconnect_base = reconnect_base
        self._reconnect_max = reconnect_max

        self._token_client = AppwriteMultiTokenClient(internal_url, internal_api_key)
        self._connections: dict[str, AccountConnection] = {}
        self._lock = asyncio.Lock()
        self._running = False
        self._poll_task: asyncio.Task | None = None

        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=10_000)
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1_000)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    async def _broadcast(self, event: dict[str, Any]) -> None:
        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            pass
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                self.unsubscribe(q)

    async def start(self) -> None:
        self._running = True
        logger.info("AccountHub starting")
        await self._sync_accounts()
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        await self._disconnect_all()
        logger.info("AccountHub stopped")

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self._sync_accounts()
            except Exception as exc:
                logger.error("Poll loop error: %s", exc)
            await asyncio.sleep(self._poll_interval)

    async def _sync_accounts(self) -> None:
        try:
            result = self._tables.list_rows(
                database_id=self._database_id,
                table_id=self._slave_accounts_table,
            )
        except Exception as exc:
            logger.error("Failed to list slave_accounts: %s", exc)
            return

        active_grants: set[str] = set()
        found_count = 0

        for row in result.rows:
            if hasattr(row, "data"):
                data = row.data
            elif hasattr(row, "model_dump"):
                dumped = row.model_dump()
                data = dumped.get("data", dumped)
            else:
                data = dict(row).get("data", dict(row))
            grant_id = data.get("grant_id", "")
            status = data.get("status", "")
            username = data.get("username")
            role = data.get("role", "slave")
            appwrite_user_id = data.get("appwrite_user_id")
            ctrader_ids_raw = data.get("ctrader_account_ids", "")

            if not grant_id or status != "active":
                continue

            account_ids: list[int] = []
            if ctrader_ids_raw:
                for part in str(ctrader_ids_raw).split(","):
                    part = part.strip()
                    if part:
                        try:
                            account_ids.append(int(part))
                        except ValueError:
                            continue

            if not account_ids:
                continue

            found_count += 1

            for ctid in account_ids:
                key_demo = f"{grant_id}:{ctid}:demo"
                key_live = f"{grant_id}:{ctid}:live"
                active_grants.add(key_demo)
                active_grants.add(key_live)

                info_demo = AccountInfo(
                    grant_id=grant_id,
                    username=username,
                    role=role,
                    status=status,
                    ctid_trader_account_id=ctid,
                    is_live=False,
                    appwrite_user_id=appwrite_user_id,
                )
                info_live = AccountInfo(
                    grant_id=grant_id,
                    username=username,
                    role=role,
                    status=status,
                    ctid_trader_account_id=ctid,
                    is_live=True,
                    appwrite_user_id=appwrite_user_id,
                )

                async with self._lock:
                    if key_demo not in self._connections:
                        conn = AccountConnection(info=info_demo)
                        self._connections[key_demo] = conn
                        asyncio.create_task(self._manage_connection(key_demo, conn))

                    if key_live not in self._connections:
                        conn = AccountConnection(info=info_live)
                        self._connections[key_live] = conn
                        asyncio.create_task(self._manage_connection(key_live, conn))

        if found_count > 0:
            logger.info("AccountHub discovered %d active accounts (creating %d connections)",
                        found_count, found_count * 2)

        async with self._lock:
            stale = set(self._connections.keys()) - active_grants
            for key in stale:
                conn = self._connections.pop(key, None)
                if conn and conn.session:
                    logger.info("Removing stale connection: %s", key)
                    asyncio.create_task(self._stop_connection(conn))

    async def _manage_connection(self, key: str, conn: AccountConnection) -> None:
        reconnect_delay = self._reconnect_base
        while self._running and key in self._connections:
            try:
                await self._connect_account(conn)
                reconnect_delay = self._reconnect_base
                await self._connection_monitor(key, conn)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(
                    "Connection error for %s (grant=%s ctid=%s live=%s): %s",
                    key, conn.info.grant_id, conn.info.ctid_trader_account_id,
                    conn.info.is_live, exc,
                )
                conn.state = AccountConnectionState.RECONNECTING
                conn.error_count += 1
                await self._broadcast({
                    "type": "connection_state",
                    "grant_id": conn.info.grant_id,
                    "ctid": conn.info.ctid_trader_account_id,
                    "is_live": conn.info.is_live,
                    "state": "reconnecting",
                    "error": str(ex),
                    "ts": datetime.now(UTC).isoformat(),
                })

            await self._stop_connection(conn)
            if not self._running or key not in self._connections:
                break
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, self._reconnect_max)

    async def _connect_account(self, conn: AccountConnection) -> None:
        conn.state = AccountConnectionState.CONNECTING
        info = conn.info

        token_mgr = AppwriteTokenManager(
            internal_url=self._internal_url,
            grant_id=info.grant_id,
            internal_api_key=self._internal_api_key,
        )
        await token_mgr.refresh()

        session = CTraderSession(
            client_id=self._client_id,
            client_secret=self._client_secret,
            access_token=token_mgr.access_token,
            refresh_token="",
            account_id=info.ctid_trader_account_id,
            use_live=info.is_live,
            transport_type=TransportType.TCP,
            broker_url=self._internal_url,
            grant_id=info.grant_id,
            internal_api_key=self._internal_api_key,
        )
        session._token_manager = token_mgr

        await session.start()

        await self._start_event_pumps(conn, session)
        await session._sync_accounts_to_broker()

        conn.session = session
        conn.state = AccountConnectionState.CONNECTED
        conn.error_count = 0
        conn.last_event_at = time.time()

        await self._broadcast({
            "type": "connection_state",
            "grant_id": info.grant_id,
            "ctid": info.ctid_trader_account_id,
            "is_live": info.is_live,
            "state": "connected",
            "username": info.username,
            "ts": datetime.now(UTC).isoformat(),
        })

        logger.info(
            "AccountHub connected: grant=%s ctid=%s live=%s username=%s",
            info.grant_id, info.ctid_trader_account_id, info.is_live, info.username,
        )

    async def _start_event_pumps(self, conn: AccountConnection, session: CTraderSession) -> None:
        async def pump_execution() -> None:
            while self._running and session._transport.is_connected:
                try:
                    event = await asyncio.wait_for(
                        session._execution_events_queue.get(), timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
                await self._handle_execution_event(conn, event)

        async def pump_generic(queue_name: str) -> None:
            q = getattr(session, queue_name, None)
            if q is None:
                return
            while self._running and session._transport.is_connected:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
                await self._broadcast({
                    "type": queue_name.replace("_bus", ""),
                    "grant_id": conn.info.grant_id,
                    "ctid": conn.info.ctid_trader_account_id,
                    "is_live": conn.info.is_live,
                    "username": conn.info.username,
                    "data": str(event),
                    "ts": datetime.now(UTC).isoformat(),
                })
                conn.last_event_at = time.time()

        conn.tasks = [
            asyncio.create_task(pump_execution(), name=f"pump-exec-{conn.info.grant_id}"),
        ]
        for qname in ["tick_bus", "bar_bus", "depth_bus"]:
            conn.tasks.append(
                asyncio.create_task(
                    pump_generic(qname),
                    name=f"pump-{qname}-{conn.info.grant_id}",
                )
            )

    async def _handle_execution_event(self, conn: AccountConnection, event: ExecutionEvent) -> None:
        payload = {
            "event_type": event.event_type,
            "order_id": event.order_id,
            "position_id": event.position_id,
            "symbol_id": event.symbol_id,
            "volume": event.volume,
            "price": event.price,
            "error_code": event.error_code,
        }
        await self._broadcast({
            "type": "execution_event",
            "grant_id": conn.info.grant_id,
            "ctid": conn.info.ctid_trader_account_id,
            "is_live": conn.info.is_live,
            "username": conn.info.username,
            "payload": payload,
            "ts": datetime.now(UTC).isoformat(),
        })
        conn.last_event_at = time.time()

    async def _connection_monitor(self, key: str, conn: AccountConnection) -> None:
        while self._running and key in self._connections:
            if conn.session is None or not conn.session._transport.is_connected:
                logger.warning("Connection lost for %s", key)
                break
            await asyncio.sleep(5)

    async def _stop_connection(self, conn: AccountConnection) -> None:
        for task in conn.tasks:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        conn.tasks.clear()

        if conn.session:
            try:
                await conn.session.stop()
            except Exception as exc:
                logger.warning("Error stopping session: %s", exc)
            conn.session = None

        conn.state = AccountConnectionState.DISCONNECTED

    async def _disconnect_all(self) -> None:
        async with self._lock:
            for conn in list(self._connections.values()):
                await self._stop_connection(conn)
            self._connections.clear()

    async def get_accounts(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        async with self._lock:
            for key, conn in self._connections.items():
                result.append({
                    "key": key,
                    "grant_id": conn.info.grant_id,
                    "username": conn.info.username,
                    "ctid": conn.info.ctid_trader_account_id,
                    "is_live": conn.info.is_live,
                    "state": conn.state.value,
                    "error_count": conn.error_count,
                    "balance": conn.balance,
                    "equity": conn.equity,
                    "margin": conn.margin,
                    "last_event_at": datetime.fromtimestamp(
                        conn.last_event_at, tz=UTC
                    ).isoformat() if conn.last_event_at else None,
                })
        return result

    async def get_account_state(
        self, grant_id: str, ctid: int, is_live: bool = False
    ) -> dict[str, Any] | None:
        key = f"{grant_id}:{ctid}:{'live' if is_live else 'demo'}"
        async with self._lock:
            conn = self._connections.get(key)
            if conn is None:
                return None
            return {
                "grant_id": conn.info.grant_id,
                "username": conn.info.username,
                "ctid": conn.info.ctid_trader_account_id,
                "is_live": conn.info.is_live,
                "state": conn.state.value,
                "error_count": conn.error_count,
                "balance": conn.balance,
                "equity": conn.equity,
                "margin": conn.margin,
                "positions": conn.positions,
                "last_event_at": datetime.fromtimestamp(
                    conn.last_event_at, tz=UTC
                ).isoformat() if conn.last_event_at else None,
            }

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    @property
    def connected_count(self) -> int:
        return sum(
            1 for c in self._connections.values()
            if c.state == AccountConnectionState.CONNECTED
        )
