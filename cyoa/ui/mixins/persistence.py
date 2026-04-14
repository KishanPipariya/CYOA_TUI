import json
import logging
import os

from textual.containers import Container, VerticalScroll
from textual.widgets import Button, Label, ListView, Markdown

from cyoa.core import constants
from cyoa.ui.components import JournalListItem
from cyoa.ui.mixins.contracts import as_mixin_host, as_textual_app

logger = logging.getLogger(__name__)

class PersistenceMixin:
    """Mixin for save/load game persistence."""

    @staticmethod
    def _coerce_ui_state(payload: object) -> dict[str, object]:
        """Normalize optional UI save data so partial files still restore safely."""
        if not isinstance(payload, dict):
            return {}
        return payload

    @staticmethod
    def _coerce_journal_entries(payload: object) -> list[dict[str, object]]:
        """Return only well-formed journal entry objects from a save payload."""
        if not isinstance(payload, list):
            return []
        return [entry for entry in payload if isinstance(entry, dict)]

    @staticmethod
    def _coerce_scene_index(value: object) -> int:
        """Clamp scene indexes from save files to a safe non-negative int."""
        if isinstance(value, bool):
            return 0
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 0
        return max(0, parsed)

    def action_save_game(self) -> None:
        """Serialize the current game state to a JSON save file."""
        app = as_textual_app(self)
        host = as_mixin_host(self)
        if not host.engine or not host.engine.state.story_title or not host.engine.state.current_node:
            app.notify("Nothing to save yet.", severity="warning", timeout=2)
            return

        os.makedirs(constants.SAVES_DIR, exist_ok=True)
        safe_title = "".join(
            c if c.isalnum() or c in " _-" else "_" for c in host.engine.state.story_title
        )
        save_path = os.path.join(
            constants.SAVES_DIR,
            f"{safe_title}_turn{host.engine.state.turn_count}.json",
        )

        save_data = host.engine.get_save_data()
        journal_list = app.query_one("#journal-list", ListView)
        current_turn_text = (
            host.engine.state.current_node.narrative
            if host.engine.state.current_node is not None
            else host._current_turn_text
        )
        save_data["ui_state"] = {
            "current_story_text": host._current_story,
            "journal_entries": [
                {
                    "label": str(item.query_one(Label).render().plain),
                    "scene_index": item.scene_index,
                    "entry_kind": getattr(item, "entry_kind", "choice"),
                }
                for item in journal_list.query(JournalListItem)
            ],
            "current_turn_text": current_turn_text,
            "mood": host.mood,
            "journal_panel_collapsed": app.query_one("#journal-panel", Container).has_class("panel-collapsed"),
            "story_map_panel_collapsed": app.query_one("#story-map-panel", Container).has_class("panel-collapsed"),
        }

        try:
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(save_data, f, indent=2, ensure_ascii=False)
            app.notify(f"Game saved to {save_path}", severity="information", timeout=3)
        except OSError as e:
            app.notify(f"Save failed: {e}", severity="error", timeout=3)

    def action_load_game(self) -> None:
        """Show available save files and load a selected one."""
        app = as_textual_app(self)
        if not os.path.isdir(constants.SAVES_DIR):
            app.notify("No saves found.", severity="warning", timeout=2)
            return

        save_files = sorted(
            [f for f in os.listdir(constants.SAVES_DIR) if f.endswith(".json")],
            key=lambda f: os.path.getmtime(os.path.join(constants.SAVES_DIR, f)),
            reverse=True,
        )
        if not save_files:
            app.notify("No saves found.", severity="warning", timeout=2)
            return

        from cyoa.ui.components import LoadGameScreen

        def on_selected(save_file: str | None) -> None:
            if save_file:
                self._restore_from_save(os.path.join(constants.SAVES_DIR, save_file))

        app.push_screen(LoadGameScreen(save_files), on_selected)

    def _restore_from_save(self, save_path: str) -> None:
        """Load game state via the engine."""
        app = as_textual_app(self)
        host = as_mixin_host(self)
        try:
            with open(save_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            app.notify(f"Load failed: {e}", severity="error", timeout=3)
            return

        if not host.engine:
            return

        # Cancel speculative generation before hydrating; keep core UI workers alive.
        app.workers.cancel_group(app, "speculation")

        ui_state = self._coerce_ui_state(data.get("ui_state"))
        host.engine.load_save_data(data)

        current_story_text = ui_state.get("current_story_text")
        host._current_story = (
            current_story_text if isinstance(current_story_text, str) and current_story_text else constants.LOADING_ART
        )
        current_turn_text = ui_state.get("current_turn_text")
        host._current_turn_text = (
            current_turn_text if isinstance(current_turn_text, str) else host._current_story
        )
        host._loading_suffix_shown = False
        mood = ui_state.get("mood")
        host.mood = mood if isinstance(mood, str) else "default"

        # Sync UI
        container = app.query_one("#story-container", VerticalScroll)
        for md in container.query(Markdown):
            md.remove()

        new_turn = Markdown(host._current_turn_text, classes="story-turn")
        container.mount(new_turn, before="#scene-art")
        host._current_turn_widget = new_turn

        host._scroll_to_bottom()

        # U8 Fix: If loaded node is empty (error case), provide a way out
        choices_container = app.query_one("#choices-container", Container)
        choices_container.remove_children()
        if host.engine.state.current_node:
            host._mount_choice_buttons(host.engine.state.current_node, choices_container, False)
        else:
            choices_container.mount(Button("✦ Start a New Adventure", id="btn-new-adventure", variant="success"))

        journal_list = app.query_one("#journal-list", ListView)
        journal_list.clear()
        for entry in self._coerce_journal_entries(ui_state.get("journal_entries")):
            label = entry.get("label")
            journal_list.append(
                JournalListItem(
                    Label(label if isinstance(label, str) and label else "Unknown Turn"),
                    scene_index=self._coerce_scene_index(entry.get("scene_index", 0)),
                    entry_kind=(
                        entry.get("entry_kind")
                        if isinstance(entry.get("entry_kind"), str)
                        else "choice"
                    ),
                )
            )

        journal_panel = app.query_one("#journal-panel", Container)
        story_map_panel = app.query_one("#story-map-panel", Container)
        journal_collapsed = ui_state.get("journal_panel_collapsed")
        story_map_collapsed = ui_state.get("story_map_panel_collapsed")
        journal_panel.set_class(journal_collapsed is not False, "panel-collapsed")
        story_map_panel.set_class(story_map_collapsed is not False, "panel-collapsed")

        app.notify(
            f"Loaded save from Turn {host.engine.state.turn_count}.",
            severity="information",
            timeout=3,
        )
