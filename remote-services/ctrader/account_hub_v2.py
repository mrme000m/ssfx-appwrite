"""Account Hub v2 — one transport per environment, multi-account auth.

Discovers active slave accounts from Appwrite TablesDB, maintains exactly two
cTrader transports (live + demo), persists account events to Appwrite, and fans
out real-time data to WebSocket subscribers.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from appwrite.client import Client

from ctrader.account_discovery import AccountDiscovery, AccountRef
from ctrader.account_events_persister import AccountEventsPersister, PersisterConfig
from ctrader.env_connection import AccountEvent, EnvironmentConnection
from ctrader_client.broker_auth import sync_accounts_to_broker

logger = logging.getLogger(__name__)


class AccountConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


@dataclass
class AccountSnapshot:
    grant_id: str = ""
    username: str | None = None
    ctid_trader_account_id: int = 0
    is_live: bool = False
    state: AccountConnectionState = AccountConnectionState.DISCONNECTED
    error_count: int = 0
    balance: float = 0.0
    equity: float = 0.0
    margin: float = 0.0
    positions: list[dict[str, Any]] = field(default_factory=list)
    orders: list[dict[str, Any]] = field(default_factory=list)
    last_event_at: float = 0.0


class AccountHubV2:
    """Persistent multi-account cTrader hub with one transport per environment."""

    def __init__(
        self,
        appwrite_client: Client,
        database_id: str,
        slave_accounts_table: str,
        internal_url: str,
        internal_api_key: str,
        client_id: str,
        client_secret: str,
        account_events_table: str = "account_state_history",
        poll_interval: float = 30.0,
        reconnect_base: float = 5.0,
        reconnect_max: float = 60.0,
        transport_type: str = "tcp",
    ):
        self._appwrite_client = appwrite_client
        self._database_id = database_id
        self._slave_accounts_table = slave_accounts_table
        self._internal_url = internal_url.rstrip("/")
        self._internal_api_key = internal_api_key
        self._client_id = client_id
        self._client_secret = client_secret
        self._poll_interval = poll_interval
        self._reconnect_base = reconnect_base
        self._reconnect_max = reconnect_max
        self._transport_type = transport_type

        self._discovery = AccountDiscovery(
            appwrite_client=appwrite_client,
            database_id=database_id,
            slave_accounts_table=slave_accounts_table,
        )
        self._persister = AccountEventsPersister(
            appwrite_client=appwrite_client,
            config=PersisterConfig(
                database_id=database_id,
                table_id=account_events_table,
            ),
        )

        self._env_connections: dict[bool, EnvironmentConnection] = {}
        self._snapshots: dict[tuple[str, int, bool], AccountSnapshot] = {}
        self._lock = asyncio.Lock()
        self._running = False
        self._poll_task: asyncio.Task | None = None

        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=10_000)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1_000)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    async def start(self) -> None:
        self._running = True
        logger.info("AccountHubV2 starting")
        await self._persister.start()
        await self._ensure_env_connections()
        await self._sync_accounts()
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll_task
            self._poll_task = None
        for conn in list(self._env_connections.values()):
            await conn.stop()
        self._env_connections.clear()
        await self._persister.stop()
        logger.info("AccountHubV2 stopped")

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self._sync_accounts()
            except Exception as exc:
                logger.error("Poll loop error: %s", exc)
            await asyncio.sleep(self._poll_interval)

    async def _ensure_env_connections(self) -> None:
        async with self._lock:
            pending: list[EnvironmentConnection] = []
            for is_live in (False, True):
                if is_live not in self._env_connections:
                    conn = EnvironmentConnection(
                        is_live=is_live,
                        client_id=self._client_id,
                        client_secret=self._client_secret,
                        internal_url=self._internal_url,
                        internal_api_key=self._internal_api_key,
                        transport_type=self._transport_type,
                    )
                    conn.event_bus.subscribe(AccountEvent, self._on_account_event)
                    self._env_connections[is_live] = conn
                    asyncio.create_task(self._start_env_connection(conn))
                    pending.append(conn)
        for conn in pending:
            try:
                await asyncio.wait_for(conn.ready_event.wait(), timeout=30.0)
                logger.info("Environment %s ready", conn._env_label())
            except asyncio.TimeoutError:
                logger.warning(
                    "Timeout waiting for %s environment to be ready; authorisation may fail",
                    conn._env_label(),
                )

    async def _start_env_connection(self, conn: EnvironmentConnection) -> None:
        retry = self._reconnect_base
        while self._running:
            try:
                await conn.start()
                return
            except Exception as exc:
                logger.error(
                    "Failed to start %s environment connection: %s",
                    "live" if conn.is_live else "demo", exc,
                )
                await asyncio.sleep(retry)
                retry = min(retry * 2, self._reconnect_max)

    async def _sync_accounts(self) -> None:
        await self._ensure_env_connections()
        added, removed = await asyncio.to_thread(self._discovery.diff)

        if added:
            logger.info("Discovered %d new account refs", len(added))
        if removed:
            logger.info("Removed %d stale account refs", len(removed))

        for ref in added:
            try:
                await self._authorize_ref(ref)
            except Exception as exc:
                logger.error(
                    "Failed to authorise %s account %d for grant %s: %s",
                    "live" if ref.is_live else "demo",
                    ref.ctid_trader_account_id,
                    ref.grant_id,
                    exc,
                )
                async with self._lock:
                    snap = self._snapshot_for(ref)
                    snap.state = AccountConnectionState.ERROR
                    snap.error_count += 1

        for ref in removed:
            await self._deauthorize_ref(ref)

    async def _authorize_ref(self, ref: AccountRef) -> None:
        conn = self._env_connections.get(ref.is_live)
        if conn is None:
            return
        try:
            await asyncio.wait_for(conn.ready_event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning(
                "Environment %s not ready for account %d; skipping authorisation",
                "live" if ref.is_live else "demo", ref.ctid_trader_account_id,
            )
            async with self._lock:
                snap = self._snapshot_for(ref)
                snap.state = AccountConnectionState.ERROR
                snap.error_count += 1
            return
        async with self._lock:
            snap = self._snapshot_for(ref)
            snap.state = AccountConnectionState.CONNECTING
            snap.username = ref.username
        await conn.authorize_account(ref.grant_id, ref.ctid_trader_account_id)
        
        # Sync account data to Appwrite broker
        try:
            # Get access token for this grant
            access_token = await conn._token_client.refresh(ref.grant_id)
            if access_token:
                # Get the protocol client from the connection
                protocol = conn._protocol
                
                # Sync accounts to broker
                await sync_accounts_to_broker(
                    protocol=protocol,
                    access_token=access_token,
                    broker_url=self._internal_url,
                    internal_api_key=self._internal_api_key,
                    grant_id=ref.grant_id,
                )
                logger.info("Synced account %d (grant=%s) to broker", ref.ctid_trader_account_id, ref.grant_id)
            else:
                logger.warning("No access token available for account sync (grant=%s)", ref.grant_id)
        except Exception as exc:
            logger.error("Failed to sync account %d (grant=%s) to broker: %s", 
                        ref.ctid_trader_account_id, ref.grant_id, exc)
        
        async with self._lock:
            snap = self._snapshot_for(ref)
            snap.state = AccountConnectionState.CONNECTED
            snap.error_count = 0
        await self._broadcast({
            "type": "connection_state",
            "grant_id": ref.grant_id,
            "ctid": ref.ctid_trader_account_id,
            "is_live": ref.is_live,
            "username": ref.username,
            "state": "connected",
            "ts": datetime.now(UTC).isoformat(),
        })

    async def _deauthorize_ref(self, ref: AccountRef) -> None:
        conn = self._env_connections.get(ref.is_live)
        if conn is not None:
            await conn.remove_account(ref.ctid_trader_account_id)
        async with self._lock:
            self._snapshots.pop(
                (ref.grant_id, ref.ctid_trader_account_id, ref.is_live), None
            )
        await self._broadcast({
            "type": "connection_state",
            "grant_id": ref.grant_id,
            "ctid": ref.ctid_trader_account_id,
            "is_live": ref.is_live,
            "state": "disconnected",
            "ts": datetime.now(UTC).isoformat(),
        })

    def _snapshot_for(self, ref: AccountRef) -> AccountSnapshot:
        key = (ref.grant_id, ref.ctid_trader_account_id, ref.is_live)
        if key not in self._snapshots:
            self._snapshots[key] = AccountSnapshot(
                grant_id=ref.grant_id,
                username=ref.username,
                ctid_trader_account_id=ref.ctid_trader_account_id,
                is_live=ref.is_live,
            )
        return self._snapshots[key]

    async def _on_account_event(self, event: AccountEvent) -> None:
        # Persist account events (skip pure connection-state meta events)
        if event.event_type not in {"connection_state"}:
            await self._persister.on_event(event)

        # Update hot snapshot
        async with self._lock:
            snap = self._snapshots.get(
                (event.grant_id, event.ctid_trader_account_id, event.is_live)
            )
            if snap is not None:
                snap.last_event_at = time.time()
                if event.event_type == "trader_updated":
                    snap.balance = event.payload.get("balance", snap.balance)
                    snap.equity = event.payload.get("equity", snap.equity)
                elif event.event_type == "margin_changed":
                    snap.margin = event.payload.get("used_margin", snap.margin)
                elif event.event_type == "reconcile":
                    snap.positions = event.payload.get("positions", [])
                    snap.orders = event.payload.get("orders", [])

        # Fan-out to WebSocket subscribers
        await self._broadcast({
            "type": event.event_type,
            "grant_id": event.grant_id,
            "ctid": event.ctid_trader_account_id,
            "is_live": event.is_live,
            "timestamp_ms": event.timestamp_ms,
            "payload": event.payload,
            "ts": datetime.now(UTC).isoformat(),
        })

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

    # ── Public query API (matches AccountHub) ──────────────────────────────────

    async def get_accounts(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        async with self._lock:
            for key, snap in self._snapshots.items():
                result.append({
                    "key": ":".join(str(k) for k in key),
                    "grant_id": snap.grant_id,
                    "username": snap.username,
                    "ctid": snap.ctid_trader_account_id,
                    "is_live": snap.is_live,
                    "state": snap.state.value,
                    "error_count": snap.error_count,
                    "balance": snap.balance,
                    "equity": snap.equity,
                    "margin": snap.margin,
                    "last_event_at": datetime.fromtimestamp(
                        snap.last_event_at, tz=UTC
                    ).isoformat() if snap.last_event_at else None,
                })
        return result

    async def get_account_state(
        self, grant_id: str, ctid: int, is_live: bool = False
    ) -> dict[str, Any] | None:
        key = (grant_id, ctid, is_live)
        async with self._lock:
            snap = self._snapshots.get(key)
            if snap is None:
                return None
            return {
                "grant_id": snap.grant_id,
                "username": snap.username,
                "ctid": snap.ctid_trader_account_id,
                "is_live": snap.is_live,
                "state": snap.state.value,
                "error_count": snap.error_count,
                "balance": snap.balance,
                "equity": snap.equity,
                "margin": snap.margin,
                "positions": snap.positions,
                "orders": snap.orders,
                "last_event_at": datetime.fromtimestamp(
                    snap.last_event_at, tz=UTC
                ).isoformat() if snap.last_event_at else None,
            }

    @property
    def connection_count(self) -> int:
        return len(self._env_connections)

    @property
    def connected_count(self) -> int:
        return sum(
            1 for snap in self._snapshots.values()
            if snap.state == AccountConnectionState.CONNECTED
        )
