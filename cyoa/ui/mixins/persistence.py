import json
import logging
import os
from textual.app import App
from textual.widgets import Markdown, Button, ListView
from cyoa.core import constants

logger = logging.getLogger(__name__)

class PersistenceMixin:
    """Mixin for save/load game persistence."""

    def action_save_game(self) -> None:
        """Serialize the current game state to a JSON save file."""
        assert isinstance(self, App)
        if not self.engine or not self.engine.state.story_title or not self.engine.state.current_node:
            self.notify("Nothing to save yet.", severity="warning", timeout=2)
            return

        os.makedirs(constants.SAVES_DIR, exist_ok=True)
        safe_title = "".join(
            c if c.isalnum() or c in " _-" else "_" for c in self.engine.state.story_title
        )
        save_path = os.path.join(constants.SAVES_DIR, f"{safe_title}_turn{self.engine.state.turn_count}.json")

        save_data = self.engine.get_save_data()

        # UI-specific cleanup: strip transient loading indicators before persistence
        story_text = self._current_story
        suffix = "\n\n*(The ancient texts are shifting...)*"
        if story_text.endswith(suffix):
            story_text = story_text[: -len(suffix)]

        save_data["current_story_text"] = story_text

        try:
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(save_data, f, indent=2, ensure_ascii=False)
            self.notify(f"💾 Game saved to {save_path}", severity="information", timeout=3)
        except OSError as e:
            self.notify(f"Save failed: {e}", severity="error", timeout=3)

    def action_load_game(self) -> None:
        """Show available save files and load a selected one."""
        assert isinstance(self, App)
        if not os.path.isdir(constants.SAVES_DIR):
            self.notify("No saves found.", severity="warning", timeout=2)
            return

        save_files = sorted(
            [f for f in os.listdir(constants.SAVES_DIR) if f.endswith(".json")],
            key=lambda f: os.path.getmtime(os.path.join(constants.SAVES_DIR, f)),
            reverse=True,
        )
        if not save_files:
            self.notify("No saves found.", severity="warning", timeout=2)
            return

        from cyoa.ui.components import LoadGameScreen

        def on_selected(save_file: str | None) -> None:
            if save_file:
                self._restore_from_save(os.path.join(constants.SAVES_DIR, save_file))

        self.push_screen(LoadGameScreen(save_files), on_selected)

    def _restore_from_save(self, save_path: str) -> None:
        """Load game state via the engine."""
        assert isinstance(self, App)
        try:
            with open(save_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self.notify(f"Load failed: {e}", severity="error", timeout=3)
            return

        if not self.engine:
            return

        # A8 Fix: Cancel any background workers before hydrating new state
        self.workers.cancel_all()

        self._current_story = data.get("current_story_text", constants.LOADING_ART)
        self._current_turn_text = self._current_story
        self._loading_suffix_shown = False

        self.engine.load_save_data(data)

        # Sync UI
        container = self.query_one("#story-container")
        for md in container.query(Markdown):
            md.remove()

        new_turn = Markdown(self._current_turn_text, classes="story-turn")
        container.mount(new_turn, before="#scene-art")
        self._current_turn_widget = new_turn

        self._scroll_to_bottom()

        # U8 Fix: If loaded node is empty (error case), provide a way out
        choices_container = self.query_one("#choices-container")
        choices_container.remove_children()
        if self.engine.state.current_node:
            self._mount_choice_buttons(self.engine.state.current_node, choices_container, False)
        else:
            choices_container.mount(Button("✦ Start a New Adventure", id="btn-new-adventure", variant="success"))

        self.query_one("#journal-list", ListView).clear()

        self.notify(
            f"📂 Loaded save from Turn {self.engine.state.turn_count}.",
            severity="information",
            timeout=3,
        )
