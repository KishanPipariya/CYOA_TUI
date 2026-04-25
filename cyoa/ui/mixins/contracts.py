from __future__ import annotations

import asyncio
from typing import Any, Literal, Protocol, cast

from textual.app import App
from textual.containers import Container
from textual.widgets import Markdown

from cyoa.core.engine import StoryEngine
from cyoa.core.models import StoryNode
from cyoa.llm.broker import ModelBroker


class CYOAAppMixinContract(Protocol):
    """Typed surface area shared across the UI mixins."""

    engine: StoryEngine | None
    generator: ModelBroker | None
    turn_count: int
    mood: str
    dark: bool
    reduced_motion: bool
    compact_layout: bool
    typewriter_enabled: bool
    typewriter_speed: str
    _current_story: str
    _current_turn_text: str
    _story_segments: list[dict[str, object]]
    _loading_suffix_shown: bool
    _is_shutting_down: bool
    _current_turn_widget: Markdown
    _typewriter_queue: asyncio.Queue[str]
    _typewriter_active_chunk: list[str]
    _is_typing: bool
    _has_rendered_first_scene: bool
    _last_stats_snapshot: dict[str, int] | None
    _startup_timer: Any | None
    _redo_payloads: list[dict[str, object]]
    _bookmark_payloads: dict[str, dict[str, object]]
    _last_manual_save_turn: int | None
    _last_manual_save_scene_id: str | None
    model_path: str

    def is_runtime_active(self) -> bool: ...
    def action_skip_typewriter(self) -> None: ...
    def show_loading(self, selected_button_id: str | None = None) -> None: ...
    def _stream_narrative(self, partial: str) -> None: ...
    def display_node(self, node: StoryNode) -> None: ...
    def update_story_map(self) -> Any: ...
    def _scroll_to_bottom(self, animate: bool = True) -> None: ...
    def _is_at_bottom(self) -> bool: ...
    def _mount_choice_buttons(
        self, node: StoryNode, choices_container: Container, is_error: bool
    ) -> None: ...
    def _reset_story_segments(self, initial_text: str) -> None: ...
    def _append_story_segment(self, kind: str, text: str) -> None: ...
    def _update_current_story_segment(self, text: str) -> None: ...
    def _current_story_turn_index(self) -> int: ...
    def _refresh_story_timeline_classes(self) -> None: ...
    def apply_ui_theme(self) -> None: ...
    def speculate_all_choices(self, node: StoryNode) -> Any: ...
    def mark_first_scene_rendered(self) -> None: ...
    def queue_notification(
        self,
        message: str,
        *,
        severity: Literal["information", "warning", "error"] = "information",
        timeout: float = 3,
        batch: bool = True,
    ) -> None: ...
    def cache_story_history(self, scene_id: str | None, history: dict[str, Any]) -> None: ...
    def get_cached_story_history(self, scene_id: str | None) -> dict[str, Any] | None: ...
    def cache_story_map(self, scene_id: str | None, tree_data: dict[str, Any]) -> None: ...
    def get_cached_story_map(self, scene_id: str | None) -> dict[str, Any] | None: ...
    def invalidate_scene_caches(self, keep_scene_id: str | None = None) -> None: ...
    def initialize_and_start(self, model_path: str) -> Any: ...
    def _sync_runtime_status(self) -> None: ...


class UICommandHost(Protocol):
    """Minimal surface area used by extracted UI commands."""

    engine: StoryEngine | None
    _redo_payloads: list[dict[str, object]]
    _current_story: str
    _current_turn_text: str
    _story_segments: list[dict[str, object]]
    _current_turn_widget: Markdown
    _last_manual_save_turn: int | None
    _last_manual_save_scene_id: str | None

    def is_runtime_active(self) -> bool: ...
    def invalidate_scene_caches(self, keep_scene_id: str | None = None) -> None: ...
    def _reset_story_segments(self, initial_text: str) -> None: ...
    def action_skip_typewriter(self) -> None: ...
    def _update_current_story_segment(self, text: str) -> None: ...
    def _trim_story_segments_for_undo(self, host: object) -> None: ...
    def _refresh_story_timeline_classes(self) -> None: ...
    def _mount_choice_buttons(
        self, node: StoryNode, choices_container: Container, is_error: bool
    ) -> None: ...
    def _scroll_to_bottom(self, animate: bool = True) -> None: ...
    def update_story_map(self) -> Any: ...


class PersistenceCommandOwner(Protocol):
    """Persistence helpers used by command objects."""

    @staticmethod
    def _resolve_save_title(host: object) -> str | None: ...
    def _build_save_payload(self, host: object, app: object) -> dict[str, object]: ...
    def _restore_from_payload(self, data: dict[str, object], *, source_label: str) -> None: ...
    @staticmethod
    def _write_json_payload(path: str, payload: dict[str, object]) -> None: ...
    def _discard_autosave(self) -> None: ...
    def _write_export_files(self, payload: dict[str, object], title: str) -> tuple[str, str]: ...


def as_textual_app(value: object) -> App[Any]:
    """Cast a mixin host to the Textual app API it runs inside."""

    return cast(App[Any], value)


def as_mixin_host(value: object) -> CYOAAppMixinContract:
    """Cast a mixin host to the local app contract shared by mixins."""

    return cast(CYOAAppMixinContract, value)


def as_command_host(value: object) -> UICommandHost:
    """Cast a mixin host to the narrower command contract."""

    return cast(UICommandHost, value)


def as_persistence_owner(value: object) -> PersistenceCommandOwner:
    """Cast a mixin host to the persistence helper contract."""

    return cast(PersistenceCommandOwner, value)
