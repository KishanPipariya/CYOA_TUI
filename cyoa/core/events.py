import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class EventBus:
    """A minimal dictionary-based Pub/Sub Event Bus for decoupling modules."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[..., None]]] = {}

    def subscribe(self, event_name: str, callback: Callable[..., None]) -> Callable[[], None]:
        """Register a callback for a specific event. Returns a function to unsubscribe."""
        if event_name not in self._subscribers:
            self._subscribers[event_name] = []
        if callback not in self._subscribers[event_name]:
            self._subscribers[event_name].append(callback)

        return lambda: self.unsubscribe(event_name, callback)

    def unsubscribe(self, event_name: str, callback: Callable[..., None]) -> None:
        """Remove a callback from an event's subscriber list."""
        if event_name in self._subscribers and callback in self._subscribers[event_name]:
            self._subscribers[event_name].remove(callback)

    def emit(self, event_name: str, **kwargs: Any) -> None:
        """Broadcast an event, calling all registered callbacks with kwargs."""
        if event_name in self._subscribers:
            for callback in self._subscribers[event_name]:
                try:
                    callback(**kwargs)
                except Exception as e:  # noqa: BLE001
                    logger.error(
                        f"Error executing callback {callback.__name__} for event {event_name}: {e}"
                    )

    def clear(self) -> None:
        """Clear all subscribers (mainly useful for isolating test environments)."""
        self._subscribers.clear()


# Global Singleton Event Bus instance
bus = EventBus()


# Event names
class Events:
    # Engine lifecycle
    ENGINE_STARTED = "engine.started"
    ENGINE_RESTARTED = "engine.restarted"

    # Narrative flow
    CHOICE_MADE = "engine.choice_made"
    NODE_GENERATING = "engine.node_generating"
    TOKEN_STREAMED = "engine.token_streamed"
    SUMMARIZATION_STARTED = "engine.summarization_started"
    NODE_COMPLETED = "engine.node_completed"

    # State updates
    STATS_UPDATED = "engine.stats_updated"
    INVENTORY_UPDATED = "engine.inventory_updated"
    STORY_TITLE_GENERATED = "engine.story_title_generated"

    # Endings and errors
    ENDING_REACHED = "engine.ending_reached"
    ERROR_OCCURRED = "engine.error_occurred"
    STATUS_MESSAGE = "engine.status_message"

    # External integrations
    DB_SAVED = "db.saved"
    MEMORY_INDEXED = "memory.indexed"
