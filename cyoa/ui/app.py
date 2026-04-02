import asyncio
import logging
from collections.abc import Callable
from typing import Any, ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.events import Click
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
from cyoa.ui.components import StatusDisplay, ThemeSpinner
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

    def __init__(
        self,
        model_path: str,
        starting_prompt: str = constants.DEFAULT_STARTING_PROMPT,
        spinner_frames: list[str] | None = None,
        accent_color: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.model_path = model_path
        self.starting_prompt = starting_prompt
        self.spinner_frames = spinner_frames or ["[-]", "[\\]", "[|]", "[/]"]
        self._accent_color = accent_color

        self.generator: ModelBroker | None = None
        self.engine: StoryEngine | None = None
        self._current_story: str = constants.LOADING_ART
        self._current_turn_text: str = constants.LOADING_ART
        self._loading_suffix_shown: bool = False
        self._unsubscribers: list[Callable[[], None]] = []

        # Typewriter Narrator state
        self._typewriter_queue: asyncio.Queue[str] = asyncio.Queue()
        self._typewriter_active_chunk: list[str] = []
        self._is_typing: bool = False

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
        with Horizontal():
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
        self.query_one("#choices-container").border_title = "Choices"
        self.query_one("#story-container").border_title = "Story"

        self._current_turn_widget = self.query_one("#initial-turn", Markdown)

        # Subscribe to Engine Events
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
                bus.subscribe(Events.STORY_TITLE_GENERATED, self._handle_title_generated),
                bus.subscribe(Events.ENDING_REACHED, self._handle_ending_reached),
                bus.subscribe(Events.ERROR_OCCURRED, self._handle_error),
                bus.subscribe(Events.STATUS_MESSAGE, self._handle_status_message),
            ]
        )

        # Start loading indicator immediately
        self.show_loading()
        # Start the typewriter narrator worker
        self._typewriter_worker()
        # Short delay to let the UI paint the initial scene before starting the engine
        self.set_timer(0.1, lambda: self.initialize_and_start(self.model_path))

    def on_unmount(self) -> None:
        """Cancel all background work and release resources."""
        self.workers.cancel_all()
        if self.generator:
            self.generator.close()
        if self.engine and self.engine.db:
            self.engine.db.close()

        # Clean up EventBus subscriptions
        for unsub in self._unsubscribers:
            unsub()
        self._unsubscribers.clear()

    @work(exclusive=True)
    async def initialize_and_start(self, model_path: str) -> None:
        """Load model and start the story engine."""
        self.show_loading()
        await asyncio.sleep(0.2)

        try:
            if self.generator is None:
                self.generator = await asyncio.to_thread(ModelBroker, model_path=model_path)

            if self.engine is None:
                # Initialize engine with shared services
                self.engine = StoryEngine(
                    broker=self.generator,
                    starting_prompt=self.starting_prompt,
                    db=CYOAGraphDB(),
                )

            # 2. Check for Graceful Degradation (Graph / RAG)
            if self.engine.db:
                # A. Verify Graph DB connectivity asynchronously (non-blocking)
                is_online = await self.engine.db.verify_connectivity_async()
                if not is_online:
                    self.notify(
                        "Graph DB not found. Proceeding with ephemeral memory only.",
                        severity="warning",
                        timeout=5
                    )

            # Check Chroma availability without forcing model download if it's lazy
            if not self.engine.memory.is_online:
                 self.notify(
                    "RAG Engine unavailable. Basic memory fallback active.",
                    severity="warning",
                    timeout=5
                )

            await self.engine.initialize()

        except Exception as e:
            self.notify(f"Initial setup failed: {e}", severity="error", timeout=5)
            self.query_one("#loading").add_class("hidden")
            raise

    # ------------------------------------------------------------------
    # Speculation
    # ------------------------------------------------------------------

    @work(group="speculation", exclusive=True)
    async def speculate_all_choices(self, node: StoryNode) -> None:
        """Sequential background generation of the most likely next scenes."""
        if not self.engine or not self.engine.story_context or not self.generator:
            return

        # Emit status message to inform user of background activity
        bus.emit(Events.STATUS_MESSAGE, message="⚡ Weaving possible futures...")

        # P6 Fix: More aggressive/intelligent delay
        # Skip long sleep if the generator is idle
        is_locked = False
        lock = getattr(self.generator, "_lock", None)
        if hasattr(lock, "locked"):
            is_locked = lock.locked()

        if is_locked:
            await asyncio.sleep(2.0)
        else:
            await asyncio.sleep(0.5)

        # Optimization: Limit speculation to only 1 "most likely" choice (the first one)
        # to prevent resource starvation on local LLMs.
        if not node.choices:
            return

        choice = node.choices[0]
        if self.engine.speculation_cache.get_node(self.engine.current_scene_id or "", choice.text):
            return

        # Clone context to speculate without polluting the main one
        spec_context = self.engine.story_context.clone()
        spec_context.add_turn(
            node.narrative,
            choice.text,
            self.engine.inventory,
            self.engine.player_stats
        )

        try:
            # Low-priority generation (no streaming)
            spec_node = await self.generator.generate_next_node_async(spec_context, low_priority=True)
            self.engine.speculation_cache.set_node(
                self.engine.current_scene_id or "",
                choice.text,
                spec_node
            )
        except Exception as e:
            logger.debug("Speculative generation failed: %s", e)

    def on_click(self, event: Click) -> None:
        """Typewriter skip shortcut on clicking the story area."""
        try:
            # U1 Fix: Only skip if clicking within the story container
            story = self.query_one("#story-container")
            if story.is_ancestor_of(event.control):
                self.action_skip_typewriter()
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
                self.engine.retry()
            return

        button_id = event.button.id
        if button_id and button_id.startswith("choice-"):
            try:
                choice_idx = int(button_id.split("-")[-1])
                await self._trigger_choice(choice_idx)
            except (ValueError, IndexError) as e:
                logger.debug("Invalid choice button ID clicked: %s (%s)", button_id, e)

    async def action_choose(self, number: str) -> None:
        """Select a choice by its 1-based index."""
        idx = int(number) - 1
        prefix = f"choice-t{self.turn_count}-"
        for btn in self.query("Button"):
            if btn.id and btn.id.startswith(prefix) and btn.id.endswith(f"-{idx}"):
                await self._trigger_choice(idx)
                return
