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
from textual.app import App, ComposeResult, ScreenStackError
from textual.containers import Container, VerticalScroll
from textual.css.query import NoMatches
from textual.events import Click, Resize
from textual.notifications import SeverityLevel
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widget import Widget
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
    FIRST_RUN_ACCESSIBILITY_PRESET_OPTIONS,
    StartupAccessibilityRecommendation,
    UserConfig,
    UserConfigSaveError,
    accessibility_preset_overrides,
    infer_accessibility_preset,
    infer_startup_accessibility_recommendation,
    infer_terminal_accessibility_fallback,
    load_user_config,
    reset_user_config,
    resolve_accessibility_preferences,
    update_user_config,
)
from cyoa.db.graph_db import CYOAGraphDB
from cyoa.db.rag_memory import is_rag_diagnostics_enabled
from cyoa.llm.broker import ModelBroker
from cyoa.llm.providers import LlamaCppProvider
from cyoa.ui.components import (
    CommandPaletteScreen,
    ConfirmScreen,
    FirstRunSetupScreen,
    GameWorkspace,
    JournalListItem,
    ModelDownloadScreen,
    SettingsScreen,
    StartupAccessibilityRecommendationScreen,
    StatusDisplay,
    ThemeSpinner,
)
from cyoa.ui.keybindings import (
    build_app_bindings,
    build_command_palette_entries,
    effective_keybindings,
    resolve_keybinding_overrides,
)
from cyoa.ui.mixins import (
    EventsMixin,
    NavigationMixin,
    PersistenceMixin,
    RenderingMixin,
    ThemeMixin,
    TypewriterMixin,
)
from cyoa.ui.presenters import (
    build_lore_codex_summary,
    build_scene_recap,
    build_world_state_summary,
    format_status_message,
    loading_story_text,
)

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


@dataclass(slots=True)
class FocusTarget:
    kind: Literal["widget_id", "choice_index"]
    value: str | int


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

    BINDINGS: ClassVar[list[Any]] = build_app_bindings()

    # Fix #4: Reactive turn counter displayed in footer
    turn_count: reactive[int] = reactive(1)
    mood: reactive[str] = reactive("default")
    typewriter_enabled: reactive[bool] = reactive(True)
    typewriter_speed: reactive[str] = reactive("normal")
    reduced_motion: reactive[bool] = reactive(False)
    high_contrast_mode: reactive[bool] = reactive(False)
    screen_reader_mode: reactive[bool] = reactive(False)
    cognitive_load_reduction_mode: reactive[bool] = reactive(False)
    text_scale: reactive[str] = reactive("standard")
    line_width: reactive[str] = reactive("standard")
    line_spacing: reactive[str] = reactive("standard")
    notification_verbosity: reactive[str] = reactive("standard")
    scene_recap_verbosity: reactive[str] = reactive("standard")
    runtime_metadata_verbosity: reactive[str] = reactive("standard")
    locked_choice_verbosity: reactive[str] = reactive("standard")
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
        startup_accessibility_overrides: dict[str, bool] | None = None,
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
        self._startup_accessibility_overrides = startup_accessibility_overrides or {}
        self._allow_headless_startup_recovery = allow_headless_startup_recovery
        self._user_config = load_user_config()
        self._terminal_accessibility_fallback = infer_terminal_accessibility_fallback(
            term=os.getenv("TERM"),
            colorterm=os.getenv("COLORTERM"),
            no_color="NO_COLOR" in os.environ,
        )
        initial_accessibility_preferences = resolve_accessibility_preferences(
            self._user_config,
            self._startup_accessibility_overrides,
        )
        initial_accessibility_preferences = self._merge_terminal_fallback_accessibility(
            initial_accessibility_preferences
        )
        self._keybinding_overrides = resolve_keybinding_overrides(
            getattr(self._user_config, "keybindings", {})
        )
        self.set_keymap(self._keybinding_overrides)
        self._first_run_setup_pending = self._requires_first_run_setup(self._user_config)
        self._startup_accessibility_recommendation_handled = False
        self._pending_accessibility_preset = getattr(
            self._user_config, "accessibility_preset", "default"
        )
        initial_story_text = loading_story_text(
            screen_reader_mode=bool(initial_accessibility_preferences["screen_reader_mode"])
        )

        self.generator: ModelBroker | None = None
        self.engine: StoryEngine | None = None
        self._current_story: str = initial_story_text
        self._current_turn_text: str = initial_story_text
        self._story_segments: list[dict[str, object]] = [
            {"kind": "story_turn", "text": initial_story_text}
        ]
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
        self._latest_status_source_message: str = "Waiting for adventure updates."
        self._latest_status_severity: SeverityLevel = "information"
        self._latest_status_message: str = self._prepare_status_message(
            self._latest_status_source_message,
            self._latest_status_severity,
        )
        self._modal_focus_return_target: FocusTarget | None = None
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
        self.high_contrast_mode = bool(initial_accessibility_preferences["high_contrast"])
        self.reduced_motion = bool(initial_accessibility_preferences["reduced_motion"])
        self.screen_reader_mode = bool(initial_accessibility_preferences["screen_reader_mode"])
        self.cognitive_load_reduction_mode = config.get("cognitive_load_reduction_mode", False)
        self.text_scale = str(config.get("text_scale", "standard"))
        self.line_width = str(config.get("line_width", "standard"))
        self.line_spacing = str(config.get("line_spacing", "standard"))
        self.notification_verbosity = str(config.get("notification_verbosity", "standard"))
        self.scene_recap_verbosity = str(config.get("scene_recap_verbosity", "standard"))
        self.runtime_metadata_verbosity = str(config.get("runtime_metadata_verbosity", "standard"))
        self.locked_choice_verbosity = str(config.get("locked_choice_verbosity", "standard"))
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
        status_display.cognitive_load_reduction_mode = self.cognitive_load_reduction_mode
        status_display.runtime_metadata_verbosity = self.runtime_metadata_verbosity
        status_display.latest_status = self._latest_status_message
        self._sync_runtime_status()
        self.apply_ui_theme()
        self.set_class(self.reduced_motion, "reduced-motion")
        self.set_class(self.screen_reader_mode, "screen-reader-mode")
        self.set_class(self.cognitive_load_reduction_mode, "cognitive-load-mode")
        self._apply_reading_preference_classes()

        self._subscribe_engine_events()
        self._typewriter_worker()
        self._continue_startup_sequence()

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
        if (
            self._current_story == other_loading_text
            and self._current_turn_text == other_loading_text
        ):
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
                self.engine.state.current_node.narrative.startswith(
                    constants.ERROR_NARRATIVE_PREFIX
                ),
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
            focus_target = self._capture_focus_target()
            choices_container.remove_children()
            mount_args = (
                self.engine.state.current_node,
                choices_container,
                self.engine.state.current_node.narrative.startswith(
                    constants.ERROR_NARRATIVE_PREFIX
                ),
            )
            if focus_target is None:
                self._mount_choice_buttons(*mount_args)
            else:
                self._mount_choice_buttons(*mount_args, focus_target=focus_target)
        self._refresh_latest_status_message()

    def watch_cognitive_load_reduction_mode(self, enabled: bool) -> None:
        self.set_class(enabled, "cognitive-load-mode")
        try:
            status_display = self.query_one(StatusDisplay)
        except Exception:
            return
        status_display.cognitive_load_reduction_mode = enabled
        self._refresh_latest_status_message()

    def _set_variant_class(
        self, prefix: str, value: str, allowed: tuple[str, ...], default: str
    ) -> None:
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

    def watch_notification_verbosity(self, _value: str) -> None:
        self._refresh_latest_status_message()

    def watch_scene_recap_verbosity(self, _value: str) -> None:
        return

    def watch_runtime_metadata_verbosity(self, value: str) -> None:
        try:
            self.query_one(StatusDisplay).runtime_metadata_verbosity = value
        except Exception:
            return

    def watch_locked_choice_verbosity(self, _value: str) -> None:
        if (
            self.engine is None
            or self.engine.state.current_node is None
            or self._loading_suffix_shown
        ):
            return
        try:
            choices_container = self.query_one("#choices-container", Container)
        except Exception:
            return
        focus_target = self._capture_focus_target()
        choices_container.remove_children()
        self._mount_choice_buttons(
            self.engine.state.current_node,
            choices_container,
            self.engine.state.current_node.narrative.startswith(constants.ERROR_NARRATIVE_PREFIX),
            focus_target=focus_target,
        )

    def _notification_prefix(self, severity: SeverityLevel) -> str:
        prefix = self._notification_title(severity)
        if self.cognitive_load_reduction_mode:
            prefix = {
                "information": "Update",
                "warning": "Attention",
                "error": "Problem",
            }.get(severity, "Update")
        return prefix

    @staticmethod
    def _notification_title(severity: SeverityLevel) -> str:
        titles = {
            "information": "Information",
            "warning": "Warning",
            "error": "Error",
        }
        return titles.get(severity, "Notice")

    def _prepare_status_message(self, message: str, severity: SeverityLevel) -> str:
        prefix = self._notification_prefix(severity)
        cleaned = format_status_message(
            message,
            screen_reader_mode=self.screen_reader_mode,
            simplified_mode=self.cognitive_load_reduction_mode,
        ).strip()
        if not cleaned:
            return cleaned
        if self.notification_verbosity == "minimal":
            return cleaned
        if self.notification_verbosity == "detailed":
            detailed_prefix = f"{prefix} update"
            if cleaned.lower().startswith(f"{detailed_prefix.lower()}:"):
                return cleaned
            return f"{detailed_prefix}: {cleaned}"
        if not cleaned.lower().startswith(f"{prefix.lower()}:"):
            cleaned = f"{prefix}: {cleaned}"
        return cleaned

    def _refresh_latest_status_message(self) -> None:
        self._latest_status_message = self._prepare_status_message(
            self._latest_status_source_message,
            self._latest_status_severity,
        )
        try:
            self.query_one(StatusDisplay).latest_status = self._latest_status_message
        except Exception:
            return

    def _record_notification_history(self, message: str, severity: SeverityLevel) -> None:
        cleaned = self._prepare_status_message(message, severity)
        if not cleaned:
            return
        self._notification_history.append(
            NotificationHistoryEntry(message=message, severity=severity)
        )
        if len(self._notification_history) > self._notification_history_limit:
            self._notification_history = self._notification_history[
                -self._notification_history_limit :
            ]

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
            self._latest_status_source_message = message
            self._latest_status_severity = severity
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
        was_compact = bool(self.compact_layout)
        self.compact_layout = is_compact
        self.set_class(is_compact, "compact-layout")
        if is_compact and not was_compact:
            self._collapse_side_panels_for_compact_layout()

    def _collapse_side_panels_for_compact_layout(self) -> None:
        """Reset side panels when entering rescue mode so story flow keeps priority."""
        try:
            journal_panel = self.query_one("#journal-panel", Container)
            story_map_panel = self.query_one("#story-map-panel", Container)
        except Exception:
            return

        journal_panel.add_class("panel-collapsed")
        story_map_panel.add_class("panel-collapsed")

        focused = self._focused_widget()
        if focused is not None and not self._widget_can_receive_focus(focused):
            self._restore_focus_target(None, fallback="choices")

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

    def action_show_action_palette(self) -> None:
        """Open a searchable command palette for discoverable action launching."""
        entries = build_command_palette_entries(self._keybinding_overrides)

        def on_selected(action: str | None) -> None:
            if not action:
                return
            self.run_worker(self.run_action(action), exclusive=False, group="palette")

        self._push_modal_screen(CommandPaletteScreen(entries), on_selected)

    def _focused_widget(self) -> Widget | None:
        try:
            focused = self.focused
        except ScreenStackError:
            return None
        return focused if isinstance(focused, Widget) else None

    def _capture_focus_target(self) -> FocusTarget | None:
        focused = self._focused_widget()
        if focused is None or not focused.is_attached:
            return None

        if isinstance(focused, Button):
            buttons = self._available_action_buttons()
            if focused in buttons:
                return FocusTarget("choice_index", buttons.index(focused))

        widget: Widget | None = focused
        while widget is not None:
            if widget.id:
                return FocusTarget("widget_id", widget.id)
            parent = widget.parent
            widget = parent if isinstance(parent, Widget) else None
        return None

    def _widget_can_receive_focus(self, widget: Widget) -> bool:
        if not widget.is_attached or not widget.visible or not widget.display:
            return False
        if bool(getattr(widget, "disabled", False)):
            return False

        current: Widget | None = widget
        while current is not None:
            if current.has_class("hidden") or current.has_class("panel-collapsed"):
                return False
            parent = current.parent
            current = parent if isinstance(parent, Widget) else None
        return True

    def _resolve_focus_target_widget(self, target: FocusTarget | None) -> Widget | None:
        if target is None:
            return None
        if target.kind == "choice_index":
            buttons = self._available_action_buttons()
            if not buttons:
                return None
            index = min(int(target.value), len(buttons) - 1)
            return buttons[index]
        if target.kind == "widget_id":
            try:
                widget = self.query_one(f"#{target.value}", Widget)
            except NoMatches:
                return None
            return widget if self._widget_can_receive_focus(widget) else None
        return None

    def _fallback_focus_widget(self, fallback: str = "choices") -> Widget | None:
        fallback_methods: dict[str, Callable[[], Widget | None]] = {
            "choices": lambda: (
                self._available_action_buttons()[0] if self._available_action_buttons() else None
            ),
            "story": lambda: self.query_one("#story-container", Widget),
            "status": lambda: self.query_one("#status-display", Widget),
            "journal": lambda: self.query_one("#journal-list", Widget),
            "story_map": lambda: self.query_one("#story-map-tree", Widget),
        }
        ordered = [fallback, "choices", "story", "status", "journal", "story_map"]
        for key in ordered:
            resolver = fallback_methods.get(key)
            if resolver is None:
                continue
            try:
                widget = resolver()
            except NoMatches:
                continue
            if widget is not None and self._widget_can_receive_focus(widget):
                return widget
        return None

    def _restore_focus_target(
        self,
        target: FocusTarget | None,
        *,
        fallback: str = "choices",
    ) -> None:
        def apply_focus() -> None:
            widget = self._resolve_focus_target_widget(target)
            if widget is None:
                widget = self._fallback_focus_widget(fallback)
            if widget is not None and self._widget_can_receive_focus(widget):
                widget.focus()

        self.call_after_refresh(apply_focus)

    def _has_open_modal_screen(self) -> bool:
        try:
            screen_stack = self.screen_stack
        except ScreenStackError:
            return False
        return any(isinstance(screen, ModalScreen) for screen in screen_stack[1:])

    def _push_modal_screen(
        self,
        screen: ModalScreen[Any],
        callback: Callable[[Any], None] | None = None,
        *,
        fallback_focus: str = "choices",
    ) -> None:
        opened_over_modal = self._has_open_modal_screen()
        modal_focus_target = self._capture_focus_target()
        if self._modal_focus_return_target is None:
            self._modal_focus_return_target = modal_focus_target

        def on_dismiss(result: Any) -> None:
            try:
                if callback is not None:
                    callback(result)
            finally:
                if self._has_open_modal_screen():
                    if opened_over_modal:
                        self._restore_focus_target(modal_focus_target, fallback=fallback_focus)
                else:
                    target = self._modal_focus_return_target
                    self._modal_focus_return_target = None
                    self._restore_focus_target(target, fallback=fallback_focus)

        self.push_screen(screen, on_dismiss)

    def get_scene_recap_text(self) -> str:
        if not self.engine or not self.engine.state.current_node:
            return (
                "## Scene\nNo current scene is available yet.\n\n"
                "## Choices\nNo choices available yet.\n\n"
                "## Objectives\n- None\n\n"
                "## Progress\n- Stats: Health 100 | Gold 0 | Reputation 0\n- Inventory: Empty\n\n"
                "## Recent Changes\n- No major changes this turn."
            )

        state = self.engine.state
        node = state.current_node
        return build_scene_recap(
            narrative=node.narrative,
            choices=node.choices,
            inventory=state.inventory,
            player_stats=state.player_stats,
            objectives=state.objectives,
            screen_reader_mode=self.screen_reader_mode,
            turn_count=state.turn_count,
            scene_recap_verbosity=self.scene_recap_verbosity,
            locked_choice_verbosity=self.locked_choice_verbosity,
            story_title=state.story_title or node.title,
            last_choice_text=state.last_choice_text,
            last_resolved_choice_check=state.last_resolved_choice_check,
            story_flags=state.story_flags,
            items_gained=node.items_gained,
            items_lost=node.items_lost,
            stat_updates=node.stat_updates,
            objectives_updated=node.objectives_updated,
            faction_updates=node.faction_updates,
            npc_affinity_updates=node.npc_affinity_updates,
            story_flags_set=node.story_flags_set,
            story_flags_cleared=node.story_flags_cleared,
        )

    def get_world_state_text(self) -> str:
        if not self.engine:
            return build_world_state_summary(
                story_title=None,
                turn_count=1,
                player_stats={},
                inventory=[],
                objectives=[],
                faction_reputation={},
                npc_affinity={},
                story_flags=[],
            )

        state = self.engine.state
        return build_world_state_summary(
            story_title=state.story_title,
            turn_count=state.turn_count,
            player_stats=state.player_stats,
            inventory=state.inventory,
            objectives=state.objectives,
            faction_reputation=state.faction_reputation,
            npc_affinity=state.npc_affinity,
            story_flags=state.story_flags,
            last_choice_text=state.last_choice_text,
            last_resolved_choice_check=state.last_resolved_choice_check,
            current_scene_id=state.current_scene_id,
        )

    def get_lore_codex_text(self) -> str:
        if not self.engine:
            return build_lore_codex_summary(
                story_title=None,
                turn_count=1,
                lore_entries=[],
            )

        state = self.engine.state
        return build_lore_codex_summary(
            story_title=state.story_title,
            turn_count=state.turn_count,
            lore_entries=state.lore_entries,
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
        self._post_render_warmup_timer = self.set_timer(
            0.05, self._schedule_optional_runtime_warmup
        )

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
            self.run_worker(
                self._warm_optional_runtime_services(), exclusive=False, group="runtime-warmup"
            )

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
        current_turn = (
            self._current_turn_widget if self._current_turn_widget in story_turns else None
        )
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

    @staticmethod
    def _resolve_accessibility_preset(value: object, *, fallback: str = "default") -> str:
        if isinstance(value, str):
            candidate = value.strip().lower().replace("-", "_").replace(" ", "_")
            if candidate in FIRST_RUN_ACCESSIBILITY_PRESET_OPTIONS:
                return candidate
        return fallback

    def _first_run_accessibility_changes(self, preset: str) -> dict[str, object]:
        resolved = self._resolve_accessibility_preset(preset)
        return {
            "accessibility_preset": resolved,
            **accessibility_preset_overrides(resolved),
        }

    def _merge_terminal_fallback_accessibility(
        self,
        preferences: dict[str, bool],
    ) -> dict[str, bool]:
        fallback = self._terminal_accessibility_fallback
        if fallback is None:
            return dict(preferences)

        merged = dict(preferences)
        for key, enabled in fallback.overrides.items():
            if enabled:
                merged[key] = True
        return merged

    def _apply_live_accessibility_settings(
        self,
        *,
        high_contrast: bool,
        reduced_motion: bool,
        screen_reader_mode: bool,
    ) -> None:
        effective = self._merge_terminal_fallback_accessibility(
            {
                "high_contrast": high_contrast,
                "reduced_motion": reduced_motion,
                "screen_reader_mode": screen_reader_mode,
            }
        )
        self.high_contrast_mode = effective["high_contrast"]
        self.reduced_motion = effective["reduced_motion"]
        self.screen_reader_mode = effective["screen_reader_mode"]
        if self.reduced_motion or self.screen_reader_mode:
            self.action_skip_typewriter()

    def _present_first_run_setup(self) -> None:
        def on_selected(selection: dict[str, str] | str | None) -> None:
            runtime_choice = ""
            accessibility_preset = self._pending_accessibility_preset
            if isinstance(selection, dict):
                runtime_choice = str(selection.get("runtime") or "").strip().lower()
                accessibility_preset = self._resolve_accessibility_preset(
                    selection.get("accessibility_preset"),
                    fallback=accessibility_preset,
                )
            elif isinstance(selection, str):
                runtime_choice = selection.strip().lower()
            self._pending_accessibility_preset = accessibility_preset

            if runtime_choice == "mock":
                applied = self._apply_first_run_selection(
                    "mock",
                    accessibility_preset=accessibility_preset,
                )
                if applied:
                    self._resume_startup_flow()
            elif runtime_choice == "download":
                self._present_model_download_setup()

        terminal_report = check_terminal_conditions(
            width=self.size.width,
            height=self.size.height,
            term=os.getenv("TERM"),
            is_headless=self.is_headless,
        )
        self._push_modal_screen(
            FirstRunSetupScreen(
                general_notes=tuple(terminal_report.render_lines()),
                selected_accessibility_preset=self._pending_accessibility_preset,
            ),
            on_selected,
        )

    def _dismissed_startup_recommendations(self) -> list[str]:
        dismissed = getattr(self._user_config, "dismissed_startup_recommendations", [])
        if not isinstance(dismissed, list):
            return []
        return [value for value in dismissed if isinstance(value, str) and value]

    def _continue_startup_sequence(self) -> None:
        if self._present_startup_accessibility_recommendation_if_needed():
            return
        if self._first_run_setup_pending:
            if self.is_headless:
                self._apply_first_run_selection("mock")
                self._resume_startup_flow()
                return
            self._present_first_run_setup()
            return
        self._resume_startup_flow()

    def _build_startup_accessibility_recommendation(
        self,
    ) -> StartupAccessibilityRecommendation | None:
        return infer_startup_accessibility_recommendation(
            config=self._user_config,
            width=self.size.width,
            height=self.size.height,
            term=os.getenv("TERM"),
            colorterm=os.getenv("COLORTERM"),
            no_color="NO_COLOR" in os.environ,
            overrides=self._startup_accessibility_overrides,
        )

    def _present_startup_accessibility_recommendation_if_needed(self) -> bool:
        if (
            self.is_headless and not self._allow_headless_startup_recovery
        ) or self._startup_accessibility_recommendation_handled:
            return False
        recommendation = self._build_startup_accessibility_recommendation()
        self._startup_accessibility_recommendation_handled = True
        if recommendation is None:
            return False
        self._push_modal_screen(
            StartupAccessibilityRecommendationScreen(recommendation),
            lambda response: self._handle_startup_accessibility_recommendation_response(
                recommendation,
                response,
            ),
        )
        return True

    def _apply_startup_accessibility_recommendation(
        self,
        recommendation: StartupAccessibilityRecommendation,
    ) -> None:
        accessibility_changes = self._first_run_accessibility_changes(
            recommendation.accessibility_preset
        )
        dismissed = [
            value
            for value in self._dismissed_startup_recommendations()
            if value != recommendation.key
        ]
        self._user_config = update_user_config(
            dismissed_startup_recommendations=dismissed,
            **accessibility_changes,
        )
        self._pending_accessibility_preset = str(accessibility_changes["accessibility_preset"])
        self._apply_live_accessibility_settings(
            high_contrast=bool(accessibility_changes["high_contrast"]),
            reduced_motion=bool(accessibility_changes["reduced_motion"]),
            screen_reader_mode=bool(accessibility_changes["screen_reader_mode"]),
        )

    def _dismiss_startup_accessibility_recommendation(
        self,
        recommendation: StartupAccessibilityRecommendation,
    ) -> None:
        dismissed = self._dismissed_startup_recommendations()
        if recommendation.key not in dismissed:
            dismissed.append(recommendation.key)
        self._user_config = update_user_config(dismissed_startup_recommendations=dismissed)

    def _handle_startup_accessibility_recommendation_response(
        self,
        recommendation: StartupAccessibilityRecommendation,
        response: str | None,
    ) -> None:
        action = (response or "later").strip().lower()
        if action == "accept":
            self._apply_startup_accessibility_recommendation(recommendation)
        elif action == "dismiss":
            self._dismiss_startup_accessibility_recommendation(recommendation)
        self._continue_startup_sequence()

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
        self._push_modal_screen(
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
        if autosave_path is not None and (
            not self.is_headless or self._allow_headless_startup_recovery
        ):
            self._prompt_autosave_recovery(autosave_path)
            return
        self._startup_timer = self.set_timer(
            0.1, lambda: self.initialize_and_start(self.model_path)
        )

    def _apply_first_run_selection(
        self,
        selection: Literal["mock"],
        *,
        accessibility_preset: str = "default",
    ) -> bool:
        runtime_preset = "mock-smoke"
        preset = "precise"
        startup_note = "Quick Demo mode selected during first-run setup."
        accessibility_changes = self._first_run_accessibility_changes(accessibility_preset)

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
            **accessibility_changes,
        )
        self._pending_accessibility_preset = str(accessibility_changes["accessibility_preset"])
        self._apply_live_accessibility_settings(
            high_contrast=bool(accessibility_changes["high_contrast"]),
            reduced_motion=bool(accessibility_changes["reduced_motion"]),
            screen_reader_mode=bool(accessibility_changes["screen_reader_mode"]),
        )
        try:
            self._sync_runtime_status()
        except NoMatches:
            pass
        return True

    def _apply_downloaded_model_selection(self, result: DownloadResult) -> None:
        accessibility_changes = self._first_run_accessibility_changes(
            self._pending_accessibility_preset
        )
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
            **accessibility_changes,
        )
        self._pending_accessibility_preset = str(accessibility_changes["accessibility_preset"])
        self._apply_live_accessibility_settings(
            high_contrast=bool(accessibility_changes["high_contrast"]),
            reduced_motion=bool(accessibility_changes["reduced_motion"]),
            screen_reader_mode=bool(accessibility_changes["screen_reader_mode"]),
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
            self.queue_notification(
                self._runtime_summary(), severity="information", timeout=4, batch=False
            )
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
        if choice.check is not None:
            return
        if self.engine.speculation_cache.get_node(
            self.engine.state.current_scene_id or "", choice.text
        ):
            return

        # Clone context to speculate without polluting the main one
        spec_context = self.engine.story_context.clone()
        spec_context.add_turn(
            node.narrative, choice.text, self.engine.state.inventory, self.engine.state.player_stats
        )

        try:
            # Low-priority generation (no streaming)
            spec_node = await self.generator.generate_next_node_async(
                spec_context, low_priority=True
            )
            self.engine.speculation_cache.set_node(
                self.engine.state.current_scene_id or "", choice.text, spec_node
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
        self._show_settings_screen()

    def _show_settings_screen(
        self,
        draft_settings: dict[str, Any] | None = None,
        *,
        feedback_message: str = "",
    ) -> None:
        config = self._user_config
        draft = draft_settings or {}

        def pick(key: str, fallback: Any) -> Any:
            return draft.get(key, fallback)

        def on_saved(payload: dict[str, Any] | None) -> None:
            if not payload:
                return
            if "action" in payload:
                self._handle_settings_action(str(payload["action"]), payload)
                return
            try:
                self._apply_settings(payload)
            except UserConfigSaveError as exc:
                self._show_settings_screen(payload, feedback_message=str(exc))

        self._push_modal_screen(
            SettingsScreen(
                provider=pick("provider", config.provider),
                model_path=pick("model_path", config.model_path),
                theme=pick("theme", config.theme),
                dark=bool(pick("dark", self.dark)),
                high_contrast=bool(pick("high_contrast", self.high_contrast_mode)),
                reduced_motion=bool(pick("reduced_motion", self.reduced_motion)),
                screen_reader_mode=bool(pick("screen_reader_mode", self.screen_reader_mode)),
                cognitive_load_reduction_mode=bool(
                    pick(
                        "cognitive_load_reduction_mode",
                        self.cognitive_load_reduction_mode,
                    )
                ),
                text_scale=str(pick("text_scale", self.text_scale)),
                line_width=str(pick("line_width", self.line_width)),
                line_spacing=str(pick("line_spacing", self.line_spacing)),
                notification_verbosity=str(
                    pick("notification_verbosity", self.notification_verbosity)
                ),
                scene_recap_verbosity=str(
                    pick("scene_recap_verbosity", self.scene_recap_verbosity)
                ),
                runtime_metadata_verbosity=str(
                    pick("runtime_metadata_verbosity", self.runtime_metadata_verbosity)
                ),
                locked_choice_verbosity=str(
                    pick("locked_choice_verbosity", self.locked_choice_verbosity)
                ),
                keybindings=cast(
                    dict[str, str], pick("keybindings", getattr(config, "keybindings", {}))
                ),
                typewriter=bool(pick("typewriter", self.typewriter_enabled)),
                typewriter_speed=str(pick("typewriter_speed", self.typewriter_speed)),
                diagnostics_enabled=bool(pick("diagnostics_enabled", config.diagnostics_enabled)),
                available_themes=list_themes(),
                terminal_accessibility_fallback=self._terminal_accessibility_fallback,
                initial_feedback=feedback_message,
            ),
            on_saved,
        )

    def _handle_settings_action(
        self,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if action == "test_backend":
            draft_settings = payload.get("draft_settings") if payload else None
            self.run_worker(
                self._run_backend_connection_test(cast(dict[str, Any] | None, draft_settings)),
                exclusive=False,
                group="settings",
            )
            return
        if action == "reveal_saves":
            self._reveal_save_folder()
            return
        if action == "capture_accessibility_snapshot":
            path = self.export_accessibility_diagnostics_snapshot()
            self.notify(
                f"Accessibility diagnostics saved: {path}",
                severity="information",
                timeout=5,
            )
            return
        if action == "reset_settings":
            self._push_modal_screen(
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
        keybinding_overrides = resolve_keybinding_overrides(
            payload.get("keybindings", getattr(self._user_config, "keybindings", {}))
        )

        raw_model_path = payload.get("model_path")
        model_path = (
            raw_model_path.strip()
            if isinstance(raw_model_path, str) and raw_model_path.strip()
            else None
        )
        theme_name = (
            str(payload.get("theme") or self._user_config.theme).strip() or self._user_config.theme
        )
        dark = bool(payload.get("dark", self._user_config.dark))
        high_contrast = bool(
            payload.get("high_contrast", getattr(self._user_config, "high_contrast", False))
        )
        reduced_motion = bool(
            payload.get("reduced_motion", getattr(self._user_config, "reduced_motion", False))
        )
        screen_reader_mode = bool(
            payload.get(
                "screen_reader_mode", getattr(self._user_config, "screen_reader_mode", False)
            )
        )
        cognitive_load_reduction_mode = bool(
            payload.get(
                "cognitive_load_reduction_mode",
                getattr(self._user_config, "cognitive_load_reduction_mode", False),
            )
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
        notification_verbosity = self._resolve_option_setting(
            payload,
            "notification_verbosity",
            getattr(self._user_config, "notification_verbosity", "standard"),
            constants.VERBOSITY_OPTIONS,
        )
        scene_recap_verbosity = self._resolve_option_setting(
            payload,
            "scene_recap_verbosity",
            getattr(self._user_config, "scene_recap_verbosity", "standard"),
            constants.VERBOSITY_OPTIONS,
        )
        runtime_metadata_verbosity = self._resolve_option_setting(
            payload,
            "runtime_metadata_verbosity",
            getattr(self._user_config, "runtime_metadata_verbosity", "standard"),
            constants.VERBOSITY_OPTIONS,
        )
        locked_choice_verbosity = self._resolve_option_setting(
            payload,
            "locked_choice_verbosity",
            getattr(self._user_config, "locked_choice_verbosity", "standard"),
            constants.VERBOSITY_OPTIONS,
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
        accessibility_preset = infer_accessibility_preset(
            high_contrast=high_contrast,
            reduced_motion=reduced_motion,
            screen_reader_mode=screen_reader_mode,
        )

        self._user_config = update_user_config(
            raise_on_error=True,
            provider=provider,
            model_path=model_path,
            theme=theme_name,
            dark=dark,
            high_contrast=high_contrast,
            reduced_motion=reduced_motion,
            screen_reader_mode=screen_reader_mode,
            cognitive_load_reduction_mode=cognitive_load_reduction_mode,
            text_scale=text_scale,
            line_width=line_width,
            line_spacing=line_spacing,
            notification_verbosity=notification_verbosity,
            scene_recap_verbosity=scene_recap_verbosity,
            runtime_metadata_verbosity=runtime_metadata_verbosity,
            locked_choice_verbosity=locked_choice_verbosity,
            keybindings=keybinding_overrides,
            typewriter=typewriter,
            typewriter_speed=typewriter_speed,
            diagnostics_enabled=diagnostics_enabled,
            accessibility_preset=accessibility_preset,
        )

        self._set_diagnostics_env(diagnostics_enabled)

        self.dark = dark
        self._pending_accessibility_preset = accessibility_preset
        self._apply_live_accessibility_settings(
            high_contrast=high_contrast,
            reduced_motion=reduced_motion,
            screen_reader_mode=screen_reader_mode,
        )
        self.cognitive_load_reduction_mode = cognitive_load_reduction_mode
        self.text_scale = text_scale
        self.line_width = line_width
        self.line_spacing = line_spacing
        self.notification_verbosity = notification_verbosity
        self.scene_recap_verbosity = scene_recap_verbosity
        self.runtime_metadata_verbosity = runtime_metadata_verbosity
        self.locked_choice_verbosity = locked_choice_verbosity
        self.typewriter_enabled = typewriter
        self.typewriter_speed = typewriter_speed
        self._keybinding_overrides = keybinding_overrides
        self.set_keymap(self._keybinding_overrides)
        if not self.typewriter_enabled and not (self.reduced_motion or self.screen_reader_mode):
            self.action_skip_typewriter()

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
        self._keybinding_overrides = resolve_keybinding_overrides(
            getattr(self._user_config, "keybindings", {})
        )
        self.set_keymap(self._keybinding_overrides)
        os.environ.pop("CYOA_ENABLE_RAG", None)
        os.environ.pop("LLM_MODEL_PATH", None)

        self.dark = self._user_config.dark
        self._pending_accessibility_preset = getattr(
            self._user_config, "accessibility_preset", "default"
        )
        self.high_contrast_mode = getattr(self._user_config, "high_contrast", False)
        self.reduced_motion = getattr(self._user_config, "reduced_motion", False)
        self.screen_reader_mode = getattr(self._user_config, "screen_reader_mode", False)
        self.cognitive_load_reduction_mode = getattr(
            self._user_config, "cognitive_load_reduction_mode", False
        )
        self.text_scale = getattr(self._user_config, "text_scale", "standard")
        self.line_width = getattr(self._user_config, "line_width", "standard")
        self.line_spacing = getattr(self._user_config, "line_spacing", "standard")
        self.notification_verbosity = getattr(
            self._user_config, "notification_verbosity", "standard"
        )
        self.scene_recap_verbosity = getattr(self._user_config, "scene_recap_verbosity", "standard")
        self.runtime_metadata_verbosity = getattr(
            self._user_config,
            "runtime_metadata_verbosity",
            "standard",
        )
        self.locked_choice_verbosity = getattr(
            self._user_config, "locked_choice_verbosity", "standard"
        )
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

    @staticmethod
    def _region_snapshot(widget: Widget | None) -> dict[str, int] | None:
        if widget is None:
            return None
        region = getattr(widget, "region", None)
        if region is None:
            return None
        return {
            "x": int(region.x),
            "y": int(region.y),
            "width": int(region.width),
            "height": int(region.height),
        }

    def _widget_snapshot(self, widget: Widget | None) -> dict[str, Any]:
        if widget is None:
            return {
                "id": None,
                "type": None,
                "classes": [],
                "disabled": False,
                "can_focus": False,
                "visible": False,
                "region": None,
            }
        return {
            "id": widget.id,
            "type": type(widget).__name__,
            "classes": sorted(str(name) for name in widget.classes),
            "disabled": bool(getattr(widget, "disabled", False)),
            "can_focus": bool(getattr(widget, "can_focus", False)),
            "visible": bool(widget.visible and widget.display),
            "region": self._region_snapshot(widget),
        }

    def collect_accessibility_diagnostics_snapshot(
        self,
        *,
        include_story_content: bool = False,
    ) -> dict[str, Any]:
        from cyoa.core.observability import build_accessibility_diagnostics_snapshot

        try:
            journal_panel = self.query_one("#journal-panel", Container)
            story_map_panel = self.query_one("#story-map-panel", Container)
            story_container = self.query_one("#story-container", VerticalScroll)
            status_display = self.query_one("#status-display", Widget)
        except Exception:
            journal_panel = None
            story_map_panel = None
            story_container = None
            status_display = None

        focused = self._focused_widget()
        current_node = self.engine.state.current_node if self.engine is not None else None
        story_title = None
        if self.engine is not None:
            story_title = self.engine.state.story_title or (
                current_node.title if current_node is not None else None
            )

        settings = {
            "high_contrast": self.high_contrast_mode,
            "reduced_motion": self.reduced_motion,
            "screen_reader_mode": self.screen_reader_mode,
            "cognitive_load_reduction_mode": self.cognitive_load_reduction_mode,
            "text_scale": self.text_scale,
            "line_width": self.line_width,
            "line_spacing": self.line_spacing,
            "notification_verbosity": self.notification_verbosity,
            "scene_recap_verbosity": self.scene_recap_verbosity,
            "runtime_metadata_verbosity": self.runtime_metadata_verbosity,
            "locked_choice_verbosity": self.locked_choice_verbosity,
            "typewriter_enabled": self.typewriter_enabled,
            "typewriter_speed": self.typewriter_speed,
            "diagnostics_enabled": bool(getattr(self._user_config, "diagnostics_enabled", False)),
            "accessibility_preset": getattr(self._user_config, "accessibility_preset", "default"),
        }
        environment = {
            "term": os.getenv("TERM"),
            "colorterm": os.getenv("COLORTERM"),
            "no_color": "NO_COLOR" in os.environ,
            "headless": self.is_headless,
            "terminal_size": {"width": int(self.size.width), "height": int(self.size.height)},
            "runtime_profile": self._runtime_diagnostics.get("runtime_preset", "custom"),
            "provider": self._runtime_diagnostics.get("provider", "unknown"),
            "model": self._runtime_diagnostics.get("model", "unknown"),
        }
        layout = {
            "screen": type(self.screen).__name__,
            "compact_layout": self.compact_layout,
            "modal_open": self._has_open_modal_screen(),
            "journal_panel_collapsed": (
                journal_panel.has_class("panel-collapsed") if journal_panel is not None else True
            ),
            "story_map_panel_collapsed": (
                story_map_panel.has_class("panel-collapsed")
                if story_map_panel is not None
                else True
            ),
            "status_display": self._widget_snapshot(status_display),
            "story_container": self._widget_snapshot(story_container),
            "choice_count": len(self._available_action_buttons()),
            "notification_history_count": len(self._notification_history),
        }
        focus = {
            "focused_widget": self._widget_snapshot(focused),
            "story_scroll_y": (
                float(getattr(story_container, "scroll_y", 0.0))
                if story_container is not None
                else 0.0
            ),
            "story_max_scroll_y": (
                float(getattr(story_container, "max_scroll_y", 0.0))
                if story_container is not None
                else 0.0
            ),
        }
        story = {
            "story_title": story_title,
            "current_story_text": self._current_story,
            "current_turn_text": self._current_turn_text,
            "story_segments": [
                {
                    "kind": str(segment.get("kind") or "unknown"),
                    "text": str(segment.get("text") or ""),
                }
                for segment in self._story_segments
                if isinstance(segment, dict)
            ],
        }

        return build_accessibility_diagnostics_snapshot(
            settings=settings,
            environment=environment,
            layout=layout,
            bindings=effective_keybindings(self._keybinding_overrides),
            focus=focus,
            story=story,
            include_story_content=include_story_content,
        )

    def export_accessibility_diagnostics_snapshot(
        self,
        *,
        path: str | Path | None = None,
        include_story_content: bool = False,
    ) -> Path:
        from datetime import UTC, datetime

        from cyoa.core.observability import write_accessibility_diagnostics_snapshot

        target = (
            Path(path)
            if path is not None
            else (
                support_paths()["state_dir"]
                / "diagnostics"
                / f"accessibility_snapshot_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
            )
        )
        snapshot = self.collect_accessibility_diagnostics_snapshot(
            include_story_content=include_story_content
        )
        return write_accessibility_diagnostics_snapshot(snapshot, path=target)

    async def _run_backend_connection_test(
        self,
        draft_settings: dict[str, Any] | None = None,
    ) -> None:
        config = self._user_config
        draft = draft_settings or {}
        provider = (
            self._resolve_provider_setting(draft_settings)
            if draft_settings is not None
            else (config.provider or "mock").strip().lower()
        )

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

        raw_model_path = draft.get("model_path", config.model_path)
        model_path = (
            raw_model_path.strip()
            if isinstance(raw_model_path, str) and raw_model_path.strip()
            else None
        )
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

        self._push_modal_screen(
            TextPromptScreen(
                "[b]Edit Directives[/b]",
                value=current,
                placeholder="comma-separated directives",
            ),
            on_saved,
        )

    def get_effective_keybindings(self) -> dict[str, str]:
        return effective_keybindings(self._keybinding_overrides)

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
        if event.button.id == "btn-compact-journal":
            self.action_toggle_journal()
            return

        if event.button.id == "btn-compact-map":
            self.action_toggle_story_map()
            return

        if event.button.id == "btn-compact-messages":
            self.action_show_notification_history()
            return

        if event.button.id == "btn-compact-recap":
            self.action_show_scene_recap()
            return

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

    def _available_action_buttons(self) -> list[Button]:
        """Return enabled action buttons from the choices dock in render order."""
        return [
            btn
            for btn in self.query("#choices-container Button")
            if isinstance(btn, Button) and not btn.disabled
        ]

    def _move_choice_focus(self, step: int) -> None:
        """Move focus between available choice buttons."""
        buttons = self._available_action_buttons()
        if not buttons:
            return

        focused = self._focused_widget()
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
