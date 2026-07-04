"""Persist normalised account events to Appwrite TablesDB.

Subscribes to an ``AsyncEventBus`` (usually the one exposed by
``EnvironmentConnection``), batches events, and writes them to the
``account_events`` table in the ``ctrader_auth`` database.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from appwrite.client import Client
from appwrite.exception import AppwriteException
from appwrite.id import ID
from appwrite.services.tables_db import TablesDB

from ctrader.env_connection import AccountEvent

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PersisterConfig:
    database_id: str = "slwp_platform"
    table_id: str = "account_state_history"
    batch_size: int = 50
    flush_interval_seconds: float = 5.0


class AccountEventsPersister:
    """Async batch persister for ``AccountEvent`` rows."""

    _COLUMNS = [
        {"key": "grant_id", "type": "string", "size": 255, "required": True},
        {"key": "ctid_trader_account_id", "type": "integer", "required": True},
        {"key": "is_live", "type": "boolean", "required": True},
        {"key": "event_type", "type": "string", "size": 100, "required": True},
        {"key": "event_json", "type": "string", "size": 65535, "required": True},
        {"key": "timestamp_ms", "type": "bigint", "required": True},
        {"key": "received_at", "type": "datetime", "required": True},
    ]

    def __init__(
        self,
        appwrite_client: Client,
        config: PersisterConfig | None = None,
    ):
        self._client = appwrite_client
        self._tables = TablesDB(appwrite_client)
        self._config = config or PersisterConfig()
        self._queue: asyncio.Queue[AccountEvent] = asyncio.Queue(maxsize=10_000)
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Ensure the table exists and start the background flush loop."""
        if self._running:
            return
        self._running = True
        await self._ensure_table()
        self._task = asyncio.create_task(self._flush_loop())
        logger.info(
            "AccountEventsPersister started (table=%s)", self._config.table_id
        )

    async def stop(self) -> None:
        """Stop the flush loop and drain remaining events."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._flush()

    async def on_event(self, event: AccountEvent) -> None:
        """Public handler suitable for ``AsyncEventBus.subscribe``."""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Account events queue full; dropping event %s", event.event_type)

    # ── Table setup ────────────────────────────────────────────────────────────

    async def _ensure_table(self) -> None:
        def _check_or_create() -> None:
            try:
                self._tables.get_table(
                    database_id=self._config.database_id,
                    table_id=self._config.table_id,
                )
                return
            except AppwriteException as exc:
                if exc.code != 404:
                    raise
            logger.info(
                "Creating account_events table '%s' in database '%s'",
                self._config.table_id, self._config.database_id,
            )
            self._tables.create_table(
                database_id=self._config.database_id,
                table_id=self._config.table_id,
                name="Account Events",
                columns=self._COLUMNS,
            )

        try:
            await asyncio.to_thread(_check_or_create)
        except AppwriteException as exc:
            if exc.code != 409:
                logger.error("Failed to ensure account_events table: %s", exc)

    # ── Flush loop ─────────────────────────────────────────────────────────────

    async def _flush_loop(self) -> None:
        while self._running:
            try:
                await asyncio.wait_for(
                    self._queue.join(), timeout=self._config.flush_interval_seconds
                )
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break
            await self._flush()

    async def _flush(self) -> None:
        batch: list[AccountEvent] = []
        while not self._queue.empty() and len(batch) < self._config.batch_size:
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if not batch:
            return

        received_at = datetime.now(UTC).isoformat()

        def _insert() -> None:
            for event in batch:
                try:
                    self._tables.create_row(
                        database_id=self._config.database_id,
                        table_id=self._config.table_id,
                        row_id=ID.unique(),
                        data={
                            "grant_id": event.grant_id,
                            "ctid_trader_account_id": event.ctid_trader_account_id,
                            "is_live": event.is_live,
                            "event_type": event.event_type,
                            "event_json": json.dumps(event.payload, default=str),
                            "timestamp_ms": event.timestamp_ms,
                            "received_at": received_at,
                        },
                    )
                except Exception as exc:
                    logger.error(
                        "Failed to persist account event %s for %d: %s",
                        event.event_type, event.ctid_trader_account_id, exc,
                    )

        try:
            await asyncio.to_thread(_insert)
            logger.debug("Persisted %d account events", len(batch))
        except Exception as exc:
            logger.error("Batch account-event insert failed: %s", exc)
        finally:
            for _ in batch:
                self._queue.task_done()
