"""WebSocket server for real-time account data delivery.

Accepts WebSocket connections from clients (dashboard, command center, dataservice)
and delivers real-time account events from the AccountHub. Clients can subscribe
to specific accounts or receive all account events.

REST endpoints:
    GET /health          — health check
    GET /accounts        — list all managed accounts and their connection state
    GET /accounts/:grant_id/:ctid  — get specific account state
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query

from ctrader.account_hub import AccountHub
from ctrader.config import CTRADERConfig

logger = logging.getLogger(__name__)


class AccountWebSocketServer:
    """FastAPI app that serves WebSocket and REST endpoints."""

    def __init__(self, hub: AccountHub):
        self._hub = hub
        self._app = FastAPI(title="Account WS Server")
        self._setup_routes()

    @property
    def app(self) -> FastAPI:
        return self._app

    def _setup_routes(self) -> None:
        app = self._app

        @app.get("/health")
        async def health() -> dict[str, Any]:
            return {
                "status": "ok",
                "service": "account-ws-server",
                "connections": self._hub.connection_count,
                "connected": self._hub.connected_count,
            }

        @app.get("/accounts")
        async def list_accounts() -> list[dict[str, Any]]:
            return await self._hub.get_accounts()

        @app.get("/accounts/{grant_id}/{ctid}")
        async def get_account(
            grant_id: str, ctid: int, live: bool = Query(default=False),
        ) -> dict[str, Any]:
            state = await self._hub.get_account_state(grant_id, ctid, live)
            if state is None:
                return {"error": "not_found", "grant_id": grant_id, "ctid": ctid}
            return state

        @app.websocket("/ws")
        async def ws_endpoint(ws: WebSocket) -> None:
            await ws.accept()
            queue = self._hub.subscribe()

            try:
                data = await ws.receive_text()
                try:
                    filters: dict[str, Any] = json.loads(data)
                except json.JSONDecodeError:
                    filters = {}

                filter_grant: str | None = filters.get("grant_id")
                filter_ctid: int | None = filters.get("ctid")
                filter_live: bool | None = filters.get("live_only",
                    filters.get("is_live"))

                accounts_snapshot = await self._hub.get_accounts()
                await ws.send_text(json.dumps({
                    "type": "snapshot",
                    "accounts": accounts_snapshot,
                }))

                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    except asyncio.TimeoutError:
                        await ws.send_text(json.dumps({"type": "ping"}))
                        continue

                    if filter_grant and event.get("grant_id") != filter_grant:
                        continue
                    if filter_ctid and event.get("ctid") != filter_ctid:
                        continue
                    if filter_live is not None and event.get("is_live") != filter_live:
                        continue

                    await ws.send_text(json.dumps(event))
            except WebSocketDisconnect:
                pass
            except Exception as exc:
                logger.warning("WS error: %s", exc)
            finally:
                self._hub.unsubscribe(queue)
                try:
                    await ws.close()
                except Exception:
                    pass
