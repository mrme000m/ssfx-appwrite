"""WebSocket connection registry and pub/sub fan-out for market data."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import Any

from fastapi import WebSocket

from .models import BarClose, ContextSnapshot, DepthUpdate, SpotTick

logger = logging.getLogger(__name__)

MarketEvent = SpotTick | BarClose | DepthUpdate | ContextSnapshot | dict[str, Any]


class WebSocketHub:
    """Fan-out hub with per-connection backpressure."""

    def __init__(self, heartbeat_interval: float = 15.0, max_queue: int = 1000):
        self._conns: dict[str, WebSocket] = {}
        self._subscriptions: dict[str, set[str]] = {}
        self._queues: dict[str, asyncio.Queue[MarketEvent]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._heartbeat_interval = heartbeat_interval
        self._max_queue = max_queue

    async def connect(self, ws: WebSocket) -> str:
        await ws.accept()
        conn_id = str(uuid.uuid4())
        self._conns[conn_id] = ws
        self._queues[conn_id] = asyncio.Queue(maxsize=self._max_queue)
        self._tasks[conn_id] = asyncio.create_task(self._send_loop(conn_id))
        logger.info("WS connected %s (total %d)", conn_id, len(self._conns))
        return conn_id

    async def disconnect(self, conn_id: str) -> None:
        ws = self._conns.pop(conn_id, None)
        if ws:
            try:
                await ws.close()
            except Exception:
                pass
        task = self._tasks.pop(conn_id, None)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._queues.pop(conn_id, None)
        for subs in self._subscriptions.values():
            subs.discard(conn_id)
        logger.info("WS disconnected %s", conn_id)

    def subscribe(self, conn_id: str, channels: list[str]) -> None:
        for ch in channels:
            self._subscriptions.setdefault(ch, set()).add(conn_id)
        logger.info("%s subscribed to %s", conn_id, channels)

    def unsubscribe(self, conn_id: str, channels: list[str]) -> None:
        for ch in channels:
            self._subscriptions.get(ch, set()).discard(conn_id)

    async def publish(self, channel: str, payload: MarketEvent) -> None:
        for conn_id in list(self._subscriptions.get(channel, set())):
            q = self._queues.get(conn_id)
            if q is None:
                continue
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    async def broadcast(self, payload: dict[str, Any]) -> None:
        for conn_id in list(self._conns.keys()):
            q = self._queues.get(conn_id)
            if q is None:
                continue
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    async def _send_loop(self, conn_id: str) -> None:
        import time as _time

        ws = self._conns.get(conn_id)
        if ws is None:
            return
        q = self._queues[conn_id]
        last_heartbeat = _time.time()
        try:
            while True:
                timeout = self._heartbeat_interval - (_time.time() - last_heartbeat)
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=max(timeout, 0.1))
                except asyncio.TimeoutError:
                    if _time.time() - last_heartbeat >= self._heartbeat_interval:
                        await ws.send_json({"type": "heartbeat", "ts": int(_time.time() * 1000)})
                        last_heartbeat = _time.time()
                    continue

                if isinstance(payload, (SpotTick, BarClose, DepthUpdate, ContextSnapshot)):
                    msg = payload.to_json()
                else:
                    msg = payload
                try:
                    await ws.send_json(msg)
                except Exception as exc:
                    logger.warning("WS send failed %s: %s", conn_id, exc)
                    await self.disconnect(conn_id)
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("WS send loop error %s: %s", conn_id, exc)
            await self.disconnect(conn_id)
