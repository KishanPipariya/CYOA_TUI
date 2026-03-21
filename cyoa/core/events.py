from typing import Callable, Dict, List, Any
import logging

logger = logging.getLogger(__name__)

class EventBus:
    """A minimal dictionary-based Pub/Sub Event Bus for decoupling modules."""
    
    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Callable[..., None]]] = {}

    def subscribe(self, event_name: str, callback: Callable[..., None]) -> None:
        """Register a callback for a specific event."""
        if event_name not in self._subscribers:
            self._subscribers[event_name] = []
        if callback not in self._subscribers[event_name]:
            self._subscribers[event_name].append(callback)

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
                    logger.error(f"Error executing callback {callback.__name__} for event {event_name}: {e}")
                    
    def clear(self) -> None:
        """Clear all subscribers (mainly useful for isolating test environments)."""
        self._subscribers.clear()

# Global Singleton Event Bus instance
bus = EventBus()
