from __future__ import annotations

import asyncio
from typing import Any, Protocol, cast

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
    compact_layout: bool
    typewriter_enabled: bool
    typewriter_speed: str
    _current_story: str
    _current_turn_text: str
    _loading_suffix_shown: bool
    _is_shutting_down: bool
    _current_turn_widget: Markdown
    _typewriter_queue: asyncio.Queue[str]
    _typewriter_active_chunk: list[str]
    _is_typing: bool
    _last_stats_snapshot: dict[str, int] | None

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
    def speculate_all_choices(self, node: StoryNode) -> Any: ...


def as_textual_app(value: object) -> App[Any]:
    """Cast a mixin host to the Textual app API it runs inside."""

    return cast(App[Any], value)


def as_mixin_host(value: object) -> CYOAAppMixinContract:
    """Cast a mixin host to the local app contract shared by mixins."""

    return cast(CYOAAppMixinContract, value)
