from typing import Literal

from textual.containers import Container
from textual.widgets import ListView, Static

from cyoa.core.models import StoryNode
from cyoa.ui.components import StatusDisplay
from cyoa.ui.mixins.contracts import as_mixin_host, as_textual_app


class EventsMixin:
    """Mixin for handling engine and UI events."""

    def _handle_engine_started(self) -> None:
        app = as_textual_app(self)
        host = as_mixin_host(self)
        if host._is_shutting_down:
            return
        host.turn_count = 1
        host.mood = "default"
        host._last_stats_snapshot = None
        app.query_one("#journal-list", ListView).clear()

    def _handle_engine_restarted(self) -> None:
        if as_mixin_host(self)._is_shutting_down:
            return
        as_textual_app(self).notify("Adventure Reset.", severity="information", timeout=2)

    def _handle_choice_made(self, choice_text: str) -> None:
        as_mixin_host(self).action_skip_typewriter()

    def _handle_node_generating(self) -> None:
        as_mixin_host(self).show_loading()

    def _handle_token_streamed(self, token: str) -> None:
        as_mixin_host(self)._stream_narrative(token)

    def _handle_node_completed(self, node: StoryNode) -> None:
        app = as_textual_app(self)
        host = as_mixin_host(self)
        if host._is_shutting_down:
            return
        if host.engine:
            host.turn_count = host.engine.state.turn_count
        host.display_node(node)
        # Avoid DB/UI work when the panel is hidden.
        story_map_panel = app.query_one("#story-map-panel", Container)
        if not story_map_panel.has_class("panel-collapsed"):
            host.update_story_map()

    def _handle_stats_updated(self, stats: dict[str, int]) -> None:
        app = as_textual_app(self)
        host = as_mixin_host(self)
        if host._is_shutting_down:
            return

        status = app.query_one(StatusDisplay)
        status.health = stats.get("health", 100)
        status.gold = stats.get("gold", 0)
        status.reputation = stats.get("reputation", 0)

        previous = host._last_stats_snapshot
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
                severity: Literal["information", "warning", "error"] = (
                    "warning" if health_delta < 0 else "information"
                )
                app.notify(" | ".join(deltas), severity=severity, timeout=2)

        host._last_stats_snapshot = {
            "health": stats.get("health", 100),
            "gold": stats.get("gold", 0),
            "reputation": stats.get("reputation", 0),
        }

    def _handle_inventory_updated(self, inventory: list[str]) -> None:
        app = as_textual_app(self)
        if as_mixin_host(self)._is_shutting_down:
            return
        app.query_one(StatusDisplay).inventory = list(inventory)

    def _handle_title_generated(self, title: str) -> None:
        if as_mixin_host(self)._is_shutting_down:
            return
        as_textual_app(self).notify(f"New Chapter: {title}", severity="information", timeout=5)

    def _handle_ending_reached(self, node: StoryNode) -> None:
        if as_mixin_host(self)._is_shutting_down:
            return
        as_textual_app(self).notify("The Story Ends.", severity="information", timeout=10)

    def _handle_error(self, error: str) -> None:
        app = as_textual_app(self)
        if as_mixin_host(self)._is_shutting_down:
            return
        app.notify(f"Error: {error}", severity="error", timeout=5)
        app.query_one("#loading", Static).add_class("hidden")

    def _handle_status_message(self, message: str) -> None:
        if as_mixin_host(self)._is_shutting_down:
            return
        as_textual_app(self).notify(message, severity="information", timeout=4)
