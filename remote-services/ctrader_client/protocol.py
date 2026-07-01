"""Protocol layer — message routing, request/response correlation, heartbeat."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import (
    ProtoHeartbeatEvent,
    ProtoMessage,
)
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq,
    ProtoOAAccountLogoutReq,
    ProtoOAApplicationAuthReq,
    ProtoOAGetAccountListByAccessTokenReq,
    ProtoOAReconcileReq,
    ProtoOARefreshTokenReq,
    ProtoOATraderReq,
    ProtoOAVersionReq,
)
from ctrader_open_api.protobuf import Protobuf

from .transport import BaseTransport

logger = logging.getLogger(__name__)

HandlerFn = Callable[[Any], Awaitable[None]]


class CTraderProtocolClient:
    """Asyncio-native protocol client.

    Wraps raw bytes into typed domain messages, correlates request/response
    via clientMsgId, and fan-outs events to registered type handlers.
    """

    def __init__(
        self,
        transport: BaseTransport,
        client_id: str,
        client_secret: str,
    ):
        self._transport = transport
        self._client_id = client_id
        self._client_secret = client_secret

        # payloadType → set of async handlers
        self._handlers: dict[int, set[HandlerFn]] = {}
        # clientMsgId → asyncio.Future (request/response correlation)
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._msg_counter = 0
        self._heartbeat_interval = 10.0
        self._heartbeat_task: asyncio.Task[None] | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start heartbeat task."""
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def stop(self) -> None:
        """Cancel heartbeat task."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

    # ── Send / Receive ─────────────────────────────────────────────────────

    async def send(self, message: Any, client_msg_id: str | None = None) -> asyncio.Future[Any]:
        """Send a Protobuf request message and return a Future for the response."""
        if client_msg_id is None:
            self._msg_counter += 1
            client_msg_id = str(self._msg_counter)

        # Attach clientMsgId if the message supports it
        if hasattr(message, "clientMsgId"):
            message.clientMsgId = client_msg_id

        envelope = ProtoMessage()
        envelope.payloadType = message.payloadType
        envelope.payload = message.SerializeToString()
        envelope.clientMsgId = client_msg_id

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._pending[client_msg_id] = fut

        await self._transport.send_raw(envelope.SerializeToString())
        return fut

    async def send_heartbeat(self) -> None:
        """Send a heartbeat message."""
        hb = ProtoHeartbeatEvent()
        envelope = ProtoMessage()
        envelope.payloadType = hb.payloadType
        envelope.payload = hb.SerializeToString()
        await self._transport.send_raw(envelope.SerializeToString())

    # ── Handler subscription ───────────────────────────────────────────────

    def subscribe(self, payload_type: int, handler: HandlerFn) -> None:
        """Register an async handler for a specific payloadType."""
        self._handlers.setdefault(payload_type, set()).add(handler)

    def unsubscribe(self, payload_type: int, handler: HandlerFn) -> None:
        """Remove a previously registered handler."""
        self._handlers.get(payload_type, set()).discard(handler)

    # ── Frame dispatch (called by transport) ───────────────────────────────

    async def on_frame(self, raw: bytes) -> None:
        """Parse a raw frame and route to pending future and/or handlers."""
        proto_msg = ProtoMessage()
        proto_msg.ParseFromString(raw)

        # Decode inner message
        domain_obj = Protobuf.extract(proto_msg)

        # Resolve pending request/response
        if proto_msg.clientMsgId and proto_msg.clientMsgId in self._pending:
            fut = self._pending.pop(proto_msg.clientMsgId)
            if not fut.done():
                fut.set_result(domain_obj)

        # Fan-out to all registered type handlers
        for handler in self._handlers.get(proto_msg.payloadType, set()):
            asyncio.create_task(handler(domain_obj))

    # ── Authentication ─────────────────────────────────────────────────────

    async def authenticate_app(self) -> Any:
        """Step 1: Authenticate the application."""
        req = ProtoOAApplicationAuthReq()
        req.clientId = self._client_id
        req.clientSecret = self._client_secret
        fut = await self.send(req)
        result = await fut
        logger.info("App authenticated: %s", type(result).__name__)
        return result

    async def get_accounts(self, access_token: str) -> Any:
        """Step 2: Get list of accounts for the access token."""
        req = ProtoOAGetAccountListByAccessTokenReq()
        req.accessToken = access_token
        fut = await self.send(req)
        return await fut

    async def authorize_account(self, account_id: int, access_token: str) -> Any:
        """Step 3: Authorize a specific account."""
        req = ProtoOAAccountAuthReq()
        req.ctidTraderAccountId = account_id
        req.accessToken = access_token
        fut = await self.send(req)
        result = await fut
        logger.info("Account %d authorised", account_id)
        return result

    async def reconcile(self, account_id: int) -> Any:
        """Re-sync open positions and pending orders after reconnect."""
        req = ProtoOAReconcileReq()
        req.ctidTraderAccountId = account_id
        fut = await self.send(req)
        return await fut

    async def refresh_token(self, refresh_token: str) -> Any:
        """Refresh OAuth access token using a refresh token."""
        req = ProtoOARefreshTokenReq()
        req.refreshToken = refresh_token
        fut = await self.send(req)
        return await fut

    async def logout(self, account_id: int) -> Any:
        """Logout from a specific trading account."""
        req = ProtoOAAccountLogoutReq()
        req.ctidTraderAccountId = account_id
        fut = await self.send(req)
        return await fut

    async def get_trader(self, account_id: int) -> Any:
        """Get trader profile information for an account."""
        req = ProtoOATraderReq()
        req.ctidTraderAccountId = account_id
        fut = await self.send(req)
        return await fut

    async def get_expected_margin(
        self, account_id: int, symbol_id: int, volume: int
    ) -> Any:
        """Calculate expected margin for a potential order.

        Args:
            account_id: cTrader account ID.
            symbol_id: Target symbol ID.
            volume: Volume in base currency units (lots × lot_size).
        """
        req = self._import("ProtoOAExpectedMarginReq")()
        req.ctidTraderAccountId = account_id
        req.symbolId = symbol_id
        req.volume.extend([volume])
        fut = await self.send(req)
        return await fut

    async def get_unrealized_pnl(self, account_id: int) -> Any:
        """Get total unrealized P&L for all open positions."""
        req = self._import("ProtoOAGetPositionUnrealizedPnLReq")()
        req.ctidTraderAccountId = account_id
        fut = await self.send(req)
        return await fut

    async def get_cash_flow_history(
        self, account_id: int, from_ts: int = 0, to_ts: int = 0
    ) -> Any:
        """Get cash flow history (deposits, withdrawals, bonuses).

        Args:
            from_ts: Unix ms timestamp to search from.
            to_ts: Unix ms timestamp to search to.
        """
        req = self._import("ProtoOACashFlowHistoryListReq")()
        req.ctidTraderAccountId = account_id
        req.fromTimestamp = from_ts
        req.toTimestamp = to_ts
        fut = await self.send(req)
        return await fut

    async def get_order_list(
        self, account_id: int, from_ts: int = 0, to_ts: int = 0
    ) -> Any:
        """Get pending order list (alternative to reconcile, supports date filtering).

        Args:
            from_ts: Unix ms timestamp to search from.
            to_ts: Unix ms timestamp to search to.
        """
        req = self._import("ProtoOAOrderListReq")()
        req.ctidTraderAccountId = account_id
        req.fromTimestamp = from_ts
        req.toTimestamp = to_ts
        fut = await self.send(req)
        return await fut

    async def get_order_details(self, account_id: int, order_id: int) -> Any:
        """Get details of a specific order."""
        req = self._import("ProtoOAOrderDetailsReq")()
        req.ctidTraderAccountId = account_id
        req.orderId = order_id
        fut = await self.send(req)
        return await fut

    async def get_deals_by_position(
        self, account_id: int, position_id: int
    ) -> Any:
        """Get all deals associated with a specific position."""
        req = self._import("ProtoOADealListByPositionIdReq")()
        req.ctidTraderAccountId = account_id
        req.positionId = position_id
        fut = await self.send(req)
        return await fut

    async def get_version(self) -> Any:
        """Get cTrader Open API server version."""
        req = ProtoOAVersionReq()
        fut = await self.send(req)
        return await fut

    # ── Heartbeat loop ─────────────────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        """Periodically send heartbeat messages."""
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            try:
                await self.send_heartbeat()
            except Exception as exc:
                logger.warning("Heartbeat send failed: %s", exc)

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _import(name: str) -> Any:
        """Dynamically import a cTrader protobuf message class."""
        import importlib

        mod = importlib.import_module(
            "ctrader_open_api.messages.OpenApiMessages_pb2"
        )
        return getattr(mod, name)
