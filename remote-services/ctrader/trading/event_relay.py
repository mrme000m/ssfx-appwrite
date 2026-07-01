"""Relay execution events to Appwrite TablesDB and Realtime."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from appwrite.client import Client
from appwrite.services.tables_db import TablesDB

from ctrader_client.market_data import ExecutionEvent

logger = logging.getLogger(__name__)


class EventRelay:
    """Durable event log for per-account trading events."""

    def __init__(
        self,
        client: Client,
        database_id: str,
        events_table: str,
        listeners: list[Callable[[str, int, str, dict[str, Any]], Awaitable[None]]] | None = None,
    ):
        self._client = client
        self._database_id = database_id
        self._events_table = events_table
        self._tables = TablesDB(client)
        self._listeners = listeners or []

    def add_listener(
        self,
        listener: Callable[[str, int, str, dict[str, Any]], Awaitable[None]],
    ) -> None:
        self._listeners.append(listener)

    async def emit(
        self,
        grant_id: str,
        ctid: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        row_id = uuid.uuid4().hex  # 32 chars; Appwrite rowId limit is 36
        data = {
            "grant_id": grant_id,
            "ctid_trader_account_id": str(ctid),
            "event_type": event_type,
            "payload": json.dumps(payload),
            "created_at": datetime.now(UTC).isoformat(),
        }
        try:
            self._tables.create_row(
                database_id=self._database_id,
                table_id=self._events_table,
                row_id=row_id,
                data=data,
            )
            logger.debug("Relayed event %s for %s:%s", event_type, grant_id, ctid)
            for listener in list(self._listeners):
                try:
                    await listener(grant_id, ctid, event_type, payload)
                except Exception as exc:
                    logger.warning("Event listener failed: %s", exc)
        except Exception as exc:
            logger.error("Event relay failed: %s", exc)

    async def emit_execution_event(
        self,
        grant_id: str,
        ctid: int,
        event: ExecutionEvent,
    ) -> None:
        payload = {
            "event_type": event.event_type,
            "order_id": event.order_id,
            "position_id": event.position_id,
            "symbol_id": event.symbol_id,
            "volume": event.volume,
            "price": event.price,
            "error_code": event.error_code,
        }
        await self.emit(grant_id, ctid, "execution_event", payload)
