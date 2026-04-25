import asyncio
import copy
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal, cast

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.css.query import NoMatches
from textual.events import Click, Resize
from textual.notifications import SeverityLevel
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import (
    Button,
    Footer,
    Header,
    ListView,
    Markdown,
    Static,
)

from cyoa.core import constants
from cyoa.core.engine import StoryEngine
from cyoa.core.events import Events, bus
from cyoa.core.model_download import (
    DownloadProgress,
    DownloadResult,
    ModelDownloadCancelled,
    ModelDownloadError,
    download_recommended_model,
    get_models_dir,
    recommend_model_for_current_machine,
)
from cyoa.core.models import StoryNode
from cyoa.core.preflight import (
    check_local_model_preflight,
    check_terminal_conditions,
)
from cyoa.core.support import reveal_in_file_manager, support_paths
from cyoa.core.theme_loader import list_themes
from cyoa.core.user_config import (
    UserConfig,
    load_user_config,
    reset_user_config,
    update_user_config,
)
from cyoa.db.graph_db import CYOAGraphDB
from cyoa.db.rag_memory import is_rag_diagnostics_enabled
from cyoa.llm.broker import ModelBroker
from cyoa.llm.providers import LlamaCppProvider
from cyoa.ui.components import (
    ConfirmScreen,
    FirstRunSetupScreen,
    GameWorkspace,
    JournalListItem,
    ModelDownloadScreen,
    SettingsScreen,
    StatusDisplay,
    ThemeSpinner,
)
from cyoa.ui.mixins import (
    EventsMixin,
    NavigationMixin,
    PersistenceMixin,
    RenderingMixin,
    ThemeMixin,
    TypewriterMixin,
)
from cyoa.ui.presenters import format_status_message, loading_story_text

logger = logging.getLogger(__name__)

__all__ = ["CYOAApp"]


@dataclass(slots=True)
class BufferedNotification:
    message: str
    severity: Literal["information", "warning", "error"]
    timeout: float


@dataclass(slots=True)
class NotificationHistoryEntry:
    message: str
    severity: Literal["information", "warning", "error"]


class CYOAApp(
    ThemeMixin,
    TypewriterMixin,
    PersistenceMixin,
    EventsMixin,
    NavigationMixin,
    RenderingMixin,
    App,
):
    """A Choose-Your-Adventure Textual App."""

    # Fix #8: CSS loaded from external file
    CSS_PATH = "styles.tcss"
    theme = "textual-dark"
    dark = True

    BINDINGS: ClassVar[list[Any]] = [
        Binding("up", "focus_previous_choice", "Prev Choice", show=False),
        Binding("down", "focus_next_choice", "Next Choice", show=False),
        Binding("d", "toggle_dark", "Theme", show=True),
        Binding("b", "branch_past", "Branch", show=True),
        Binding("j", "toggle_journal", "Journal", show=True),
        Binding("m", "toggle_story_map", "Map", show=True),
        Binding("h", "show_help", "Help", show=True),
        Binding("n", "repeat_latest_status", "Repeat Status", show=True),
        Binding("shift+n", "show_notification_history", "Notifications", show=False),
        Binding("o", "show_settings", "Settings", show=True),
        Binding("u", "undo", "Undo", show=True),
        Binding("y", "redo", "Redo", show=True),
        Binding("k", "create_bookmark", "Bookmark", show=True),
        Binding("p", "restore_bookmark", "Restore Mark", show=True),
        Binding("s", "save_game", "Save", show=True),
        Binding("l", "load_game", "Load", show=True),
        Binding("e", "export_story", "Export", show=True),
        Binding("q", "request_quit", "Quit", show=True),
        Binding("r", "request_restart", "Restart", show=True),
        Binding("t", "toggle_typewriter", "Typewriter", show=True),
        Binding("v", "cycle_typewriter_speed", "Speed", show=True),
        Binding("g", "cycle_generation_preset", "Preset", show=True),
        Binding("x", "edit_directives", "Directives", show=True),
        Binding("space", "skip_typewriter", "Skip", show=True),
        Binding("1", "choose('1')", "Choice 1", show=False),
        Binding("2", "choose('2')", "Choice 2", show=False),
        Binding("3", "choose('3')", "Choice 3", show=False),
        Binding("4", "choose('4')", "Choice 4", show=False),
    ]

    # Fix #4: Reactive turn counter displayed in footer
    turn_count: reactive[int] = reactive(1)
    mood: reactive[str] = reactive("default")
    typewriter_enabled: reactive[bool] = reactive(True)
    typewriter_speed: reactive[str] = reactive("normal")
    reduced_motion: reactive[bool] = reactive(False)
    high_contrast_mode: reactive[bool] = reactive(False)
    screen_reader_mode: reactive[bool] = reactive(False)
    text_scale: reactive[str] = reactive("standard")
    line_width: reactive[str] = reactive("standard")
    line_spacing: reactive[str] = reactive("standard")
    compact_layout: reactive[bool] = reactive(False)

    def __init__(
        self,
        model_path: str,
        starting_prompt: str = constants.DEFAULT_STARTING_PROMPT,
        spinner_frames: list[str] | None = None,
        accent_color: str | None = None,
        ui_theme: dict[str, str] | None = None,
        initial_world_state: dict[str, object] | None = None,
        initial_prompt_config: dict[str, object] | None = None,
        runtime_diagnostics: dict[str, str] | None = None,
        allow_headless_startup_recovery: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.model_path = model_path
        self.starting_prompt = starting_prompt
        self.spinner_frames = spinner_frames or ["[-]", "[\\]", "[|]", "[/]"]
        self._accent_color = accent_color
        self._ui_theme = ui_theme or {}
        self._initial_world_state = initial_world_state or {}
        self._initial_prompt_config = initial_prompt_config or {}
        self._runtime_diagnostics = runtime_diagnostics or {}
        self._allow_headless_startup_recovery = allow_headless_startup_recovery
        self._user_config = load_user_config()
        self._first_run_setup_pending = self._requires_first_run_setup(self._user_config)
        initial_story_text = loading_story_text(
            screen_reader_mode=getattr(self._user_config, "screen_reader_mode", False)
        )

        self.generator: ModelBroker | None = None
        self.engine: StoryEngine | None = None
        self._current_story: str = initial_story_text
        self._current_turn_text: str = initial_story_text
        self._story_segments: list[dict[str, object]] = [{"kind": "story_turn", "text": initial_story_text}]
        self._loading_suffix_shown: bool = False
        self._unsubscribers: list[Callable[[], None]] = []
        self._subscriptions_active: bool = False
        self._is_shutting_down: bool = False
        self._startup_timer: Timer | None = None
        self._post_render_warmup_timer: Any | None = None
        self._has_rendered_first_scene: bool = False
        self._optional_runtime_ready: bool = False
        self._story_history_cache: dict[str, dict[str, Any]] = {}
        self._story_map_cache: dict[str, dict[str, Any]] = {}
        self._scene_cache_limit: int = 8
        self._notification_buffer: list[BufferedNotification] = []
        self._notification_timer: Any | None = None
        self._notification_history: list[NotificationHistoryEntry] = []
        self._notification_history_limit: int = 200
        self._latest_status_message: str = "Information: Waiting for adventure updates."
        self._redo_payloads: list[dict[str, object]] = []
        self._bookmark_payloads: dict[str, dict[str, object]] = {}
        self._last_manual_save_turn: int | None = None
        self._last_manual_save_scene_id: str | None = None
        self._model_download_cancel_event: threading.Event | None = None

        # Typewriter Narrator state
        self._typewriter_queue: asyncio.Queue[str] = asyncio.Queue()
        self._typewriter_active_chunk: list[str] = []
        self._is_typing: bool = False
        self._last_stats_snapshot: dict[str, int] | None = None

        # Restore preferences
        config = self._user_config.to_ui_preferences()
        self.dark = config.get("dark", True)
        self.high_contrast_mode = config.get("high_contrast", False)
        self.reduced_motion = config.get("reduced_motion", False)
        self.screen_reader_mode = config.get("screen_reader_mode", False)
        self.text_scale = str(config.get("text_scale", "standard"))
        self.line_width = str(config.get("line_width", "standard"))
        self.line_spacing = str(config.get("line_spacing", "standard"))
        self.typewriter_enabled = config.get("typewriter", True)
        self.typewriter_speed = config.get("typewriter_speed", "normal")

        # Apply theme accent color if specified
        if self._accent_color:
            self._apply_custom_accent(self._accent_color)

    def compose(self) -> ComposeResult:
        yield Header()
        yield GameWorkspace(
            spinner_frames=self.spinner_frames,
            screen_reader_mode=self.screen_reader_mode,
            id="workspace",
        )
        yield Footer()

    def watch_turn_count(self, count: int) -> None:
        self.sub_title = f"Turn {count}" if count > 0 else ""

    async def on_mount(self) -> None:
        self._is_shutting_down = False
        self._has_rendered_first_scene = False
        self._optional_runtime_ready = False
        self.query_one("#action-panel", Container).border_title = "Choices"
        self.query_one("#story-container", VerticalScroll).border_title = "Story"

        self._current_turn_widget = self.query_one("#initial-turn", Markdown)
        self._refresh_story_timeline_classes()
        self._set_compact_layout(self.size.width)
        status_display = self.query_one(StatusDisplay)
        status_display.generation_preset = "balanced"
        status_display.screen_reader_mode = self.screen_reader_mode
        status_display.latest_status = self._latest_status_message
        self._sync_runtime_status()
        self.apply_ui_theme()
        self.set_class(self.reduced_motion, "reduced-motion")
        self.set_class(self.screen_reader_mode, "screen-reader-mode")
        self._apply_reading_preference_classes()

        self._subscribe_engine_events()
        self._typewriter_worker()
        if self._first_run_setup_pending:
            if self.is_headless:
                self._apply_first_run_selection("mock")
                self._resume_startup_flow()
                return
            self._present_first_run_setup()
            return
        self._resume_startup_flow()

    def on_resize(self, event: Resize) -> None:
        self._set_compact_layout(event.size.width)

    def watch_reduced_motion(self, enabled: bool) -> None:
        self.set_class(enabled, "reduced-motion")
        if enabled:
            self.action_skip_typewriter()

    def watch_high_contrast_mode(self, enabled: bool) -> None:
        self.set_class(enabled, "high-contrast-mode")
        if enabled:
            self._apply_custom_accent("#FFD400")
        else:
            self.watch_mood(self.mood, self.mood)
        try:
            self.apply_ui_theme()
        except Exception:
            return

    def watch_screen_reader_mode(self, enabled: bool) -> None:
        self.set_class(enabled, "screen-reader-mode")
        try:
            status_display = self.query_one(StatusDisplay)
        except Exception:
            return
        status_display.screen_reader_mode = enabled
        loading_text = loading_story_text(screen_reader_mode=enabled)
        other_loading_text = loading_story_text(screen_reader_mode=not enabled)
        if self._current_story == other_loading_text and self._current_turn_text == other_loading_text:
            self._current_story = loading_text
            self._current_turn_text = loading_text
            self._reset_story_segments(loading_text)
            try:
                self._current_turn_widget.update(loading_text)
            except Exception:
                pass
        if enabled:
            try:
                self.query_one("#scene-art", Static).add_class("hidden")
            except Exception:
                return
        elif self.engine is not None and self.engine.state.current_node is not None:
            self._update_scene_art(
                self.engine.state.current_node.narrative,
                self.engine.state.current_node.narrative.startswith(constants.ERROR_NARRATIVE_PREFIX),
            )
        if (
            self.engine is not None
            and self.engine.state.current_node is not None
            and not self._loading_suffix_shown
        ):
            try:
                choices_container = self.query_one("#choices-container", Container)
            except Exception:
                return
            choices_container.remove_children()
            self._mount_choice_buttons(
                self.engine.state.current_node,
                choices_container,
                self.engine.state.current_node.narrative.startswith(constants.ERROR_NARRATIVE_PREFIX),
            )

    def _set_variant_class(self, prefix: str, value: str, allowed: tuple[str, ...], default: str) -> None:
        resolved = value if value in allowed else default
        for option in allowed:
            self.remove_class(f"{prefix}-{option}")
        self.add_class(f"{prefix}-{resolved}")

    def _apply_reading_preference_classes(self) -> None:
        self._set_variant_class(
            "text-scale",
            self.text_scale,
            constants.TEXT_SCALE_OPTIONS,
            "standard",
        )
        self._set_variant_class(
            "line-width",
            self.line_width,
            constants.READING_WIDTH_OPTIONS,
            "standard",
        )
        self._set_variant_class(
            "line-spacing",
            self.line_spacing,
            constants.LINE_SPACING_OPTIONS,
            "standard",
        )

    def watch_text_scale(self, _value: str) -> None:
        self._apply_reading_preference_classes()

    def watch_line_width(self, _value: str) -> None:
        self._apply_reading_preference_classes()

    def watch_line_spacing(self, _value: str) -> None:
        self._apply_reading_preference_classes()

    @staticmethod
    def _notification_title(severity: SeverityLevel) -> str:
        titles = {
            "information": "Information",
            "warning": "Warning",
            "error": "Error",
        }
        return titles.get(severity, "Notice")

    def _prepare_status_message(self, message: str, severity: SeverityLevel) -> str:
        prefix = self._notification_title(severity)
        cleaned = format_status_message(message, screen_reader_mode=self.screen_reader_mode).strip()
        if cleaned and not cleaned.lower().startswith(f"{prefix.lower()}:"):
            cleaned = f"{prefix}: {cleaned}"
        return cleaned

    def _record_notification_history(self, message: str, severity: SeverityLevel) -> None:
        cleaned = self._prepare_status_message(message, severity)
        if not cleaned:
            return
        self._notification_history.append(NotificationHistoryEntry(message=message, severity=severity))
        if len(self._notification_history) > self._notification_history_limit:
            self._notification_history = self._notification_history[-self._notification_history_limit :]

    def get_notification_history_lines(self) -> list[str]:
        return [
            self._prepare_status_message(entry.message, entry.severity)
            for entry in self._notification_history
        ]

    def _dispatch_notification(
        self,
        message: str,
        *,
        title: str,
        severity: SeverityLevel,
        timeout: float | None,
        markup: bool,
        update_latest: bool,
    ) -> None:
        if not message:
            return
        if update_latest:
            self._latest_status_message = message
            try:
                self.query_one(StatusDisplay).latest_status = message
            except Exception:
                pass
        super().notify(
            message,
            title=title,
            severity=severity,
            timeout=timeout,
            markup=markup,
        )

    def notify(
        self,
        message: str,
        *,
        title: str = "",
        severity: SeverityLevel = "information",
        timeout: float | None = None,
        markup: bool = True,
    ) -> None:
        prefix = self._notification_title(severity)
        cleaned = self._prepare_status_message(message, severity)
        self._record_notification_history(message, severity)
        self._dispatch_notification(
            cleaned,
            title=title or prefix,
            severity=severity,
            timeout=timeout,
            markup=markup and not self.screen_reader_mode,
            update_latest=True,
        )

    def is_runtime_active(self) -> bool:
        """Return whether UI workers and event handlers may still touch widgets."""
        return self.is_running and not self._is_shutting_down

    def _set_compact_layout(self, width: int) -> None:
        """Enable compact mode on narrow terminals to preserve story readability."""
        is_compact = width < 140
        self.compact_layout = is_compact
        self.set_class(is_compact, "compact-layout")

    def action_repeat_latest_status(self) -> None:
        if not self._latest_status_message:
            self.notify("No status messages yet.", severity="warning", timeout=2)
            return
        self._dispatch_notification(
            self._latest_status_message,
            title="Latest Status",
            severity="information",
            timeout=6,
            markup=False,
            update_latest=False,
        )

    def on_unmount(self) -> None:
        """Cancel all background work and release resources."""
        self._is_shutting_down = True
        if self._startup_timer is not None:
            self._startup_timer.stop()
            self._startup_timer = None
        if self._post_render_warmup_timer is not None:
            self._post_render_warmup_timer.stop()
            self._post_render_warmup_timer = None
        if self._notification_timer is not None:
            self._notification_timer.stop()
            self._notification_timer = None
        self._cancel_background_workers()
        self._close_runtime_resources()
        self._unsubscribe_engine_events()

    def _subscribe_engine_events(self) -> None:
        """Register event bus subscriptions exactly once per mounted app instance."""
        if self._subscriptions_active:
            return

        self._unsubscribers.extend(
            [
                bus.subscribe(Events.ENGINE_STARTED, self._handle_engine_started),
                bus.subscribe(Events.ENGINE_RESTARTED, self._handle_engine_restarted),
                bus.subscribe(Events.ENGINE_PHASE_CHANGED, self._handle_engine_phase_changed),
                bus.subscribe(Events.CHOICE_MADE, self._handle_choice_made),
                bus.subscribe(Events.NODE_GENERATING, self._handle_node_generating),
                bus.subscribe(Events.TOKEN_STREAMED, self._handle_token_streamed),
                bus.subscribe(Events.NODE_COMPLETED, self._handle_node_completed),
                bus.subscribe(Events.STATS_UPDATED, self._handle_stats_updated),
                bus.subscribe(Events.INVENTORY_UPDATED, self._handle_inventory_updated),
                bus.subscribe(Events.WORLD_STATE_UPDATED, self._handle_world_state_updated),
                bus.subscribe(Events.STORY_TITLE_GENERATED, self._handle_title_generated),
                bus.subscribe(Events.ENDING_REACHED, self._handle_ending_reached),
                bus.subscribe(Events.ERROR_OCCURRED, self._handle_error),
                bus.subscribe(Events.STATUS_MESSAGE, self._handle_status_message),
            ]
        )
        self._subscriptions_active = True

    def _unsubscribe_engine_events(self) -> None:
        """Release all event bus subscriptions owned by the app."""
        for unsub in self._unsubscribers:
            unsub()
        self._unsubscribers.clear()
        self._subscriptions_active = False

    def _cancel_background_workers(self) -> None:
        """Cancel worker groups owned by the app before teardown."""
        self.workers.cancel_group(self, "speculation")
        self.workers.cancel_group(self, "typewriter")
        self.workers.cancel_all()

    def _close_runtime_resources(self) -> None:
        """Close external resources and clear references."""
        if self.engine:
            self.engine.shutdown()
        if self.generator:
            self.generator.close()
            self.generator = None
        self.engine = None

    def cache_story_history(self, scene_id: str | None, history: dict[str, Any]) -> None:
        """Memoize branch-history payloads for the active scene."""
        if not scene_id:
            return
        self._story_history_cache[scene_id] = copy.deepcopy(history)
        self._evict_scene_cache(self._story_history_cache)

    def get_cached_story_history(self, scene_id: str | None) -> dict[str, Any] | None:
        if not scene_id:
            return None
        history = self._story_history_cache.get(scene_id)
        return copy.deepcopy(history) if history is not None else None

    def cache_story_map(self, scene_id: str | None, tree_data: dict[str, Any]) -> None:
        """Memoize story-map payloads for the active scene."""
        if not scene_id:
            return
        self._story_map_cache[scene_id] = copy.deepcopy(tree_data)
        self._evict_scene_cache(self._story_map_cache)

    def get_cached_story_map(self, scene_id: str | None) -> dict[str, Any] | None:
        if not scene_id:
            return None
        tree_data = self._story_map_cache.get(scene_id)
        return copy.deepcopy(tree_data) if tree_data is not None else None

    def invalidate_scene_caches(self, keep_scene_id: str | None = None) -> None:
        """Drop memoized panel data except for the current scene when desired."""
        if keep_scene_id is None:
            self._story_history_cache.clear()
            self._story_map_cache.clear()
            return

        self._story_history_cache = {
            scene_id: payload
            for scene_id, payload in self._story_history_cache.items()
            if scene_id == keep_scene_id
        }
        self._story_map_cache = {
            scene_id: payload
            for scene_id, payload in self._story_map_cache.items()
            if scene_id == keep_scene_id
        }

    def mark_first_scene_rendered(self) -> None:
        """Schedule deferred warmups once the first scene is visible."""
        if self._has_rendered_first_scene or not self.is_runtime_active():
            return
        self._has_rendered_first_scene = True
        self._post_render_warmup_timer = self.set_timer(0.05, self._schedule_optional_runtime_warmup)

    def queue_notification(
        self,
        message: str,
        *,
        severity: Literal["information", "warning", "error"] = "information",
        timeout: float = 3,
        batch: bool = True,
    ) -> None:
        """Coalesce bursty notifications into a single popup."""
        if not message or not self.is_runtime_active():
            return
        if not batch:
            self.notify(message, severity=severity, timeout=timeout)
            return

        self._record_notification_history(message, severity)
        entry = BufferedNotification(message=message, severity=severity, timeout=timeout)
        if self._notification_buffer and self._notification_buffer[-1] == entry:
            return
        self._notification_buffer.append(entry)
        if self._notification_timer is None:
            self._notification_timer = self.set_timer(0.18, self._flush_buffered_notifications)

    def _evict_scene_cache(self, cache: dict[str, dict[str, Any]]) -> None:
        while len(cache) > self._scene_cache_limit:
            oldest_key = next(iter(cache))
            del cache[oldest_key]

    def _flush_buffered_notifications(self) -> None:
        self._notification_timer = None
        if not self._notification_buffer or not self.is_runtime_active():
            self._notification_buffer.clear()
            return

        buffered = self._notification_buffer
        self._notification_buffer = []
        if len(buffered) == 1:
            item = buffered[0]
            self._dispatch_notification(
                self._prepare_status_message(item.message, item.severity),
                title=self._notification_title(item.severity),
                severity=item.severity,
                timeout=item.timeout,
                markup=not self.screen_reader_mode,
                update_latest=True,
            )
            return

        severity_order = {"error": 3, "warning": 2, "information": 1}
        strongest = max(buffered, key=lambda item: severity_order.get(item.severity, 0))
        messages: list[str] = []
        for item in buffered:
            if item.message not in messages:
                messages.append(item.message)
        if len(messages) > 3:
            summary = " | ".join(messages[:3]) + f" | +{len(messages) - 3} more"
        else:
            summary = " | ".join(messages)
        self._dispatch_notification(
            self._prepare_status_message(summary, strongest.severity),
            title=self._notification_title(strongest.severity),
            severity=strongest.severity,
            timeout=max(item.timeout for item in buffered),
            markup=not self.screen_reader_mode,
            update_latest=True,
        )

    def _schedule_optional_runtime_warmup(self) -> None:
        self._post_render_warmup_timer = None
        if self.is_runtime_active():
            self.run_worker(self._warm_optional_runtime_services(), exclusive=False, group="runtime-warmup")

    async def _warm_optional_runtime_services(self) -> None:
        """Run optional startup checks only after the first scene is visible."""
        if not self.engine or self._optional_runtime_ready or not self.is_runtime_active():
            return

        self._optional_runtime_ready = True
        if self.engine.db and getattr(self.engine.db, "enabled", False):
            is_online = await self.engine.db.verify_connectivity_async()
            if not self.is_runtime_active():
                return
            if not is_online:
                self.queue_notification(
                    "Graph DB not found. Proceeding with ephemeral memory only.",
                    severity="warning",
                    timeout=5,
                )

        if (
            self.is_runtime_active()
            and is_rag_diagnostics_enabled()
            and not self.engine.rag.memory.is_online
        ):
            self.queue_notification(
                "RAG Engine unavailable. Basic memory fallback active.",
                severity="warning",
                timeout=5,
            )

        try:
            story_map_panel = self.query_one("#story-map-panel", Container)
        except NoMatches:
            return
        if not story_map_panel.has_class("panel-collapsed"):
            self.update_story_map()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle journal selection: jump to and highlight the corresponding turn."""
        if event.list_view.id != "journal-list":
            return
        if isinstance(event.item, JournalListItem):
            self._jump_to_story_turn(event.item.scene_index)

    def on_tree_node_selected(self, event: Any) -> None:
        data = event.node.data
        if not isinstance(data, dict):
            return
        narrative = str(data.get("narrative", "")).replace("\n", " ").strip()
        preview = narrative[:120] + ("…" if len(narrative) > 120 else "")
        turn = data.get("turn")
        depth = data.get("depth")
        mood = data.get("mood")
        ending = "ending" if data.get("is_ending") else "branch"
        self.notify(
            f"Turn {turn} | depth {depth} | {mood} | {ending}: {preview}",
            severity="information",
            timeout=4,
        )

    def _jump_to_story_turn(self, scene_index: int) -> None:
        """Scroll story view to a turn and flash-highlight it."""
        story_container = self.query_one("#story-container")
        turns = list(story_container.query(".story-turn"))
        if not turns:
            return

        clamped_index = max(0, min(scene_index, len(turns) - 1))
        target = turns[clamped_index]
        for turn in turns:
            turn.remove_class("turn-highlight")
        target.add_class("turn-highlight")

        self.call_after_refresh(
            lambda: story_container.scroll_to_widget(
                target,
                animate=not self.reduced_motion,
                top=True,
            )
        )
        self.set_timer(1.2, lambda: target.remove_class("turn-highlight"))

    def _reset_story_segments(self, initial_text: str) -> None:
        """Reset the structured story timeline to a single story turn."""
        self._story_segments = [{"kind": "story_turn", "text": initial_text}]

    def _append_story_segment(self, kind: str, text: str) -> None:
        """Append a structured story segment for save/restore fidelity."""
        self._story_segments.append({"kind": kind, "text": text})

    def _update_current_story_segment(self, text: str) -> None:
        """Keep the active story-turn segment synced with rendered narrative text."""
        for segment in reversed(self._story_segments):
            if segment.get("kind") == "story_turn":
                segment["text"] = text
                return
        self._append_story_segment("story_turn", text)

    def _current_story_turn_index(self) -> int:
        """Return the rendered story-turn index for the active turn widget."""
        story_turns = list(self.query_one("#story-container").query(".story-turn"))
        try:
            return max(0, story_turns.index(self._current_turn_widget))
        except ValueError:
            return max(0, len(story_turns) - 1)

    def _refresh_story_timeline_classes(self) -> None:
        """Keep the latest narrative turn and latest player action visually distinct."""
        story_container = self.query_one("#story-container")
        story_turns = list(story_container.query(".story-turn"))
        current_turn = self._current_turn_widget if self._current_turn_widget in story_turns else None
        if current_turn is None and story_turns:
            current_turn = cast(Markdown, story_turns[-1])
            self._current_turn_widget = current_turn

        for turn in story_turns:
            turn.set_class(turn is current_turn, "current-turn")
            turn.set_class(turn is not current_turn, "archived-turn")

        choice_widgets = list(story_container.query(".player-choice"))
        latest_choice = choice_widgets[-1] if choice_widgets else None
        for choice in choice_widgets:
            choice.set_class(choice is latest_choice, "latest-choice")
            choice.set_class(choice is not latest_choice, "archived-choice")

    @staticmethod
    def _requires_first_run_setup(config: UserConfig) -> bool:
        return not config.setup_completed

    def _present_first_run_setup(self) -> None:
        def on_selected(selection: str | None) -> None:
            if selection == "mock":
                applied = self._apply_first_run_selection("mock")
                if applied:
                    self._resume_startup_flow()
            elif selection == "download":
                self._present_model_download_setup()

        terminal_report = check_terminal_conditions(
            width=self.size.width,
            height=self.size.height,
            term=os.getenv("TERM"),
            is_headless=self.is_headless,
        )
        self.push_screen(
            FirstRunSetupScreen(
                general_notes=tuple(terminal_report.render_lines()),
            ),
            on_selected,
        )

    def _present_model_download_setup(self) -> None:
        recommendation = recommend_model_for_current_machine()
        report = check_local_model_preflight(
            recommendation,
            models_dir=get_models_dir(),
            width=self.size.width,
            height=self.size.height,
            term=os.getenv("TERM"),
            is_headless=self.is_headless,
        )
        self.push_screen(
            ModelDownloadScreen(
                recommendation,
                models_dir=str(get_models_dir()),
                preflight_notes=tuple(report.render_lines()),
                blocked_reason=report.blocking_reason,
            )
        )

    def _resume_startup_flow(self) -> None:
        self.show_loading()
        autosave_path = self._autosave_path()
        if autosave_path is not None and (not self.is_headless or self._allow_headless_startup_recovery):
            self._prompt_autosave_recovery(autosave_path)
            return
        self._startup_timer = self.set_timer(0.1, lambda: self.initialize_and_start(self.model_path))

    def _apply_first_run_selection(self, selection: Literal["mock"]) -> bool:
        runtime_preset = "mock-smoke"
        preset = "precise"
        startup_note = "Quick Demo mode selected during first-run setup."

        self.model_path = ""
        os.environ.pop("LLM_MODEL_PATH", None)

        os.environ["LLM_PROVIDER"] = selection
        os.environ["LLM_PRESET"] = preset
        self._runtime_diagnostics.update(
            {
                "runtime_preset": runtime_preset,
                "provider": selection,
                "model": "mock",
                "startup_note": startup_note,
            }
        )
        self._first_run_setup_pending = False
        self._user_config = update_user_config(
            provider=selection,
            model_path=None,
            preset=preset,
            runtime_preset=runtime_preset,
            setup_completed=True,
            setup_choice=selection,
        )
        try:
            self._sync_runtime_status()
        except NoMatches:
            pass
        return True

    def _apply_downloaded_model_selection(self, result: DownloadResult) -> None:
        os.environ["LLM_PROVIDER"] = "llama_cpp"
        os.environ["LLM_MODEL_PATH"] = result.path
        os.environ["LLM_PRESET"] = "balanced"
        self.model_path = result.path
        self._runtime_diagnostics.update(
            {
                "runtime_preset": "local-fast",
                "provider": "llama_cpp",
                "model": os.path.basename(result.path),
                "startup_note": "Using the local model downloaded during first-run setup.",
            }
        )
        self._first_run_setup_pending = False
        self._user_config = update_user_config(
            provider="llama_cpp",
            model_path=result.path,
            preset="balanced",
            runtime_preset="local-fast",
            setup_completed=True,
            setup_choice="download",
        )
        try:
            self._sync_runtime_status()
        except NoMatches:
            pass

    def begin_first_run_model_download(self, screen: ModelDownloadScreen) -> None:
        if self._model_download_cancel_event is not None:
            return
        self._model_download_cancel_event = threading.Event()
        self.run_worker(
            self._run_first_run_model_download(screen, self._model_download_cancel_event),
            exclusive=False,
            group="setup",
        )

    def cancel_first_run_model_download(self) -> None:
        if self._model_download_cancel_event is not None:
            self._model_download_cancel_event.set()

    def _publish_model_download_progress(
        self,
        screen: ModelDownloadScreen,
        progress: DownloadProgress,
    ) -> None:
        if self.is_runtime_active():
            screen.update_progress(progress)

    async def _run_first_run_model_download(
        self,
        screen: ModelDownloadScreen,
        cancel_event: threading.Event,
    ) -> None:
        try:
            result = await asyncio.to_thread(
                download_recommended_model,
                progress_callback=lambda progress: self.call_from_thread(
                    self._publish_model_download_progress, screen, progress
                ),
                cancel_event=cancel_event,
            )
        except ModelDownloadCancelled:
            if self.is_runtime_active():
                screen.mark_failed("Download cancelled before the model finished saving.")
            self._model_download_cancel_event = None
            return
        except ModelDownloadError as exc:
            if self.is_runtime_active():
                screen.mark_failed(str(exc))
            self._model_download_cancel_event = None
            return

        self._model_download_cancel_event = None
        if not self.is_runtime_active():
            return

        self._apply_downloaded_model_selection(result)
        screen.mark_complete(result.path)
        self.notify("Local model ready. Continuing startup.", severity="information", timeout=4)
        screen.dismiss(None)
        self._resume_startup_flow()

    @work(exclusive=True)
    async def initialize_and_start(self, model_path: str) -> None:
        """Load model and start the story engine."""
        if not self.is_runtime_active():
            return

        start_time = time.perf_counter()
        self._optional_runtime_ready = False
        self.invalidate_scene_caches()
        self.show_loading()
        await asyncio.sleep(0.2)
        if not self.is_runtime_active():
            return

        try:
            if self.generator is None:
                self.generator = await asyncio.to_thread(ModelBroker, model_path=model_path)
            if not self.is_runtime_active():
                self._close_runtime_resources()
                return
            self._sync_runtime_status()

            if self.engine is None:
                # Initialize engine with shared services
                self.engine = StoryEngine(
                    broker=self.generator,
                    starting_prompt=self.starting_prompt,
                    db=CYOAGraphDB(),
                    initial_world_state=getattr(self, "_initial_world_state", {}),
                    initial_prompt_config=getattr(self, "_initial_prompt_config", {}),
                )

            await self.engine.initialize()
            if not self.is_runtime_active():
                self._close_runtime_resources()
                return
            self._sync_runtime_status()
            self.queue_notification(self._runtime_summary(), severity="information", timeout=4, batch=False)
            startup_note = self._runtime_diagnostics.get("startup_note", "").strip()
            if startup_note:
                self.queue_notification(startup_note, severity="warning", timeout=5, batch=False)
            from cyoa.core.observability import record_startup_latency

            record_startup_latency((time.perf_counter() - start_time) * 1000, status="success")

        except Exception as e:
            from cyoa.core.observability import record_startup_latency

            record_startup_latency((time.perf_counter() - start_time) * 1000, status="failure")
            self.notify(f"Initial setup failed: {e}", severity="error", timeout=5)
            self.query_one("#loading", ThemeSpinner).add_class("hidden")
            self._close_runtime_resources()
            raise

    def _runtime_summary(self) -> str:
        profile = self._runtime_diagnostics.get("runtime_preset", "custom")
        provider = self._runtime_diagnostics.get("provider", "llama_cpp")
        model = self._runtime_diagnostics.get("model", "default")
        return f"Runtime {profile} | provider {provider} | model {model}"

    def _sync_runtime_status(self) -> None:
        display = self.query_one(StatusDisplay)
        diagnostics = self._runtime_diagnostics
        display.runtime_profile = diagnostics.get("runtime_preset", "custom")
        display.provider_label = diagnostics.get("provider", "llama_cpp")
        display.engine_phase = self.engine.phase.value if self.engine is not None else "offline"
        if self.generator is not None:
            display.generation_preset = str(self.generator.runtime_controls()["preset"])

    # ------------------------------------------------------------------
    # Speculation
    # ------------------------------------------------------------------

    @work(group="speculation", exclusive=True)
    async def speculate_all_choices(self, node: StoryNode) -> None:
        """Sequential background generation of the most likely next scenes."""
        if not self.engine or not self.engine.story_context or not self.generator:
            return
        if not self.is_runtime_active():
            return

        # Emit status message to inform user of background activity
        bus.emit(Events.STATUS_MESSAGE, message="⚡ Weaving possible futures...")

        # P6 Fix: More aggressive/intelligent delay
        # Skip long sleep if the generator is idle
        is_locked = False
        lock = getattr(self.generator, "_lock", None)
        locked = getattr(lock, "locked", None)
        if callable(locked):
            is_locked = bool(locked())

        if is_locked:
            await asyncio.sleep(2.0)
        else:
            await asyncio.sleep(0.5)
        if not self.is_runtime_active():
            return

        # Optimization: Limit speculation to only 1 "most likely" choice (the first one)
        # to prevent resource starvation on local LLMs.
        if not node.choices:
            return

        choice = node.choices[0]
        if self.engine.speculation_cache.get_node(self.engine.state.current_scene_id or "", choice.text):
            return

        # Clone context to speculate without polluting the main one
        spec_context = self.engine.story_context.clone()
        spec_context.add_turn(
            node.narrative,
            choice.text,
            self.engine.state.inventory,
            self.engine.state.player_stats
        )

        try:
            # Low-priority generation (no streaming)
            spec_node = await self.generator.generate_next_node_async(spec_context, low_priority=True)
            self.engine.speculation_cache.set_node(
                self.engine.state.current_scene_id or "",
                choice.text,
                spec_node
            )
        except Exception as e:
            logger.debug("Speculative generation failed: %s", e)

    def action_cycle_generation_preset(self) -> None:
        if not self.generator:
            return
        controls = self.generator.cycle_generation_preset()
        self.query_one(StatusDisplay).generation_preset = str(controls["preset"])
        self.notify(
            f"Generation preset: {controls['preset']} (temp {controls['temperature']}, max {controls['max_tokens']})",
            severity="information",
            timeout=3,
        )

    def action_show_settings(self) -> None:
        """Open the persisted settings modal."""
        config = self._user_config

        def on_saved(payload: dict[str, Any] | None) -> None:
            if not payload:
                return
            if "action" in payload:
                self._handle_settings_action(str(payload["action"]))
                return
            self._apply_settings(payload)

        self.push_screen(
            SettingsScreen(
                provider=config.provider,
                model_path=config.model_path,
                theme=config.theme,
                dark=config.dark,
                high_contrast=getattr(config, "high_contrast", False),
                reduced_motion=getattr(config, "reduced_motion", False),
                screen_reader_mode=getattr(config, "screen_reader_mode", False),
                text_scale=getattr(config, "text_scale", "standard"),
                line_width=getattr(config, "line_width", "standard"),
                line_spacing=getattr(config, "line_spacing", "standard"),
                typewriter=config.typewriter,
                typewriter_speed=config.typewriter_speed,
                diagnostics_enabled=config.diagnostics_enabled,
                available_themes=list_themes(),
            ),
            on_saved,
        )

    def _handle_settings_action(self, action: str) -> None:
        if action == "test_backend":
            self.run_worker(self._run_backend_connection_test(), exclusive=False, group="settings")
            return
        if action == "reveal_saves":
            self._reveal_save_folder()
            return
        if action == "reset_settings":
            self.push_screen(
                ConfirmScreen("Reset saved settings to safe defaults?"),
                self._confirm_settings_reset,
            )

    def _confirm_settings_reset(self, confirmed: object | None) -> None:
        if confirmed is True:
            self._reset_settings_to_safe_defaults()

    @staticmethod
    def _resolve_provider_setting(payload: dict[str, Any]) -> str:
        provider = str(payload.get("provider") or "mock").strip().lower()
        return provider if provider in {"mock", "llama_cpp"} else "mock"

    @staticmethod
    def _resolve_option_setting(
        payload: dict[str, Any],
        key: str,
        current_value: str,
        allowed: tuple[str, ...],
    ) -> str:
        candidate = str(payload.get(key) or current_value).strip()
        return candidate if candidate in allowed else current_value

    @staticmethod
    def _set_diagnostics_env(enabled: bool) -> None:
        if enabled:
            os.environ["CYOA_ENABLE_RAG"] = "1"
            return
        os.environ.pop("CYOA_ENABLE_RAG", None)

    def _apply_settings(self, payload: dict[str, Any]) -> None:
        """Persist settings and apply the runtime-safe subset immediately."""
        previous_config = self._user_config
        provider = self._resolve_provider_setting(payload)

        raw_model_path = payload.get("model_path")
        model_path = raw_model_path.strip() if isinstance(raw_model_path, str) and raw_model_path.strip() else None
        theme_name = str(payload.get("theme") or self._user_config.theme).strip() or self._user_config.theme
        dark = bool(payload.get("dark", self._user_config.dark))
        high_contrast = bool(payload.get("high_contrast", getattr(self._user_config, "high_contrast", False)))
        reduced_motion = bool(payload.get("reduced_motion", getattr(self._user_config, "reduced_motion", False)))
        screen_reader_mode = bool(
            payload.get("screen_reader_mode", getattr(self._user_config, "screen_reader_mode", False))
        )
        text_scale = self._resolve_option_setting(
            payload,
            "text_scale",
            getattr(self._user_config, "text_scale", "standard"),
            constants.TEXT_SCALE_OPTIONS,
        )
        line_width = self._resolve_option_setting(
            payload,
            "line_width",
            getattr(self._user_config, "line_width", "standard"),
            constants.READING_WIDTH_OPTIONS,
        )
        line_spacing = self._resolve_option_setting(
            payload,
            "line_spacing",
            getattr(self._user_config, "line_spacing", "standard"),
            constants.LINE_SPACING_OPTIONS,
        )
        typewriter = bool(payload.get("typewriter", self._user_config.typewriter))
        typewriter_speed = self._resolve_option_setting(
            payload,
            "typewriter_speed",
            self._user_config.typewriter_speed,
            tuple(constants.TYPEWRITER_SPEEDS),
        )
        diagnostics_enabled = bool(
            payload.get("diagnostics_enabled", self._user_config.diagnostics_enabled)
        )

        self._set_diagnostics_env(diagnostics_enabled)

        self.dark = dark
        self.high_contrast_mode = high_contrast
        self.reduced_motion = reduced_motion
        self.screen_reader_mode = screen_reader_mode
        self.text_scale = text_scale
        self.line_width = line_width
        self.line_spacing = line_spacing
        self.typewriter_enabled = typewriter
        self.typewriter_speed = typewriter_speed
        if self.reduced_motion or self.screen_reader_mode or not self.typewriter_enabled:
            self.action_skip_typewriter()

        self._user_config = update_user_config(
            provider=provider,
            model_path=model_path,
            theme=theme_name,
            dark=dark,
            high_contrast=high_contrast,
            reduced_motion=reduced_motion,
            screen_reader_mode=screen_reader_mode,
            text_scale=text_scale,
            line_width=line_width,
            line_spacing=line_spacing,
            typewriter=typewriter,
            typewriter_speed=typewriter_speed,
            diagnostics_enabled=diagnostics_enabled,
        )

        pending_changes: list[str] = []
        if theme_name != previous_config.theme:
            pending_changes.append("theme")
        if provider != self._runtime_diagnostics.get("provider"):
            pending_changes.append("provider")
        if provider == "llama_cpp" and model_path != previous_config.model_path:
            pending_changes.append("model path")

        message = "Settings saved."
        if pending_changes:
            message = f"Settings saved. Restart to apply: {', '.join(pending_changes)}."
        self.notify(message, severity="information", timeout=4)

    def _reset_settings_to_safe_defaults(self) -> None:
        self._user_config = reset_user_config(preserve_setup=True)
        os.environ.pop("CYOA_ENABLE_RAG", None)
        os.environ.pop("LLM_MODEL_PATH", None)

        self.dark = self._user_config.dark
        self.high_contrast_mode = getattr(self._user_config, "high_contrast", False)
        self.reduced_motion = getattr(self._user_config, "reduced_motion", False)
        self.screen_reader_mode = getattr(self._user_config, "screen_reader_mode", False)
        self.text_scale = getattr(self._user_config, "text_scale", "standard")
        self.line_width = getattr(self._user_config, "line_width", "standard")
        self.line_spacing = getattr(self._user_config, "line_spacing", "standard")
        self.typewriter_enabled = self._user_config.typewriter
        self.typewriter_speed = self._user_config.typewriter_speed
        self.notify(
            "Settings reset. Restart to return to safe demo defaults.",
            severity="information",
            timeout=4,
        )

    def _reveal_save_folder(self) -> None:
        revealed, path = reveal_in_file_manager(Path(support_paths()["saves_dir"]))
        if revealed:
            self.notify(f"Opened save folder: {path}", severity="information", timeout=4)
            return
        self.notify(
            f"Save folder: {path}",
            severity="information",
            timeout=5,
        )

    async def _run_backend_connection_test(self) -> None:
        config = self._user_config
        provider = (config.provider or "mock").strip().lower()

        if provider == "mock":
            self.notify("Quick Demo backend is ready.", severity="information", timeout=4)
            return

        if provider != "llama_cpp":
            self.notify(
                f"Backend test is unavailable for provider '{provider}'.",
                severity="warning",
                timeout=4,
            )
            return

        model_path = config.model_path
        if not model_path:
            self.notify(
                "Local Model is selected, but no GGUF path is saved.",
                severity="warning",
                timeout=5,
            )
            return

        if not os.path.exists(model_path):
            self.notify(
                f"Saved model path was not found: {model_path}",
                severity="error",
                timeout=5,
            )
            return

        provider_instance: LlamaCppProvider | None = None
        try:
            provider_instance = await asyncio.to_thread(LlamaCppProvider, model_path)
        except Exception as exc:
            self.notify(
                f"Local model check failed: {exc}",
                severity="error",
                timeout=5,
            )
            return
        finally:
            if provider_instance is not None:
                provider_instance.close()

        self.notify(
            "Local model backend passed startup checks.",
            severity="information",
            timeout=4,
        )

    def action_edit_directives(self) -> None:
        """Edit comma-separated player directives for the active run."""
        if not self.engine or not self.engine.story_context:
            return

        from cyoa.ui.components import TextPromptScreen

        current = ", ".join(self.engine.story_context.directives)

        def on_saved(value: str | None) -> None:
            if value is None or not self.engine or not self.engine.story_context:
                return
            directives = [part.strip() for part in value.split(",") if part.strip()]
            self.engine.story_context.directives = directives
            self.query_one(StatusDisplay).directives = directives
            self.notify("Updated directives.", severity="information", timeout=2)

        self.push_screen(
            TextPromptScreen(
                "[b]Edit Directives[/b]",
                value=current,
                placeholder="comma-separated directives",
            ),
            on_saved,
        )

    def on_click(self, event: Click) -> None:
        """Typewriter skip shortcut on clicking the story area."""
        try:
            if isinstance(event.control, Button) and event.control.id == "btn-new-adventure":
                self.run_worker(self.action_restart(), exclusive=True)
                return

            # U1 Fix: Only skip if clicking within the story container
            story = self.query_one("#story-container", VerticalScroll)
            current = event.control
            while current is not None:
                if current is story:
                    self.action_skip_typewriter()
                    break
                current = getattr(current, "parent", None)
        except (Exception, KeyError) as e:
            logger.debug("Click handler failed: %s", e)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-new-adventure":
            self.run_worker(self.action_restart(), exclusive=True)
            return

        if event.button.id == "btn-retry":
            self.show_loading()
            if self.engine:
                # A5 Fix: Call public engine.retry() instead of private _generate_next()
                await self.engine.retry()
            return

        button_id = event.button.id
        if button_id and button_id.startswith("choice-"):
            try:
                choice_idx = int(button_id.split("-")[-1])
                await self._trigger_choice(choice_idx, selected_button_id=button_id)
            except (ValueError, IndexError) as e:
                logger.debug("Invalid choice button ID clicked: %s (%s)", button_id, e)

    async def action_choose(self, number: str) -> None:
        """Select a choice by its 1-based index."""
        idx = int(number) - 1
        prefix = f"choice-t{self.turn_count}-"
        for btn in self.query("Button"):
            if btn.id and btn.id.startswith(prefix) and btn.id.endswith(f"-{idx}"):
                await self._trigger_choice(idx, selected_button_id=btn.id)
                return

    def _enabled_choice_buttons(self) -> list[Button]:
        """Return enabled choice buttons for the current turn in render order."""
        prefix = f"choice-t{self.turn_count}-"
        return [
            btn
            for btn in self.query("#choices-container Button")
            if isinstance(btn, Button) and btn.id and btn.id.startswith(prefix) and not btn.disabled
        ]

    def _move_choice_focus(self, step: int) -> None:
        """Move focus between available choice buttons."""
        buttons = self._enabled_choice_buttons()
        if not buttons:
            return

        focused = self.focused
        try:
            current_index = buttons.index(focused) if isinstance(focused, Button) else -1
        except ValueError:
            current_index = -1

        if current_index == -1:
            target = buttons[0] if step > 0 else buttons[-1]
        else:
            target = buttons[(current_index + step) % len(buttons)]
        target.focus()

    def action_focus_next_choice(self) -> None:
        self._move_choice_focus(1)

    def action_focus_previous_choice(self) -> None:
        self._move_choice_focus(-1)
