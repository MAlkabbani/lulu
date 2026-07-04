from __future__ import annotations

from collections.abc import Callable

from app_core.runtime_models import RuntimeEvent


EventSubscriber = Callable[[RuntimeEvent], None]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[EventSubscriber] = []

    def subscribe(self, callback: EventSubscriber) -> None:
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: EventSubscriber) -> None:
        self._subscribers = [subscriber for subscriber in self._subscribers if subscriber != callback]

    def publish(self, event: RuntimeEvent) -> None:
        for subscriber in list(self._subscribers):
            subscriber(event)

