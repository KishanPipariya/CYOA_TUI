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
from cyoa.core.support import open_private_text_file
from cyoa.ui import persistence_payloads as payloads
from cyoa.ui.commands import ExportStoryCommand, SaveGameCommand, UICommandContext
from cyoa.ui.components import JournalListItem
from cyoa.ui.mixins.contracts import (
    as_command_host,
    as_mixin_host,
    as_persistence_owner,
    as_textual_app,
)
from cyoa.ui.presenters import (
    classify_ending_type,
    format_ending_type_label,
    identify_newly_unlocked_hidden_achievements,
)

logger = logging.getLogger(__name__)


class PersistenceMixin:
    """Mixin for save/load game persistence."""

    _REQUIRED_SAVE_KEYS = {
        "starting_prompt",
        "context_history",
        "prompt_config",
        "turn_count",
        "inventory",
        "player_stats",
        "current_node",
        "ui_state",
        "saved_at",
    }
    _REQUIRED_UI_STATE_KEYS = {
        "current_story_text",
        "story_segments",
        "journal_entries",
        "current_turn_text",
        "active_turn",
        "mood",
        "journal_panel_collapsed",
        "story_map_panel_collapsed",
    }

    @staticmethod
    def _autosave_file_path() -> str:
        return os.path.join(constants.SAVES_DIR, "autosave_latest.json")

    @staticmethod
    def _exports_dir() -> str:
        return os.path.join(constants.SAVES_DIR, "exports")

    @staticmethod
    def _run_archive_path() -> str:
        return os.path.join(constants.SAVES_DIR, "run_archive.json")

    @staticmethod
    def _clone_payload(payload: dict[str, object]) -> dict[str, object]:
        return payloads.clone_payload(payload)

    @classmethod
    def _list_manual_save_files(cls) -> list[str]:
        """Return loadable user saves, excluding the internal autosave slot."""
        if not os.path.isdir(constants.SAVES_DIR):
            return []

        excluded_names = {
            os.path.basename(cls._autosave_file_path()),
            os.path.basename(cls._run_archive_path()),
        }
        return sorted(
            [
                filename
                for filename in os.listdir(constants.SAVES_DIR)
                if filename.endswith(".json") and filename not in excluded_names
            ],
            key=lambda filename: os.path.getmtime(os.path.join(constants.SAVES_DIR, filename)),
            reverse=True,
        )

    @staticmethod
    def _resolve_save_title(host: object) -> str | None:
        """Return a stable title for save payloads even if startup title generation lags."""
        mixin_host = as_mixin_host(host)
        if not mixin_host.engine:
            return None

        current_node = mixin_host.engine.state.current_node
        node_title = current_node.title if current_node is not None else None
        story_title = mixin_host.engine.state.story_title
        turn_count = getattr(mixin_host.engine.state, "turn_count", 0)
        if (
            turn_count <= 1
            and isinstance(node_title, str)
            and node_title.strip()
            and (
                not isinstance(story_title, str)
                or not story_title.strip()
                or story_title.strip() != node_title.strip()
            )
        ):
            mixin_host.engine.state.story_title = node_title
            return node_title

        if isinstance(story_title, str) and story_title.strip():
            return story_title

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
                {"kind": segment["kind"], "text": segment["text"]} for segment in story_segments
            ]
            mixin_host._current_story = (
                self._render_story_segments(story_segments) or constants.LOADING_ART
            )
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
        return payloads.coerce_ui_state(payload)

    @classmethod
    def _validate_save_payload(cls, payload: object) -> dict[str, object]:
        return payloads.validate_save_payload(payload)

    @classmethod
    def _validate_ui_state(cls, payload: object) -> None:
        payloads.validate_ui_state(payload)

    @staticmethod
    def _require_payload_object(payload: object) -> dict[str, object]:
        return payloads.require_dict(payload, "save payload must be a JSON object")

    @staticmethod
    def _require_dict(payload: object, message: str) -> dict[str, object]:
        return payloads.require_dict(payload, message)

    @staticmethod
    def _require_keys(payload: dict[str, object], required_keys: set[str], label: str) -> None:
        payloads.require_keys(payload, required_keys, label)

    @staticmethod
    def _validate_engine_save_fields(payload: dict[str, object]) -> None:
        payloads.validate_engine_save_fields(payload)

    @staticmethod
    def _validate_ui_scalar_fields(payload: dict[str, object]) -> None:
        payloads.validate_ui_scalar_fields(payload)

    @classmethod
    def _validate_story_segments(cls, payload: object) -> None:
        payloads.validate_story_segments(payload)

    @staticmethod
    def _validate_journal_entries(journal_entries: object) -> None:
        payloads.validate_journal_entries(journal_entries)

    @classmethod
    def _validate_restore_points(cls, restore_points: object) -> None:
        payloads.validate_restore_points(restore_points)

    @staticmethod
    def _coerce_journal_entries(payload: object) -> list[dict[str, object]]:
        return payloads.coerce_journal_entries(payload)

    @staticmethod
    def _coerce_run_archive_entries(payload: object) -> list[dict[str, object]]:
        return payloads.coerce_run_archive_entries(payload)

    @staticmethod
    def _coerce_restore_points(payload: object) -> dict[str, dict[str, object]]:
        return payloads.coerce_restore_points(payload)

    @staticmethod
    def _coerce_story_segments(payload: object) -> list[dict[str, str]]:
        return payloads.coerce_story_segments(payload)

    @staticmethod
    def _render_story_segments(segments: list[dict[str, str]]) -> str:
        return payloads.render_story_segments(segments)

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
        return payloads.coerce_scene_index(value)

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
        save_files = self._list_manual_save_files()
        if not save_files:
            app.notify("No saves found.", severity="warning", timeout=2)
            return

        from cyoa.ui.components import LoadGameScreen

        def on_selected(save_file: str | None) -> None:
            if save_file:
                restore_path = os.path.join(constants.SAVES_DIR, save_file)
                if cast(Any, self).should_confirm_high_impact_action("load_game"):
                    from cyoa.ui.components import ConfirmScreen

                    cast(Any, app)._push_modal_screen(
                        ConfirmScreen(
                            f"[b]Load save '{save_file}'?[/b]\n\nThe current unsaved run state will be replaced with that save."
                        ),
                        lambda confirmed: (
                            self._restore_from_save(restore_path) if confirmed else None
                        ),
                    )
                    return
                self._restore_from_save(restore_path)

        cast(Any, app)._push_modal_screen(LoadGameScreen(save_files), on_selected)

    def _restore_from_save(self, save_path: str) -> None:
        """Load game state via the engine."""
        app = as_textual_app(self)
        try:
            with open(save_path, encoding="utf-8") as f:
                data = json.load(f)
            data = self._validate_save_payload(data)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            app.notify(f"Load failed: {e}", severity="error", timeout=3)
            return
        self._restore_from_payload(data, source_label="Loaded save")

    def _restore_from_payload(
        self,
        data: dict[str, object],
        *,
        source_label: str,
        preserve_restore_points: bool = False,
    ) -> None:
        """Hydrate the app from an in-memory save payload."""
        app = as_textual_app(self)
        host = as_mixin_host(self)
        if not host.engine:
            return

        try:
            data = self._validate_save_payload(data)
        except ValueError as exc:
            app.notify(f"Load failed: {exc}", severity="error", timeout=3)
            return

        self._clear_restore_runtime_state(host, app)
        ui_state = cast(dict[str, object], data["ui_state"])
        host.engine.load_save_data(data)
        host.invalidate_scene_caches(keep_scene_id=host.engine.state.current_scene_id)
        host.turn_count = host.engine.state.turn_count
        host._redo_payloads.clear()
        self._restore_story_state(host, ui_state)
        self._restore_story_widgets(host, app)
        if preserve_restore_points:
            host._bookmark_payloads = {
                name: self._clone_payload(payload)
                for name, payload in host._bookmark_payloads.items()
            }
        else:
            host._bookmark_payloads = {
                name: self._clone_payload(payload)
                for name, payload in self._coerce_restore_points(data.get("restore_points")).items()
            }

        focus_target = host._capture_focus_target()
        choices_container = app.query_one("#choices-container", Container)
        choices_container.remove_children()
        if host.engine.state.current_node:
            host._mount_choice_buttons(
                host.engine.state.current_node,
                choices_container,
                False,
                focus_target=focus_target,
            )
        else:
            choices_container.mount(
                Button(
                    "✦ Start a New Adventure",
                    id="btn-new-adventure",
                    variant="success",
                )
            )
            host._restore_focus_target(focus_target, fallback="choices")
        host.apply_ui_theme()
        self._restore_journal_and_panels(app, ui_state)
        story_map_panel = self._query_optional_container(app, "#story-map-panel")
        if story_map_panel is not None and not story_map_panel.has_class("panel-collapsed"):
            host.update_story_map()

        app.notify(
            f"{source_label} from Turn {host.engine.state.turn_count}.",
            severity="information",
            timeout=3,
        )
        self._sync_prompt_status(host, app)

    def _build_save_payload(
        self,
        host: object,
        app: object,
        *,
        include_restore_points: bool = True,
    ) -> dict[str, object]:
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
        current_story_text = (
            self._render_story_segments(story_segments)
            if story_segments
            else mixin_host._current_story
        )
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
            "story_map_panel_collapsed": textual_app.query_one(
                "#story-map-panel", Container
            ).has_class("panel-collapsed"),
        }
        save_data["saved_at"] = (
            datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        )
        if include_restore_points:
            save_data["restore_points"] = {
                name: self._clone_payload(payload)
                for name, payload in mixin_host._bookmark_payloads.items()
            }
        return save_data

    @staticmethod
    def _write_json_payload(path: str, payload: dict[str, object]) -> None:
        """Persist a JSON payload to disk."""
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open_private_text_file(path, "w") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except OSError as exc:
            logger.warning("Unable to write persistence payload to %s: %s", path, exc)

    def _load_run_archive(self) -> list[dict[str, object]]:
        """Return the archived completed runs, tolerating a missing or malformed file."""
        path = self._run_archive_path()
        if not os.path.exists(path):
            return []
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Unable to read run archive %s: %s", path, exc)
            return []
        return self._coerce_run_archive_entries(payload)

    def _write_run_archive(self, entries: list[dict[str, object]]) -> None:
        """Persist the archived completed runs as a JSON list."""
        path = self._run_archive_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open_private_text_file(path, "w") as handle:
                json.dump(entries, handle, indent=2, ensure_ascii=False)
        except OSError as exc:
            logger.warning("Unable to write run archive to %s: %s", path, exc)

    def _build_completed_run_summary(
        self,
        host: object,
        app: object,
        *,
        ending_narrative: str,
    ) -> dict[str, object]:
        """Capture a compact summary for a completed ending."""
        mixin_host = as_mixin_host(host)
        engine = mixin_host.engine
        if engine is None:
            return {}

        state = engine.state
        title = state.story_title or "Untitled Adventure"
        ending_type = classify_ending_type(
            ending_narrative,
            health=state.player_stats.get("health"),
        )
        ui_state = self._coerce_ui_state(self._build_save_payload(host, app).get("ui_state"))
        journal_entries = self._coerce_journal_entries(ui_state.get("journal_entries"))
        branch_restores = [
            entry.copy()
            for entry in state.timeline_metadata
            if entry.get("kind") == "branch_restore"
        ]
        divergence_points = sorted(
            {
                int(entry["restored_turn"])
                for entry in branch_restores
                if isinstance(entry.get("restored_turn"), int)
            }
        )

        return {
            "story_title": title,
            "completed_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "turn_count": state.turn_count,
            "current_scene_id": state.current_scene_id,
            "last_choice_text": state.last_choice_text,
            "last_resolved_choice_check": (
                state.last_resolved_choice_check.model_dump()
                if state.last_resolved_choice_check is not None
                else None
            ),
            "ending_type": ending_type,
            "ending_label": format_ending_type_label(ending_type),
            "ending_narrative": ending_narrative,
            "inventory": list(state.inventory),
            "player_stats": dict(state.player_stats),
            "objectives": [objective.model_dump() for objective in state.objectives],
            "companions": [companion.model_dump() for companion in state.companions],
            "faction_reputation": dict(state.faction_reputation),
            "npc_affinity": dict(state.npc_affinity),
            "story_flags": sorted(state.story_flags),
            "world_time": state.world_time.model_dump(),
            "campaign": state.campaign.model_dump() if state.campaign else None,
            "campaign_progress": (
                state.campaign_progress.model_dump() if state.campaign_progress else None
            ),
            "campaign_clocks": (
                [clock.model_dump() for clock in state.campaign_progress.clocks]
                if state.campaign_progress
                else []
            ),
            "timeline_metadata": [entry.copy() for entry in state.timeline_metadata],
            "branch_restores": branch_restores,
            "divergence_points": divergence_points,
            "journal_entries": journal_entries,
            "story_segments": self._coerce_story_segments(ui_state.get("story_segments")),
            "discovered_lore_count": len(state.lore_entries),
            "objective_status_counts": {
                "active": sum(1 for objective in state.objectives if objective.status == "active"),
                "completed": sum(
                    1 for objective in state.objectives if objective.status == "completed"
                ),
                "failed": sum(1 for objective in state.objectives if objective.status == "failed"),
            },
            "notification_hint": (
                f"{title} ended in {state.turn_count} turn"
                f"{'' if state.turn_count == 1 else 's'} as a {format_ending_type_label(ending_type).lower()}."
            ),
        }

    def _record_completed_run(
        self, host: object, app: object, *, ending_narrative: str
    ) -> list[dict[str, object]]:
        """Append a finished run summary to the archive and report new achievements."""
        summary = self._build_completed_run_summary(host, app, ending_narrative=ending_narrative)
        if not summary:
            return []

        entries = self._load_run_archive()
        previous_entries = [entry.copy() for entry in entries]
        entries.append(summary)
        self._write_run_archive(entries)
        unlocked = identify_newly_unlocked_hidden_achievements(previous_entries, entries)
        return [cast(dict[str, object], entry) for entry in unlocked]

    def _sync_prompt_status(self, host: object, app: object) -> None:
        """Keep the status bar aligned with current prompt directives."""
        mixin_host = as_mixin_host(host)
        if not mixin_host.engine or not mixin_host.engine.story_context:
            return
        from cyoa.ui.components import StatusDisplay

        textual_app = as_textual_app(app)
        textual_app.query_one(StatusDisplay).directives = list(
            mixin_host.engine.story_context.directives
        )

    def _create_autosave(self, host: object, app: object) -> None:
        """Persist the latest playable state as an autosave."""
        mixin_host = as_mixin_host(host)
        if not mixin_host.engine or mixin_host.engine.state.current_node is None:
            return
        if mixin_host.engine.state.turn_count <= 1:
            return
        if mixin_host._last_manual_save_turn == mixin_host.engine.state.turn_count and (
            mixin_host._last_manual_save_scene_id is None
            or mixin_host._last_manual_save_scene_id == mixin_host.engine.state.current_scene_id
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

        cast(Any, app)._push_modal_screen(
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
            host._startup_timer = app.set_timer(
                0.1, lambda: self._restore_autosave_session(autosave_path)
            )
        elif selection == "new":
            from cyoa.ui.components import ConfirmScreen

            def on_confirm(confirmed: bool | None) -> None:
                if confirmed is not True:
                    return
                self._discard_autosave()
                host._startup_timer = app.set_timer(
                    0.1,
                    lambda: runtime.initialize_and_start(host.model_path),
                )

            cast(Any, app)._push_modal_screen(
                ConfirmScreen(
                    "[b]Start a new game?[/b]\n\nThis discards the recovered autosave and any restore points saved inside it."
                ),
                on_confirm,
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

    def export_save_file(self, save_path: str) -> tuple[str, str, str]:
        """Export an existing named save file into Markdown and JSON timeline files."""
        with open(save_path, encoding="utf-8") as f:
            payload = json.load(f)
        title = str(payload.get("story_title") or os.path.splitext(os.path.basename(save_path))[0])
        return self._write_export_files(payload, title)

    def _write_export_files(
        self,
        payload: dict[str, object],
        title: str,
    ) -> tuple[str, str, str]:
        """Write Markdown, accessible text, and JSON exports for a story payload."""
        os.makedirs(self._exports_dir(), exist_ok=True)
        safe_title = (
            "".join(c if c.isalnum() or c in " _-" else "_" for c in title).strip() or "adventure"
        )
        stem = os.path.join(self._exports_dir(), safe_title)
        markdown_path = f"{stem}.md"
        accessible_path = f"{stem}.accessible.txt"
        json_path = f"{stem}.timeline.json"
        markdown = self._render_markdown_export(payload)
        accessible_text = self._render_accessible_export(payload)
        timeline_payload = self._build_timeline_export(payload)
        vault_path = self._write_obsidian_vault(payload, safe_title)
        timeline_payload["obsidian_vault"] = vault_path
        with open_private_text_file(markdown_path, "w") as f:
            f.write(markdown)
        with open_private_text_file(accessible_path, "w") as f:
            f.write(accessible_text)
        self._write_json_payload(json_path, timeline_payload)
        return markdown_path, accessible_path, json_path

    @staticmethod
    def _obsidian_safe_name(value: str) -> str:
        return payloads.obsidian_safe_name(value)

    def _write_obsidian_vault(self, payload: dict[str, object], safe_title: str) -> str:
        """Write an Obsidian-style Markdown vault with one note per scene."""
        vault_dir = os.path.join(self._exports_dir(), f"{safe_title}_vault")
        os.makedirs(vault_dir, exist_ok=True)

        story_title = str(payload.get("story_title") or "Untitled Adventure")
        records = payloads.build_obsidian_records(payload)

        note_names = [self._obsidian_safe_name(str(record["title"])) for record in records]
        index_lines = [
            f"# {story_title}",
            "",
            "## Playthrough",
        ]
        if not records:
            index_lines.append("- No recorded story turns are available.")
        else:
            index_lines.extend(f"- [[{name}]]" for name in note_names)

        state_lines = self._obsidian_state_lines(payload)
        if state_lines:
            index_lines.extend(["", "## Final State", *state_lines])

        with open_private_text_file(os.path.join(vault_dir, "Index.md"), "w") as handle:
            handle.write("\n".join(index_lines).strip() + "\n")

        for index, record in enumerate(records):
            note_name = note_names[index]
            previous_link = f"[[{note_names[index - 1]}]]" if index > 0 else ""
            next_link = f"[[{note_names[index + 1]}]]" if index < len(note_names) - 1 else ""
            lines = [
                "---",
                f"story: {story_title}",
                f"turn: {record.get('turn') or 0}",
                f"kind: {record.get('kind')}",
                "---",
                "",
                f"# {record['title']}",
                "",
                str(record["text"]),
            ]
            choices = record.get("choices")
            if isinstance(choices, list) and choices:
                lines.extend(["", "## Choice"])
                lines.extend(f"- {choice}" for choice in choices if isinstance(choice, str))
            nav_links = [link for link in (previous_link, "[[Index]]", next_link) if link]
            lines.extend(["", "## Links", " | ".join(nav_links)])
            with open_private_text_file(os.path.join(vault_dir, f"{note_name}.md"), "w") as handle:
                handle.write("\n".join(lines).strip() + "\n")

        return vault_dir

    @staticmethod
    def _obsidian_state_lines(payload: dict[str, object]) -> list[str]:
        return payloads.obsidian_state_lines(payload)

    def _render_markdown_export(self, payload: dict[str, object]) -> str:
        return payloads.render_markdown_export(payload)

    def _render_accessible_export(self, payload: dict[str, object]) -> str:
        return payloads.render_accessible_export(
            payload,
            verbosity=getattr(self, "scene_recap_verbosity", "standard"),
        )

    def _build_timeline_export(self, payload: dict[str, object]) -> dict[str, object]:
        return payloads.build_timeline_export(payload)
