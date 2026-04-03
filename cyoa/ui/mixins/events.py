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
        self.update_story_map()

    def _handle_stats_updated(self, stats: dict[str, int]) -> None:
        assert isinstance(self, App)
        try:
            status = self.query_one(StatusDisplay)
            status.health = stats.get("health", 100)
            status.gold = stats.get("gold", 0)
            status.reputation = stats.get("reputation", 0)
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
