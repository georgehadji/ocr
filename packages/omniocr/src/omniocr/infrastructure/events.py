from __future__ import annotations

from collections.abc import Callable


EventHandler = Callable[[object], None]


class InMemoryEventBus:
    """Synchronous event bus for desktop composition and deterministic tests."""

    def __init__(self) -> None:
        self._handlers: list[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> None:
        self._handlers.append(handler)

    def publish(self, event: object) -> None:
        for handler in tuple(self._handlers):
            handler(event)


__all__ = ["EventHandler", "InMemoryEventBus"]
