import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from queue import Empty
from typing import Any, cast

from textual.containers import Container, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Button, Label, ListView, Markdown

from cyoa.core import constants
from cyoa.ui.commands import ExportStoryCommand, SaveGameCommand, UICommandContext
from cyoa.ui.components import JournalListItem
from cyoa.ui.mixins.contracts import (
    as_command_host,
    as_mixin_host,
    as_persistence_owner,
    as_textual_app,
)

logger = logging.getLogger(__name__)


class PersistenceMixin:
    """Mixin for save/load game persistence."""

    @staticmethod
    def _autosave_file_path() -> str:
        return os.path.join(constants.SAVES_DIR, "autosave_latest.json")

    @staticmethod
    def _exports_dir() -> str:
        return os.path.join(constants.SAVES_DIR, "exports")

    @staticmethod
    def _resolve_save_title(host: object) -> str | None:
        """Return a stable title for save payloads even if startup title generation lags."""
        mixin_host = as_mixin_host(host)
        if not mixin_host.engine:
            return None

        story_title = mixin_host.engine.state.story_title
        if isinstance(story_title, str) and story_title.strip():
            return story_title

        current_node = mixin_host.engine.state.current_node
        node_title = current_node.title if current_node is not None else None
        if isinstance(node_title, str) and node_title.strip():
            mixin_host.engine.state.story_title = node_title
            return node_title

        fallback_title = "Untitled Adventure" if current_node is not None else None
        if fallback_title is not None:
            mixin_host.engine.state.story_title = fallback_title
        return fallback_title

    @staticmethod
    def _query_optional_container(app: object, selector: str) -> Container | None:
        """Return a mounted container when available, otherwise tolerate early restore timing."""
        textual_app = as_textual_app(app)
        try:
            return textual_app.query_one(selector, Container)
        except NoMatches:
            return None

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
        engine_node = mixin_host.engine.state.current_node if mixin_host.engine else None
        if engine_node is not None and engine_node.narrative:
            mixin_host._current_turn_text = engine_node.narrative
            if mixin_host._story_segments:
                for segment in reversed(mixin_host._story_segments):
                    if segment.get("kind") == "story_turn":
                        segment["text"] = engine_node.narrative
                        break
                mixin_host._current_story = self._render_story_segments(
                    self._coerce_story_segments(mixin_host._story_segments)
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

        mixin_host._refresh_story_timeline_classes()

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

        journal_panel = self._query_optional_container(app, "#journal-panel")
        story_map_panel = self._query_optional_container(app, "#story-map-panel")
        journal_collapsed = ui_state.get("journal_panel_collapsed")
        story_map_collapsed = ui_state.get("story_map_panel_collapsed")
        if journal_panel is not None:
            journal_panel.set_class(journal_collapsed is not False, "panel-collapsed")
        if story_map_panel is not None:
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
        SaveGameCommand().execute(
            UICommandContext(
                app=as_textual_app(self),
                host=as_command_host(self),
                owner=as_persistence_owner(self),
            )
        )

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
        try:
            with open(save_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            app.notify(f"Load failed: {e}", severity="error", timeout=3)
            return
        self._restore_from_payload(data, source_label="Loaded save")

    def _restore_from_payload(self, data: dict[str, object], *, source_label: str) -> None:
        """Hydrate the app from an in-memory save payload."""
        app = as_textual_app(self)
        host = as_mixin_host(self)
        if not host.engine:
            return

        self._clear_restore_runtime_state(host, app)
        ui_state = self._coerce_ui_state(data.get("ui_state"))
        host.engine.load_save_data(data)
        host.invalidate_scene_caches(keep_scene_id=host.engine.state.current_scene_id)
        host.turn_count = host.engine.state.turn_count
        host._redo_payloads.clear()
        self._restore_story_state(host, ui_state)
        self._restore_story_widgets(host, app)

        choices_container = app.query_one("#choices-container", Container)
        choices_container.remove_children()
        if host.engine.state.current_node:
            host._mount_choice_buttons(host.engine.state.current_node, choices_container, False)
        else:
            choices_container.mount(
                Button(
                    "✦ Start a New Adventure",
                    id="btn-new-adventure",
                    variant="success",
                )
            )
        host.apply_ui_theme()
        self._restore_journal_and_panels(app, ui_state)
        story_map_panel = self._query_optional_container(app, "#story-map-panel")
        if story_map_panel is not None and not story_map_panel.has_class("panel-collapsed"):
            host.update_story_map()

        app.notify(f"{source_label} from Turn {host.engine.state.turn_count}.", severity="information", timeout=3)
        self._sync_prompt_status(host, app)

    def _build_save_payload(self, host: object, app: object) -> dict[str, object]:
        """Build a unified save payload for manual saves and autosaves."""
        mixin_host = as_mixin_host(host)
        textual_app = as_textual_app(app)
        if not mixin_host.engine:
            return {}

        save_data = mixin_host.engine.get_save_data()
        journal_list = textual_app.query_one("#journal-list", ListView)
        story_segments = self._snapshot_story_segments(mixin_host)
        current_turn_text = (
            mixin_host.engine.state.current_node.narrative
            if mixin_host.engine.state.current_node is not None
            else mixin_host._current_turn_text
        )
        if story_segments:
            for segment in reversed(story_segments):
                if segment["kind"] == "story_turn":
                    segment["text"] = current_turn_text
                    break
        current_story_text = self._render_story_segments(story_segments) if story_segments else mixin_host._current_story
        save_data["ui_state"] = {
            "current_story_text": current_story_text,
            "story_segments": story_segments,
            "journal_entries": [
                {
                    "label": item.label_text,
                    "scene_index": item.scene_index,
                    "entry_kind": getattr(item, "entry_kind", "choice"),
                }
                for item in journal_list.query(JournalListItem)
            ],
            "current_turn_text": current_turn_text,
            "active_turn": mixin_host.engine.state.turn_count,
            "mood": mixin_host.mood,
            "journal_panel_collapsed": textual_app.query_one("#journal-panel", Container).has_class(
                "panel-collapsed"
            ),
            "story_map_panel_collapsed": textual_app.query_one("#story-map-panel", Container).has_class(
                "panel-collapsed"
            ),
        }
        save_data["saved_at"] = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        return save_data

    @staticmethod
    def _write_json_payload(path: str, payload: dict[str, object]) -> None:
        """Persist a JSON payload to disk."""
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except OSError as exc:
            logger.warning("Unable to write persistence payload to %s: %s", path, exc)

    def _sync_prompt_status(self, host: object, app: object) -> None:
        """Keep the status bar aligned with current prompt directives."""
        mixin_host = as_mixin_host(host)
        if not mixin_host.engine or not mixin_host.engine.story_context:
            return
        from cyoa.ui.components import StatusDisplay

        textual_app = as_textual_app(app)
        textual_app.query_one(StatusDisplay).directives = list(mixin_host.engine.story_context.directives)

    def _create_autosave(self, host: object, app: object) -> None:
        """Persist the latest playable state as an autosave."""
        mixin_host = as_mixin_host(host)
        if not mixin_host.engine or mixin_host.engine.state.current_node is None:
            return
        if (
            mixin_host._last_manual_save_turn == mixin_host.engine.state.turn_count
            and (
                mixin_host._last_manual_save_scene_id is None
                or mixin_host._last_manual_save_scene_id == mixin_host.engine.state.current_scene_id
            )
        ):
            return

        mixin_host.action_skip_typewriter()
        payload = self._build_save_payload(host, app)
        payload["autosave"] = True
        self._write_json_payload(self._autosave_file_path(), payload)

    def _autosave_path(self) -> str | None:
        """Return an existing autosave path when present."""
        path = self._autosave_file_path()
        return path if os.path.exists(path) else None

    def _discard_autosave(self) -> None:
        """Delete the current autosave file when the user rejects recovery."""
        path = self._autosave_file_path()
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError as exc:
                logger.warning("Unable to remove autosave %s: %s", path, exc)

    def _prompt_autosave_recovery(self, autosave_path: str) -> None:
        """Offer startup choices when an autosave is available."""
        app = as_textual_app(self)
        def on_selected(selection: str | None) -> None:
            self._handle_startup_recovery_choice(selection, autosave_path)

        from cyoa.ui.components import StartupChoiceScreen

        app.push_screen(
            StartupChoiceScreen(
                "A previous session was found.\n\nResume the saved adventure or start a new game."
            ),
            on_selected,
        )

    def _handle_startup_recovery_choice(self, selection: str | None, autosave_path: str) -> None:
        """Dispatch the startup choice into restore or fresh-start flow."""
        app = as_textual_app(self)
        host = as_mixin_host(self)
        runtime = cast(Any, self)

        if selection == "resume":
            host._startup_timer = app.set_timer(0.1, lambda: self._restore_autosave_session(autosave_path))
        elif selection == "new":
            self._discard_autosave()
            host._startup_timer = app.set_timer(
                0.1,
                lambda: runtime.initialize_and_start(host.model_path),
            )

    def _restore_autosave_session(self, autosave_path: str) -> None:
        """Initialize the app and then hydrate from the autosave."""
        host = as_mixin_host(self)
        cast(Any, self).initialize_and_start(host.model_path)
        as_textual_app(self).set_timer(0.8, lambda: self._finish_autosave_restore(autosave_path))

    def _finish_autosave_restore(self, autosave_path: str) -> None:
        """Retry autosave restoration until the engine is ready."""
        host = as_mixin_host(self)
        app = as_textual_app(self)
        if host.engine is None or host.engine.state.current_node is None:
            app.set_timer(0.2, lambda: self._finish_autosave_restore(autosave_path))
            return
        self._restore_from_save(autosave_path)

    def action_export_story(self) -> None:
        """Export the current live session to Markdown and JSON timeline files."""
        ExportStoryCommand().execute(
            UICommandContext(
                app=as_textual_app(self),
                host=as_command_host(self),
                owner=as_persistence_owner(self),
            )
        )

    def export_save_file(self, save_path: str) -> tuple[str, str]:
        """Export an existing named save file into Markdown and JSON timeline files."""
        with open(save_path, encoding="utf-8") as f:
            payload = json.load(f)
        title = str(payload.get("story_title") or os.path.splitext(os.path.basename(save_path))[0])
        return self._write_export_files(payload, title)

    def _write_export_files(self, payload: dict[str, object], title: str) -> tuple[str, str]:
        """Write paired Markdown and JSON exports for a story payload."""
        os.makedirs(self._exports_dir(), exist_ok=True)
        safe_title = "".join(c if c.isalnum() or c in " _-" else "_" for c in title).strip() or "adventure"
        stem = os.path.join(self._exports_dir(), safe_title)
        markdown_path = f"{stem}.md"
        json_path = f"{stem}.timeline.json"
        markdown = self._render_markdown_export(payload)
        timeline_payload = self._build_timeline_export(payload)
        with open(markdown_path, "w", encoding="utf-8") as f:
            f.write(markdown)
        self._write_json_payload(json_path, timeline_payload)
        return markdown_path, json_path

    def _render_markdown_export(self, payload: dict[str, object]) -> str:
        """Render a readable Markdown export from a save payload."""
        ui_state = self._coerce_ui_state(payload.get("ui_state"))
        story_segments = self._coerce_story_segments(ui_state.get("story_segments"))
        lines = [f"# {payload.get('story_title') or 'Untitled Adventure'}", ""]
        directives = payload.get("prompt_config", {})
        if isinstance(directives, dict):
            active = directives.get("directives")
            if isinstance(active, list) and active:
                lines.append("## Active Directives")
                lines.extend(f"- {directive}" for directive in active if isinstance(directive, str))
                lines.append("")
        lines.append("## Story")
        if story_segments:
            for segment in story_segments:
                if segment["kind"] == "player_choice":
                    lines.append(f"> {segment['text']}")
                elif segment["kind"] == "branch_marker":
                    lines.append(f"---\n{segment['text']}")
                else:
                    lines.append(segment["text"])
                lines.append("")
        else:
            current_story = ui_state.get("current_story_text")
            if isinstance(current_story, str) and current_story:
                lines.append(current_story)
                lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _build_timeline_export(self, payload: dict[str, object]) -> dict[str, object]:
        """Build the machine-readable JSON export."""
        ui_state = self._coerce_ui_state(payload.get("ui_state"))
        return {
            "story_title": payload.get("story_title"),
            "turn_count": payload.get("turn_count"),
            "inventory": payload.get("inventory"),
            "player_stats": payload.get("player_stats"),
            "timeline_metadata": payload.get("timeline_metadata"),
            "story_segments": self._coerce_story_segments(ui_state.get("story_segments")),
            "journal_entries": self._coerce_journal_entries(ui_state.get("journal_entries")),
            "prompt_config": payload.get("prompt_config"),
            "saved_at": payload.get("saved_at"),
        }
