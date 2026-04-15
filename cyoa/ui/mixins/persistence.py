import asyncio
import json
import logging
import os
from queue import Empty

from textual.containers import Container, VerticalScroll
from textual.widgets import Button, Label, ListView, Markdown

from cyoa.core import constants
from cyoa.ui.components import JournalListItem
from cyoa.ui.mixins.contracts import as_mixin_host, as_textual_app

logger = logging.getLogger(__name__)

class PersistenceMixin:
    """Mixin for save/load game persistence."""

    @staticmethod
    def _clear_restore_runtime_state(host: object, app: object) -> None:
        """Stop transient workers and buffered text before hydrating a save."""
        textual_app = as_textual_app(app)
        mixin_host = as_mixin_host(host)
        textual_app.workers.cancel_group(textual_app, "speculation")
        mixin_host._is_typing = False
        mixin_host._typewriter_active_chunk.clear()
        while True:
            try:
                mixin_host._typewriter_queue.get_nowait()
            except (asyncio.QueueEmpty, Empty, AttributeError):
                break

    def _restore_story_state(self, host: object, ui_state: dict[str, object]) -> None:
        """Restore flattened and structured story text from saved UI state."""
        mixin_host = as_mixin_host(host)
        current_story_text = ui_state.get("current_story_text")
        story_segments = self._coerce_story_segments(ui_state.get("story_segments"))
        if story_segments:
            mixin_host._story_segments = [
                {"kind": segment["kind"], "text": segment["text"]}
                for segment in story_segments
            ]
            mixin_host._current_story = self._render_story_segments(story_segments) or constants.LOADING_ART
            current_turn_from_segments = next(
                (
                    segment["text"]
                    for segment in reversed(story_segments)
                    if segment["kind"] == "story_turn"
                ),
                "",
            )
        else:
            mixin_host._current_story = (
                current_story_text
                if isinstance(current_story_text, str) and current_story_text
                else constants.LOADING_ART
            )
            current_turn_from_segments = ""
            mixin_host._reset_story_segments(mixin_host._current_story)

        current_turn_text = ui_state.get("current_turn_text")
        mixin_host._current_turn_text = (
            current_turn_text
            if isinstance(current_turn_text, str)
            else current_turn_from_segments or mixin_host._current_story
        )
        mixin_host._update_current_story_segment(mixin_host._current_turn_text)
        mixin_host._loading_suffix_shown = False
        mood = ui_state.get("mood")
        mixin_host.mood = mood if isinstance(mood, str) else "default"

    def _restore_story_widgets(self, host: object, app: object) -> None:
        """Rebuild the story pane from saved structured segments."""
        textual_app = as_textual_app(app)
        mixin_host = as_mixin_host(host)
        container = textual_app.query_one("#story-container", VerticalScroll)
        existing_markdown = list(container.query(Markdown))
        reusable_turn = existing_markdown[0] if existing_markdown else None
        for md in existing_markdown[1:]:
            md.remove()

        saved_segments = self._coerce_story_segments(mixin_host._story_segments)
        story_turns: list[Markdown] = []
        for index, segment in enumerate(saved_segments):
            kind = segment["kind"]
            text = segment["text"]
            if index == 0 and reusable_turn is not None and kind == "story_turn":
                reusable_turn.set_classes("story-turn")
                reusable_turn.update(text)
                mounted = reusable_turn
                story_turns.append(mounted)
                continue
            if kind in {"player_choice", "branch_marker"}:
                mounted = Markdown(text, classes="player-choice")
            else:
                mounted = Markdown(text, classes="story-turn")
                story_turns.append(mounted)
            container.mount(mounted, before="#scene-art")

        if story_turns:
            mixin_host._current_turn_widget = story_turns[-1]
        else:
            new_turn = Markdown(mixin_host._current_turn_text, classes="story-turn")
            container.mount(new_turn, before="#scene-art")
            mixin_host._current_turn_widget = new_turn

        mixin_host._scroll_to_bottom()

    def _restore_journal_and_panels(self, app: object, ui_state: dict[str, object]) -> None:
        """Restore journal entries and side-panel visibility from saved UI state."""
        textual_app = as_textual_app(app)
        journal_list = textual_app.query_one("#journal-list", ListView)
        journal_list.clear()
        for entry in self._coerce_journal_entries(ui_state.get("journal_entries")):
            label = entry.get("label")
            entry_kind = entry.get("entry_kind")
            journal_list.append(
                JournalListItem(
                    Label(label if isinstance(label, str) and label else "Unknown Turn"),
                    scene_index=self._coerce_scene_index(entry.get("scene_index", 0)),
                    entry_kind=entry_kind if isinstance(entry_kind, str) else "choice",
                    label_text=label if isinstance(label, str) and label else "Unknown Turn",
                )
            )

        journal_panel = textual_app.query_one("#journal-panel", Container)
        story_map_panel = textual_app.query_one("#story-map-panel", Container)
        journal_collapsed = ui_state.get("journal_panel_collapsed")
        story_map_collapsed = ui_state.get("story_map_panel_collapsed")
        journal_panel.set_class(journal_collapsed is not False, "panel-collapsed")
        story_map_panel.set_class(story_map_collapsed is not False, "panel-collapsed")

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
    def _coerce_story_segments(payload: object) -> list[dict[str, str]]:
        """Normalize structured story timeline entries from save payloads."""
        if not isinstance(payload, list):
            return []

        normalized: list[dict[str, str]] = []
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            kind = entry.get("kind")
            text = entry.get("text")
            if kind not in {"story_turn", "player_choice", "branch_marker"} or not isinstance(text, str):
                continue
            normalized.append({"kind": kind, "text": text})
        return normalized

    @staticmethod
    def _render_story_segments(segments: list[dict[str, str]]) -> str:
        """Rebuild the flattened story text from structured timeline segments."""
        story_text = ""
        for segment in segments:
            if segment["kind"] == "player_choice":
                if story_text:
                    story_text += "\n\n"
                story_text += f"> {segment['text']}\n\n---\n\n"
            elif segment["kind"] == "branch_marker":
                story_text += f"\n\n***\n\n{segment['text']}"
            else:
                story_text += segment["text"]
        return story_text

    def _snapshot_story_segments(self, host: object) -> list[dict[str, str]]:
        """Serialize structured timeline state, falling back to a flat story turn if needed."""
        mixin_host = as_mixin_host(host)
        segments = [
            {
                "kind": str(segment.get("kind", "story_turn")),
                "text": str(segment.get("text", "")),
            }
            for segment in mixin_host._story_segments
            if isinstance(segment, dict)
        ]
        normalized = self._coerce_story_segments(segments)
        if normalized and self._render_story_segments(normalized) == mixin_host._current_story:
            return normalized
        if mixin_host._current_story:
            return [{"kind": "story_turn", "text": mixin_host._current_story}]
        return normalized

    @staticmethod
    def _coerce_scene_index(value: object) -> int:
        """Clamp scene indexes from save files to a safe non-negative int."""
        if isinstance(value, bool):
            return 0
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, float):
            return max(0, int(value))
        if isinstance(value, (str, bytes, bytearray)):
            try:
                parsed = int(value)
            except ValueError:
                return 0
            return max(0, parsed)
        return 0

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
            "story_segments": self._snapshot_story_segments(host),
            "journal_entries": [
                {
                    "label": item.label_text,
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

        self._clear_restore_runtime_state(host, app)
        ui_state = self._coerce_ui_state(data.get("ui_state"))
        host.engine.load_save_data(data)
        self._restore_story_state(host, ui_state)
        self._restore_story_widgets(host, app)

        # U8 Fix: If loaded node is empty (error case), provide a way out
        choices_container = app.query_one("#choices-container", Container)
        choices_container.remove_children()
        if host.engine.state.current_node:
            host._mount_choice_buttons(host.engine.state.current_node, choices_container, False)
        else:
            choices_container.mount(Button("✦ Start a New Adventure", id="btn-new-adventure", variant="success"))
        self._restore_journal_and_panels(app, ui_state)

        app.notify(
            f"Loaded save from Turn {host.engine.state.turn_count}.",
            severity="information",
            timeout=3,
        )
