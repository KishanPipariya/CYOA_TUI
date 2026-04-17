import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.css.query import NoMatches
from textual.events import Click, Resize
from textual.reactive import reactive
from textual.widgets import (
    Button,
    Footer,
    Header,
    Label,
    ListView,
    Markdown,
    Static,
    Tree,
)

from cyoa.core import constants, utils
from cyoa.core.engine import StoryEngine
from cyoa.core.events import Events, bus
from cyoa.core.models import StoryNode
from cyoa.db.graph_db import CYOAGraphDB
from cyoa.llm.broker import ModelBroker
from cyoa.ui.components import JournalListItem, StatusDisplay, ThemeSpinner
from cyoa.ui.mixins import (
    EventsMixin,
    NavigationMixin,
    PersistenceMixin,
    RenderingMixin,
    ThemeMixin,
    TypewriterMixin,
)

logger = logging.getLogger(__name__)

__all__ = ["CYOAApp"]


@dataclass(slots=True)
class BufferedNotification:
    message: str
    severity: Literal["information", "warning", "error"]
    timeout: float


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
        Binding("u", "undo", "Undo", show=True),
        Binding("s", "save_game", "Save", show=True),
        Binding("l", "load_game", "Load", show=True),
        Binding("q", "request_quit", "Quit", show=True),
        Binding("r", "request_restart", "Restart", show=True),
        Binding("t", "toggle_typewriter", "Typewriter", show=True),
        Binding("v", "cycle_typewriter_speed", "Speed", show=True),
        Binding("g", "cycle_generation_preset", "Preset", show=True),
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
    compact_layout: reactive[bool] = reactive(False)

    def __init__(
        self,
        model_path: str,
        starting_prompt: str = constants.DEFAULT_STARTING_PROMPT,
        spinner_frames: list[str] | None = None,
        accent_color: str | None = None,
        initial_world_state: dict[str, object] | None = None,
        initial_prompt_config: dict[str, object] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.model_path = model_path
        self.starting_prompt = starting_prompt
        self.spinner_frames = spinner_frames or ["[-]", "[\\]", "[|]", "[/]"]
        self._accent_color = accent_color
        self._initial_world_state = initial_world_state or {}
        self._initial_prompt_config = initial_prompt_config or {}

        self.generator: ModelBroker | None = None
        self.engine: StoryEngine | None = None
        self._current_story: str = constants.LOADING_ART
        self._current_turn_text: str = constants.LOADING_ART
        self._story_segments: list[dict[str, object]] = [{"kind": "story_turn", "text": constants.LOADING_ART}]
        self._loading_suffix_shown: bool = False
        self._unsubscribers: list[Callable[[], None]] = []
        self._subscriptions_active: bool = False
        self._is_shutting_down: bool = False
        self._startup_timer: Any | None = None
        self._post_render_warmup_timer: Any | None = None
        self._has_rendered_first_scene: bool = False
        self._optional_runtime_ready: bool = False
        self._story_history_cache: dict[str, dict[str, Any]] = {}
        self._story_map_cache: dict[str, dict[str, Any]] = {}
        self._scene_cache_limit: int = 8
        self._notification_buffer: list[BufferedNotification] = []
        self._notification_timer: Any | None = None

        # Typewriter Narrator state
        self._typewriter_queue: asyncio.Queue[str] = asyncio.Queue()
        self._typewriter_active_chunk: list[str] = []
        self._is_typing: bool = False
        self._last_stats_snapshot: dict[str, int] | None = None

        # Restore preferences
        config = utils.load_config()
        self.dark = config.get("dark", True)
        self.typewriter_enabled = config.get("typewriter", True)
        self.typewriter_speed = config.get("typewriter_speed", "normal")

        # Apply theme accent color if specified
        if self._accent_color:
            self._apply_custom_accent(self._accent_color)

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="workspace"):
            with Container(id="main-container"):
                with VerticalScroll(id="story-container"):
                    yield Markdown(constants.LOADING_ART, classes="story-turn", id="initial-turn")
                    yield Static("", id="scene-art")
                # Dedicated status bar between story and choices
                with Container(id="status-bar"):
                    yield ThemeSpinner(frames=self.spinner_frames, id="loading")
                    yield StatusDisplay(id="status-display")
                with Container(id="choices-container"):
                    pass
            with Container(id="journal-panel", classes="panel-collapsed"):
                yield Label("In-Game Journal", id="journal-title")
                yield ListView(id="journal-list")
            with Container(id="story-map-panel", classes="panel-collapsed"):
                yield Label("Story Map", id="story-map-title")
                yield Tree("Story", id="story-map-tree")
        yield Footer()

    def watch_turn_count(self, count: int) -> None:
        self.sub_title = f"Turn {count}" if count > 0 else ""

    async def on_mount(self) -> None:
        self._is_shutting_down = False
        self._has_rendered_first_scene = False
        self._optional_runtime_ready = False
        self.query_one("#choices-container", Container).border_title = "Choices"
        self.query_one("#story-container", VerticalScroll).border_title = "Story"

        self._current_turn_widget = self.query_one("#initial-turn", Markdown)
        self._set_compact_layout(self.size.width)
        self.query_one(StatusDisplay).generation_preset = "balanced"

        self._subscribe_engine_events()

        # Start loading indicator immediately
        self.show_loading()
        # Start the typewriter narrator worker
        self._typewriter_worker()
        # Short delay to let the UI paint the initial scene before starting the engine
        self._startup_timer = self.set_timer(0.1, lambda: self.initialize_and_start(self.model_path))

    def on_resize(self, event: Resize) -> None:
        self._set_compact_layout(event.size.width)

    def is_runtime_active(self) -> bool:
        """Return whether UI workers and event handlers may still touch widgets."""
        return not self._is_shutting_down

    def _set_compact_layout(self, width: int) -> None:
        """Enable compact mode on narrow terminals to preserve story readability."""
        is_compact = width < 140
        self.compact_layout = is_compact
        self.set_class(is_compact, "compact-layout")

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
        self._story_history_cache[scene_id] = history
        self._evict_scene_cache(self._story_history_cache)

    def get_cached_story_history(self, scene_id: str | None) -> dict[str, Any] | None:
        if not scene_id:
            return None
        return self._story_history_cache.get(scene_id)

    def cache_story_map(self, scene_id: str | None, tree_data: dict[str, Any]) -> None:
        """Memoize story-map payloads for the active scene."""
        if not scene_id:
            return
        self._story_map_cache[scene_id] = tree_data
        self._evict_scene_cache(self._story_map_cache)

    def get_cached_story_map(self, scene_id: str | None) -> dict[str, Any] | None:
        if not scene_id:
            return None
        return self._story_map_cache.get(scene_id)

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
            self.notify(item.message, severity=item.severity, timeout=item.timeout)
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
        self.notify(summary, severity=strongest.severity, timeout=max(item.timeout for item in buffered))

    def _schedule_optional_runtime_warmup(self) -> None:
        self._post_render_warmup_timer = None
        if self.is_runtime_active():
            self.run_worker(self._warm_optional_runtime_services(), exclusive=False, group="runtime-warmup")

    async def _warm_optional_runtime_services(self) -> None:
        """Run optional startup checks only after the first scene is visible."""
        if not self.engine or self._optional_runtime_ready or not self.is_runtime_active():
            return

        self._optional_runtime_ready = True
        if self.engine.db:
            is_online = await self.engine.db.verify_connectivity_async()
            if not self.is_runtime_active():
                return
            if not is_online:
                self.queue_notification(
                    "Graph DB not found. Proceeding with ephemeral memory only.",
                    severity="warning",
                    timeout=5,
                )

        if self.is_runtime_active() and not self.engine.rag.memory.is_online:
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
            lambda: story_container.scroll_to_widget(target, animate=True, top=True)
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
            self.query_one(StatusDisplay).generation_preset = str(
                self.generator.runtime_controls()["preset"]
            )

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
            from cyoa.core.observability import record_startup_latency

            record_startup_latency((time.perf_counter() - start_time) * 1000, status="success")

        except Exception as e:
            from cyoa.core.observability import record_startup_latency

            record_startup_latency((time.perf_counter() - start_time) * 1000, status="failure")
            self.notify(f"Initial setup failed: {e}", severity="error", timeout=5)
            self.query_one("#loading", ThemeSpinner).add_class("hidden")
            self._close_runtime_resources()
            raise

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

    def on_click(self, event: Click) -> None:
        """Typewriter skip shortcut on clicking the story area."""
        try:
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
            self.action_restart()
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
