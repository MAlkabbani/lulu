from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from app_core.event_bus import EventBus
from app_core.runtime_models import RuntimeEvent
from backend_service.api_models import API_VERSION


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
        queue: asyncio.Queue[RuntimeEvent] = asyncio.Queue()

        def subscriber(event: RuntimeEvent) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

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
        finally:
            self._event_bus.unsubscribe(subscriber)

