import logging
from textual.app import App
from textual.widgets import ListView
from cyoa.core.models import StoryNode
from cyoa.ui.components import StatusDisplay

logger = logging.getLogger(__name__)

class EventsMixin:
    """Mixin for handling engine and UI events."""

    def _handle_engine_started(self) -> None:
        assert isinstance(self, App)
        self.turn_count = 1
        self.mood = "default"
        self._last_stats_snapshot = None
        self.query_one("#journal-list", ListView).clear()

    def _handle_engine_restarted(self) -> None:
        assert isinstance(self, App)
        self.notify("Adventure Reset.", severity="information", timeout=2)

    def _handle_choice_made(self, choice_text: str) -> None:
        self.action_skip_typewriter()

    def _handle_node_generating(self) -> None:
        self.show_loading()

    def _handle_token_streamed(self, token: str) -> None:
        self._stream_narrative(token)

    def _handle_node_completed(self, node: StoryNode) -> None:
        assert isinstance(self, App)
        if self.engine:
            self.turn_count = self.engine.state.turn_count
        self.display_node(node)
        try:
            # Avoid DB/UI work when the panel is hidden.
            story_map_panel = self.query_one("#story-map-panel")
            if not story_map_panel.has_class("panel-collapsed"):
                self.update_story_map()
        except Exception as e:
            logger.debug("Failed to conditionally update story map: %s", e)

    def _handle_stats_updated(self, stats: dict[str, int]) -> None:
        assert isinstance(self, App)
        try:
            status = self.query_one(StatusDisplay)
            status.health = stats.get("health", 100)
            status.gold = stats.get("gold", 0)
            status.reputation = stats.get("reputation", 0)

            previous = getattr(self, "_last_stats_snapshot", None)
            if previous is not None:
                deltas: list[str] = []

                health_delta = stats.get("health", 100) - previous.get("health", 100)
                if health_delta:
                    deltas.append(f"{health_delta:+d} HP")

                gold_delta = stats.get("gold", 0) - previous.get("gold", 0)
                if gold_delta:
                    deltas.append(f"{gold_delta:+d} Gold")

                rep_delta = stats.get("reputation", 0) - previous.get("reputation", 0)
                if rep_delta:
                    deltas.append(f"{rep_delta:+d} Rep")

                if deltas:
                    severity = "information"
                    if health_delta < 0:
                        severity = "warning"
                    elif health_delta > 0:
                        severity = "success"
                    self.notify(" | ".join(deltas), severity=severity, timeout=2)

            self._last_stats_snapshot = {
                "health": stats.get("health", 100),
                "gold": stats.get("gold", 0),
                "reputation": stats.get("reputation", 0),
            }
        except Exception as e:
            logger.debug("Failed to update status display stats: %s", e)

    def _handle_inventory_updated(self, inventory: list[str]) -> None:
        assert isinstance(self, App)
        try:
            self.query_one(StatusDisplay).inventory = list(inventory)
        except Exception as e:
            logger.debug("Failed to update status display inventory: %s", e)

    def _handle_title_generated(self, title: str) -> None:
        assert isinstance(self, App)
        self.notify(f"New Chapter: {title}", severity="information", timeout=5)

    def _handle_ending_reached(self, node: StoryNode) -> None:
        assert isinstance(self, App)
        self.notify("The Story Ends.", severity="success", timeout=10)

    def _handle_error(self, error: str) -> None:
        assert isinstance(self, App)
        self.notify(f"Error: {error}", severity="error", timeout=5)
        self.query_one("#loading").add_class("hidden")

    def _handle_status_message(self, message: str) -> None:
        assert isinstance(self, App)
        self.notify(message, severity="information", timeout=4)
