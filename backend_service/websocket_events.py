from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from app_core.event_bus import EventBus
from app_core.runtime_models import RuntimeEvent, make_event
from backend_service.api_models import API_VERSION

EVENT_QUEUE_MAX_SIZE = 64
MAX_DROPPED_EVENTS = 8
DROPPABLE_EVENT_TYPES = {"response.partial", "tts.chunk_emitted"}


def serialize_event(event: RuntimeEvent) -> dict[str, object]:
    return {
        "api_version": API_VERSION,
        "event_type": event.event_type,
        "timestamp": datetime.now(UTC).isoformat(),
        "payload": event.payload,
    }


class WebSocketEventBridge:
    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus

    async def stream(
        self,
        *,
        send_json: Callable[[dict[str, object]], Awaitable[None]],
    ) -> None:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[RuntimeEvent] = asyncio.Queue(maxsize=EVENT_QUEUE_MAX_SIZE)
        dropped_events = 0

        def subscriber(event: RuntimeEvent) -> None:
            def enqueue_event() -> None:
                nonlocal dropped_events
                try:
                    queue.put_nowait(event)
                    dropped_events = 0
                    return
                except asyncio.QueueFull:
                    dropped_events += 1
                    if (
                        event.event_type in DROPPABLE_EVENT_TYPES
                        and dropped_events < MAX_DROPPED_EVENTS
                    ):
                        return
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    overload_event = make_event(
                        "service.overload",
                        reason="event-backpressure",
                        dropped_events=dropped_events,
                    )
                    try:
                        queue.put_nowait(overload_event)
                    except asyncio.QueueFull:
                        return

            loop.call_soon_threadsafe(enqueue_event)

        self._event_bus.subscribe(subscriber)
        try:
            await send_json(
                {
                    "api_version": API_VERSION,
                    "event_type": "service.connected",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "payload": {"stream": "runtime-events"},
                }
            )
            while True:
                event = await queue.get()
                await send_json(serialize_event(event))
                if event.event_type == "service.overload":
                    return
        finally:
            self._event_bus.unsubscribe(subscriber)
