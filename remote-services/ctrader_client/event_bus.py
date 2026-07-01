"""Event Bus — typed event fan-out for strategies and logging."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

EventT = Any
HandlerT = Callable[[EventT], Awaitable[None]]


@dataclass(slots=True)
class Signal:
    """A trading signal produced by indicator evaluation."""

    symbol_id: int
    symbol_name: str
    timeframe: str
    condition_name: str
    direction: str  # "long" | "short" | "exit_long" | "exit_short"
    metadata: dict = field(default_factory=dict)


class AsyncEventBus:
    """Typed event bus that fan-outs events to registered handlers.

    Each handler is dispatched via asyncio.create_task so slow handlers
    don't block faster ones. Handlers are keyed by event type.

    Usage::

        bus = AsyncEventBus()
        bus.subscribe(SpotTick, handle_tick)
        bus.subscribe(Signal, handle_signal)
        await bus.publish(SpotTick(...))
    """

    def __init__(self):
        self._subscribers: dict[type, list[HandlerT]] = defaultdict(list)

    def subscribe(self, event_type: type, handler: HandlerT) -> None:
        """Register an async handler for a specific event type."""
        self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: type, handler: HandlerT) -> None:
        """Remove a previously registered handler."""
        self._subscribers[event_type].remove(handler)

    async def publish(self, event: EventT) -> None:
        """Publish an event to all handlers for its type."""
        handlers = self._subscribers.get(type(event), [])
        if not handlers:
            return
        await asyncio.gather(
            *(h(event) for h in handlers),
            return_exceptions=True,
        )

    async def publish_safe(self, event: EventT) -> None:
        """Publish with per-handler exception isolation and logging."""
        handlers = self._subscribers.get(type(event), [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception:
                logger.exception(
                    "Handler %s failed for event %s",
                    handler,
                    type(event).__name__,
                )
