"""Transport layer — raw TCP and WebSocket with SSL, framing, and reconnect."""

from __future__ import annotations

import asyncio
import logging
import struct
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

_FRAME_HEADER = struct.Struct(">I")  # 4-byte big-endian length prefix


class BaseTransport(ABC):
    """Abstract transport interface."""

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def send_raw(self, data: bytes) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @property
    @abstractmethod
    def is_connected(self) -> bool: ...


class AsyncTcpTransport(BaseTransport):
    """SSL/TLS TCP transport with 4-byte length-prefix framing."""

    def __init__(
        self,
        host: str,
        port: int,
        on_frame: Callable[[bytes], Awaitable[None]],
        heartbeat_interval: float = 10.0,
        on_reconnect: Callable[[], Awaitable[None]] | None = None,
    ):
        self._host = host
        self._port = port
        self._on_frame = on_frame
        self._heartbeat_interval = heartbeat_interval
        self._on_reconnect = on_reconnect

        self._send_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 60.0
        self._tasks: list[asyncio.Task[None]] = []
        self._connected = False

    async def connect(self) -> None:
        """Establish SSL/TLS connection and start I/O loops."""
        # Clean up any dead tasks from a previous connection before starting new ones.
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()

        ssl_ctx = self._ssl_context()
        self._reader, self._writer = await asyncio.open_connection(
            self._host, self._port, ssl=ssl_ctx
        )
        self._connected = True
        self._reconnect_delay = 1.0  # reset on success

        self._tasks.append(asyncio.create_task(self._read_loop(self._reader)))
        self._tasks.append(asyncio.create_task(self._write_loop()))
        logger.info("Connected to %s:%d", self._host, self._port)

    async def send_raw(self, data: bytes) -> None:
        """Queue a raw payload for sending (framed by write_loop)."""
        frame = _FRAME_HEADER.pack(len(data)) + data
        await self._send_queue.put(frame)

    async def close(self) -> None:
        """Shut down all tasks and close the connection."""
        self._connected = False
        # Signal write loop to exit
        await self._send_queue.put(None)
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None

    @property
    def is_connected(self) -> bool:
        return self._connected and self._writer is not None

    def request_reconnect(self) -> None:
        """Called by protocol layer when connection is lost."""
        if self._connected:
            self._connected = False
            asyncio.create_task(self._reconnect())

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        try:
            while True:
                header = await reader.readexactly(4)
                length = _FRAME_HEADER.unpack(header)[0]
                payload = await reader.readexactly(length)
                await self._on_frame(payload)
        except asyncio.CancelledError:
            raise
        except (asyncio.IncompleteReadError, ConnectionResetError, OSError) as exc:
            logger.warning("TCP read error: %s", exc)
            self.request_reconnect()

    async def _write_loop(self) -> None:
        try:
            while True:
                frame = await self._send_queue.get()
                if frame is None:  # shutdown signal
                    return
                if self._writer and not self._writer.is_closing():
                    self._writer.write(frame)
                    await self._writer.drain()
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError) as exc:
            logger.warning("TCP write error: %s", exc)
            self.request_reconnect()

    async def _reconnect(self) -> None:
        """Exponential backoff reconnection."""
        while not self._connected:
            logger.info(
                "Reconnecting in %.1fs...", self._reconnect_delay
            )
            await asyncio.sleep(self._reconnect_delay)
            self._reconnect_delay = min(
                self._reconnect_delay * 2, self._max_reconnect_delay
            )
            try:
                await self.connect()
                if self._on_reconnect is not None:
                    try:
                        await self._on_reconnect()
                    except Exception as exc:
                        logger.warning("Reconnect callback failed: %s", exc)
                return
            except (ConnectionError, OSError) as exc:
                logger.warning("Reconnect failed: %s", exc)

    @staticmethod
    def _ssl_context() -> Any:
        import ssl

        return ssl.create_default_context()


class AsyncWsTransport(BaseTransport):
    """WebSocket transport (wss://) — framing handled by WebSocket protocol."""

    def __init__(
        self,
        url: str,
        on_frame: Callable[[bytes], Awaitable[None]],
        heartbeat_interval: float = 10.0,
    ):
        self._url = url
        self._on_frame = on_frame
        self._heartbeat_interval = heartbeat_interval

        self._ws: Any = None
        self._session: Any = None
        self._tasks: list[asyncio.Task[None]] = []
        self._connected = False

    async def connect(self) -> None:
        """Establish WebSocket connection and start read loop."""
        import aiohttp

        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(
            self._url,
            protocols=["binary"],
            heartbeat=self._heartbeat_interval,
        )
        self._connected = True
        self._tasks.append(asyncio.create_task(self._read_loop()))
        logger.info("Connected to WebSocket: %s", self._url)

    async def send_raw(self, data: bytes) -> None:
        """Send raw binary over WebSocket."""
        if self._ws:
            await self._ws.send_bytes(data)

    async def close(self) -> None:
        """Shut down WebSocket and session."""
        self._connected = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._ws:
            await self._ws.close()
            self._ws = None
        if self._session:
            await self._session.close()
            self._session = None

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None and not self._ws.closed

    async def _read_loop(self) -> None:
        try:
            assert self._ws is not None
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    await self._on_frame(msg.data)
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("WebSocket read error: %s", exc)
        finally:
            self._connected = False
