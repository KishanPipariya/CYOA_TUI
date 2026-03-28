import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any, ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.reactive import reactive
from textual.theme import Theme
from textual.widgets import (
    Button,
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    Markdown,
    Static,
    Tree,
)

from cyoa.core import constants, utils
from cyoa.core.engine import StoryEngine
from cyoa.core.events import Events, bus
from cyoa.core.models import Choice, StoryNode
from cyoa.db.graph_db import CYOAGraphDB
from cyoa.db.rag_memory import NarrativeMemory, NPCMemory
from cyoa.llm.broker import ModelBroker, StoryContext
from cyoa.ui.ascii_art import SCENE_ART
from cyoa.ui.components import BranchScreen, ConfirmScreen, HelpScreen, ThemeSpinner

__all__ = ["CYOAApp"]


def _detect_scene_art(narrative: str) -> str | None:
    """Return ASCII art matching keywords found in the narrative, or None."""
    lower = narrative.lower()
    for scene_key, keywords in constants.SCENE_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return SCENE_ART.get(scene_key)
    return None


# Load the ASCII art for the initial screen
try:
    with open("loading_art.md", encoding="utf-8") as f:
        LOADING_ART = f.read()
except FileNotFoundError:
    LOADING_ART = "# Welcome to the Adventure\n\n*Loading the AI model... Please wait.*"


class CYOAApp(App):
    """A Choose-Your-Adventure Textual App."""

    # Fix #8: CSS loaded from external file
    CSS_PATH = "styles.tcss"

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
        Binding("space", "skip_typewriter", "Skip", show=True),
        Binding("1", "choose('1')", "Choice 1", show=False),
        Binding("2", "choose('2')", "Choice 2", show=False),
        Binding("3", "choose('3')", "Choice 3", show=False),
        Binding("4", "choose('4')", "Choice 4", show=False),
    ]

    # Fix #4: Reactive turn counter displayed in footer
    turn_count: reactive[int] = reactive(1)
    mood: reactive[str] = reactive("default")
    _themes_cached_config: dict[str, Any] | None = None

    def _load_themes_config(self) -> dict[str, Any]:
        """Load the mood-to-theme mapping from themes.json with rudimentary caching."""
        if self._themes_cached_config is not None:
            return self._themes_cached_config

        themes_path = Path(__file__).parent.parent.parent / "themes" / "themes.json"
        if themes_path.exists():
            try:
                with open(themes_path, encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self._themes_cached_config = data
                        return data
            except Exception:
                pass
        return {}

    def watch_mood(self, old_mood: str, new_mood: str) -> None:
        """Update the main container class and application theme when the mood changes."""
        try:
            container = self.query_one("#main-container")
            container.remove_class(f"mood-{old_mood}")
            container.add_class(f"mood-{new_mood}")

            # Look up atmospheric theme in themes.json
            themes_config = self._load_themes_config()
            # Try specific mood, then default, then return empty if none found
            mood_config = themes_config.get(new_mood, themes_config.get("default", {}))

            if mood_config:
                # 1. Update Spinner frames
                try:
                    spinner = self.query_one("#loading", ThemeSpinner)
                    if "spinner_frames" in mood_config:
                        spinner.frames = mood_config["spinner_frames"]
                        spinner._frame_idx = 0
                except Exception:
                    pass

                # 2. Update App Theme (accent color)
                accent = mood_config.get("accent_color")
                if accent:
                    from textual.theme import BUILTIN_THEMES

                    base_theme_name = "textual-dark" if self.dark else "textual-light"
                    base_theme = BUILTIN_THEMES.get(base_theme_name)
                    if base_theme:
                        theme_name = f"mood-{new_mood}"
                        # Re-register theme with new accent
                        self.register_theme(
                            Theme(
                                name=theme_name,
                                primary=base_theme.primary,
                                secondary=base_theme.secondary,
                                warning=base_theme.warning,
                                error=base_theme.error,
                                success=base_theme.success,
                                accent=accent,
                                foreground=base_theme.foreground,
                                background=base_theme.background,
                                surface=base_theme.surface,
                                panel=base_theme.panel,
                                boost=base_theme.boost,
                                dark=base_theme.dark,
                            )
                        )
                        self.theme = theme_name
        except Exception:
            pass

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
        self._current_story: str = LOADING_ART
        self._loading_suffix_shown: bool = False

        # Typewriter Narrator state

        # Typewriter Narrator state
        self._typewriter_queue: asyncio.Queue[str] = asyncio.Queue()
        self._typewriter_target: str = ""  # The full text we WANT to display
        self._typewriter_active_chunk: list[str] = []
        self._is_typing: bool = False

        # Undo: snapshot of previous turn state
        self._undo_snapshot: dict[str, Any] | None = None

        # Restore dark mode preference
        config = utils.load_config()
        self.dark = config.get("dark", True)

        # Apply theme accent color if specified
        if self._accent_color:
            from textual.theme import BUILTIN_THEMES

            base_theme = BUILTIN_THEMES.get("textual-dark")
            if base_theme:
                # Theme requires at least `primary` to be specified
                self.register_theme(
                    Theme(
                        name="cyoa-custom",
                        primary=base_theme.primary,
                        secondary=base_theme.secondary,
                        warning=base_theme.warning,
                        error=base_theme.error,
                        success=base_theme.success,
                        accent=self._accent_color,
                        foreground=base_theme.foreground,
                        background=base_theme.background,
                        surface=base_theme.surface,
                        panel=base_theme.panel,
                        boost=base_theme.boost,
                        dark=base_theme.dark,
                    )
                )
                self.theme = "cyoa-custom"

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Container(id="main-container"):
                with VerticalScroll(id="story-container"):
                    yield Markdown(LOADING_ART, id="story-text")
                    yield Static("", id="scene-art")
                # Dedicated status bar between story and choices
                with Container(id="status-bar"):
                    yield ThemeSpinner(frames=self.spinner_frames, id="loading")
                    yield Label(
                        "❤️ Health: 100 | 🪙 Gold: 0 | 🌟 Rep: 0",
                        id="stats-display",
                        classes="health-high",
                    )
                    yield Label("🎒 Inventory: Empty", id="inventory-display")
                with Container(id="choices-container"):
                    pass
            with Container(id="journal-panel", classes="hidden"):
                yield Label("In-Game Journal", id="journal-title")
                yield ListView(id="journal-list")
            with Container(id="story-map-panel", classes="hidden"):
                yield Label("Story Map", id="story-map-title")
                yield Tree("Story", id="story-map-tree")
        yield Footer()

    def watch_turn_count(self, count: int) -> None:
        self.sub_title = f"Turn {count}" if count > 0 else ""

    async def on_mount(self) -> None:
        self.query_one("#choices-container").border_title = "Choices"
        self.query_one("#story-container").border_title = "Story"

        # Subscribe to Engine Events
        bus.subscribe(Events.ENGINE_STARTED, self._handle_engine_started)
        bus.subscribe(Events.ENGINE_RESTARTED, self._handle_engine_restarted)
        bus.subscribe(Events.CHOICE_MADE, self._handle_choice_made)
        bus.subscribe(Events.NODE_GENERATING, self._handle_node_generating)
        bus.subscribe(Events.TOKEN_STREAMED, self._handle_token_streamed)
        bus.subscribe(Events.NODE_COMPLETED, self._handle_node_completed)
        bus.subscribe(Events.STATS_UPDATED, self._handle_stats_updated)
        bus.subscribe(Events.INVENTORY_UPDATED, self._handle_inventory_updated)
        bus.subscribe(Events.STORY_TITLE_GENERATED, self._handle_title_generated)
        bus.subscribe(Events.ENDING_REACHED, self._handle_ending_reached)
        bus.subscribe(Events.ERROR_OCCURRED, self._handle_error)
        bus.subscribe(Events.STATUS_MESSAGE, self._handle_status_message)

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

            await self.engine.initialize()

        except Exception as e:
            self.notify(f"Initial setup failed: {e}", severity="error", timeout=5)
            self.query_one("#loading").add_class("hidden")
            raise

    # ------------------------------------------------------------------
    # Event Handlers
    # ------------------------------------------------------------------

    def _handle_engine_started(self) -> None:
        self.turn_count = 1
        self.mood = "default"
        self.query_one("#journal-list", ListView).clear()

    def _handle_engine_restarted(self) -> None:
        self.notify("Adventure Reset.", severity="information", timeout=2)

    def _handle_choice_made(self, choice_text: str) -> None:
        self.action_skip_typewriter()
        # We don't append to _current_story here because it's handled in `_trigger_choice` for instant feedback
        # or maybe we should let the engine handle it?
        # For now, let's keep the UI responsive logic in `_trigger_choice`.
        pass

    def _handle_node_generating(self) -> None:
        self.show_loading()

    def _handle_token_streamed(self, token: str) -> None:
        self._stream_narrative(token)

    def _handle_node_completed(self, node: StoryNode) -> None:
        if self.engine:
            self.turn_count = self.engine.turn_count
        self.display_node(node)
        self.update_story_map()

    def _handle_stats_updated(self, stats: dict[str, int]) -> None:
        self._update_status_bar(stats)

    def _handle_inventory_updated(self, inventory: list[str]) -> None:
        self._update_status_bar(inventory=inventory)

    def _handle_title_generated(self, title: str) -> None:
        self.notify(f"New Chapter: {title}", severity="information", timeout=5)

    def _handle_ending_reached(self, node: StoryNode) -> None:
        self.notify("The Story Ends.", severity="success", timeout=10)

    def _handle_error(self, error: str) -> None:
        self.notify(f"Error: {error}", severity="error", timeout=5)
        self.query_one("#loading").add_class("hidden")

    def _handle_status_message(self, message: str) -> None:
        self.notify(message, severity="information", timeout=4)

    @work(group="speculation", exclusive=True)
    async def speculate_all_choices(self, node: StoryNode) -> None:
        """Sequential background generation of the most likely next scenes."""
        if not self.engine or not self.engine.story_context or not self.generator:
            return

        # Give the UI some breathing room
        await asyncio.sleep(2.0)

        for choice in node.choices:
            if self.engine.speculation_cache.get_node(self.engine.current_scene_id or "", choice.text):
                continue

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
                spec_node = await self.generator.generate_next_node_async(spec_context)
                self.engine.speculation_cache.set_node(
                    self.engine.current_scene_id or "",
                    choice.text,
                    spec_node
                )
            except Exception:
                continue

    @work(group="typewriter", exclusive=True)
    async def _typewriter_worker(self) -> None:
        """Background worker that smoothly reveals narrative text from the queue."""
        try:
            story_md = self.query_one("#story-text", Markdown)
        except Exception:
            return

        last_refresh = 0.0
        # Throttle Markdown re-renders to ~30fps max to avoid UI lag on long stories
        REFRESH_LIMIT = 0.033

        while True:
            # wait for text chunks
            chunk = await self._typewriter_queue.get()
            self._is_typing = True
            self._typewriter_active_chunk = list(chunk)

            while self._typewriter_active_chunk:
                self._handle_typewriter_batch()

                # Throttled UI update
                now = asyncio.get_event_loop().time()
                if now - last_refresh >= REFRESH_LIMIT or not self._typewriter_active_chunk:
                    story_md.update(self._current_story)
                    if self._is_at_bottom():
                        self._scroll_to_bottom(animate=False)
                    last_refresh = now

                if self._typewriter_active_chunk:
                    await asyncio.sleep(constants.TYPEWRITER_CHAR_DELAY)

            if self._typewriter_queue.empty():
                self._is_typing = False

    def _handle_typewriter_batch(self) -> None:
        """Process a batch of characters from the active chunk, handling catchup."""
        q_size = self._typewriter_queue.qsize()
        batch_size = 1
        if q_size > constants.TYPEWRITER_CATCHUP_THRESHOLD:
            # Extreme catchup: grab everything and exit loops
            self._current_story += "".join(self._typewriter_active_chunk)
            self._typewriter_active_chunk.clear()
            while not self._typewriter_queue.empty():
                self._current_story += self._typewriter_queue.get_nowait()
        elif q_size > 10:
            batch_size = constants.TYPEWRITER_MAX_BATCH

        if self._typewriter_active_chunk:
            to_add = "".join(self._typewriter_active_chunk[:batch_size])
            self._typewriter_active_chunk = self._typewriter_active_chunk[batch_size:]
            self._current_story += to_add

    def action_skip_typewriter(self) -> None:
        """Instantly reveal all pending text in the typewriter queue."""
        if not self._is_typing and self._typewriter_queue.empty():
            return

        # Flush active chunk
        if self._typewriter_active_chunk:
            self._current_story += "".join(self._typewriter_active_chunk)
            self._typewriter_active_chunk.clear()

        # Flush queue
        while not self._typewriter_queue.empty():
            try:
                self._current_story += self._typewriter_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._is_typing = False
        try:
            self.query_one("#story-text", Markdown).update(self._current_story)
            self._scroll_to_bottom()
        except Exception:
            pass

    def on_click(self) -> None:
        """Skip the typewriter animation on click."""
        self.action_skip_typewriter()

    def _stream_narrative(self, partial: str) -> None:
        """
        Streaming callback: feeds the typewriter queue instead of updating UI directly.
        """
        if self._loading_suffix_shown:
            # First token batch arrived — strip the loading placeholder
            suffix = "\n\n*(The ancient texts are shifting...)*"
            if self._current_story.endswith(suffix):
                self._current_story = self._current_story[: -len(suffix)]
            self._loading_suffix_shown = False
            self.query_one("#loading").add_class("hidden")

            # Start of a new turn: prepend separator via the queue
            if self._current_story == LOADING_ART:
                self._current_story = ""
                self._typewriter_queue.put_nowait(partial)
            else:
                self._typewriter_queue.put_nowait(f"\n\n---\n\n{partial}")
        else:
            self._typewriter_queue.put_nowait(partial)

    def show_loading(self, selected_label: str | None = None) -> None:
        """Clear choice buttons, show spinner, append 'shifting' text.

        If selected_label is given, all other buttons are removed and the
        selected one is kept visible but disabled so the player sees which
        choice was picked.
        """
        choices_container = self.query_one("#choices-container")
        if selected_label is not None:
            # Keep only the selected button, disable and dim it
            for btn in list(choices_container.query(Button)):
                if str(btn.label) != selected_label:
                    btn.remove()
                else:
                    btn.disabled = True
                    btn.variant = "default"
        else:
            choices_container.remove_children()
        self.query_one("#loading").remove_class("hidden")

        if not self._loading_suffix_shown:
            # Perf #2: append suffix and set flag — avoids str.replace later
            self._current_story += "\n\n*(The ancient texts are shifting...)*"
            self._loading_suffix_shown = True
            self.query_one("#story-text", Markdown).update(self._current_story)
            # Force scroll on new turn so player sees their choice immediately
            self._scroll_to_bottom()

    def _update_status_bar(
        self,
        stats: dict[str, int] | None = None,
        inventory: list[str] | None = None
    ) -> None:
        """Refresh the two-row status bar with color-coded health."""
        if not self.engine:
            return

        stats = stats if stats is not None else self.engine.player_stats
        inventory = inventory if inventory is not None else self.engine.inventory

        health = stats.get("health", 0)
        gold = stats.get("gold", 0)
        rep = stats.get("reputation", 0)

        # Color-coded health indicator
        if health <= 0:
            health_tag = f"💀 Health: {health} [[DEAD]]"
            css_class = "health-low"
        elif health < 30:
            health_tag = f"❤️ Health: {health} [[LOW]]"
            css_class = "health-low"
        elif health < 70:
            health_tag = f"❤️ Health: {health}"
            css_class = "health-mid"
        else:
            health_tag = f"❤️ Health: {health}"
            css_class = "health-high"

        stats_label = self.query_one("#stats-display", Label)
        stats_label.update(f"{health_tag} | 🪙 Gold: {gold} | 🌟 Rep: {rep}")
        stats_label.remove_class("health-high", "health-mid", "health-low")
        stats_label.add_class(css_class)

        inv_str = (
            f"🎒 Inventory: {', '.join(inventory)}"
            if inventory
            else "🎒 Inventory: Empty"
        )
        self.query_one("#inventory-display", Label).update(inv_str)

    def _is_at_bottom(self) -> bool:
        """Return True if the story container is near its bottom edge.

        This allows 'smart' scrolling: following the narrative only if the user
        was already at the bottom of the story.
        """
        try:
            container = self.query_one("#story-container")
            # A more lenient threshold (8.0) accounts for layout offsets,
            # varied line heights, and padding, making the scroll more "sticky".
            return container.scroll_y >= container.max_scroll_y - 8
        except Exception:
            return True

    def _scroll_to_bottom(self, animate: bool = True) -> None:
        """Scroll the story container to the end after the next refresh."""
        try:
            container = self.query_one("#story-container")
            self.call_after_refresh(lambda: container.scroll_end(animate=animate))
        except Exception:
            pass

    def display_node(self, node: StoryNode) -> None:
        """Render a newly generated StoryNode to the UI (after streaming completes)."""
        self.query_one("#loading").add_class("hidden")
        self.mood = getattr(node, "mood", "default")

        is_error = node.narrative.startswith(constants.ERROR_NARRATIVE_PREFIX)

        self.query_one("#story-text", Markdown)

        # Detect and update the separate ASCII art widget
        art = _detect_scene_art(node.narrative) if not is_error else None
        art_widget = self.query_one("#scene-art", Static)
        if art:
            art_widget.update(art)
            art_widget.remove_class("hidden")
        else:
            art_widget.update("")
            art_widget.add_class("hidden")

        # Fallback/Cache hit: nothing happened in _stream_narrative
        if self._loading_suffix_shown:
            self._loading_suffix_shown = False
            suffix = "\n\n*(The ancient texts are shifting...)*"
            if self._current_story.endswith(suffix):
                self._current_story = self._current_story[: -len(suffix)]

            sep = "\n\n---\n\n" if self._current_story != LOADING_ART else ""
            self._typewriter_queue.put_nowait(f"{sep}{node.narrative}")
        else:
            # Streaming happened. Sync to the finalized narrative.
            self.action_skip_typewriter()

            last_sep = self._current_story.rfind("\n\n---\n\n")
            if last_sep != -1:
                prefix = self._current_story[: last_sep + len("\n\n---\n\n")]
                self._current_story = prefix + node.narrative
            else:
                # First node or missing separator
                self._current_story = node.narrative

        if is_error and "⚠️" not in node.narrative:
            self._current_story += "\n\n> ⚠️ **An error occurred.** The story engine could not generate a valid response."

        try:
            self.query_one("#story-text", Markdown).update(self._current_story)
        except Exception:
            pass

        # memory.add() moved to worker thread (generate_next_step)
        # so chromadb embedding does not block the UI event loop here.

        self._update_status_bar()

        choices_container = self.query_one("#choices-container", Container)
        # Clear any leftover stale buttons (e.g. the disabled selected-choice button)
        choices_container.remove_children()
        self._mount_choice_buttons(node, choices_container, is_error)

        # Trigger background speculation for the current node's choices
        self.speculate_all_choices(node)

        # Ensure the view is at the bottom after narrative and choices are fully rendered
        self._scroll_to_bottom()

    def _mount_choice_buttons(
        self, node: StoryNode, choices_container: Container, is_error: bool
    ) -> None:
        """Mount choice buttons based on the node state."""
        # Error UX: show a Retry button alongside the fallback choice
        if is_error:
            choices_container.mount(
                Button("🔄 Retry Generation", id="btn-retry", variant="warning")
            )
            for i, choice in enumerate(node.choices):
                btn_id = f"choice-{uuid.uuid4().hex[:8]}"
                btn = Button(f"[b]{i + 1}[/b]  {choice.text}", id=btn_id, variant="default")
                choices_container.mount(btn)
        elif node.is_ending:
            end_btn = Button("✦ Start a New Adventure", id="btn-new-adventure", variant="success")
            choices_container.mount(end_btn)
        else:
            for i, choice in enumerate(node.choices):
                btn_id = f"choice-t{self.turn_count}-{i}"
                btn = Button(f"[b]{i + 1}[/b]  {choice.text}", id=btn_id, variant="primary")
                choices_container.mount(btn)

    async def _trigger_choice(self, choice_idx: int) -> None:
        """Handle choice selection and delegate to the engine."""
        if (
            not self.engine
            or not self.engine.current_node
            or choice_idx >= len(self.engine.current_node.choices)
        ):
            return

        choice = self.engine.current_node.choices[choice_idx]
        choice_text = choice.text

        # 1. Instant UI feedback
        self.action_skip_typewriter()
        self._current_story += f"\n\n> **You chose:** {choice_text}"

        selected_label = f"[{choice_idx + 1}] {choice_text}"
        self.show_loading(selected_label=selected_label)

        # 2. Journal update
        journal_list = self.query_one("#journal-list", ListView)
        narrative_preview = self.engine.current_node.narrative[:60].replace("\n", " ").strip()
        if len(self.engine.current_node.narrative) > 60:
            narrative_preview += "…"
        journal_entry = f"Turn {self.engine.turn_count}: {choice_text} → {narrative_preview}"
        journal_list.append(ListItem(Label(journal_entry)))
        journal_list.scroll_end(animate=False)

        # 3. Cancel speculations and let the engine handle the rest
        self.workers.cancel_group(self, "speculation")
        await self.engine.make_choice(choice_text)

    async def action_restart(self) -> None:
        """Reset story state via the engine."""
        if not self.engine:
            return

        self._current_story = LOADING_ART
        self.query_one("#story-text", Markdown).update(LOADING_ART)
        self.query_one("#scene-art", Static).update("")
        self.query_one("#scene-art", Static).add_class("hidden")
        self.query_one("#choices-container").remove_children()
        self.query_one("#journal-list", ListView).clear()

        self._update_status_bar()
        await self.engine.restart()

    # UX: Confirmation before restart
    def action_request_restart(self) -> None:
        """Show a confirmation dialog before restarting the adventure."""

        def on_confirm(confirmed: bool | None) -> None:
            if confirmed:
                self.run_worker(self.action_restart(), exclusive=True)

        self.push_screen(
            ConfirmScreen("[b]Restart the adventure?[/b]\n\nAll progress will be lost."),
            on_confirm,
        )

    # UX: Confirmation before quit
    def action_request_quit(self) -> None:
        """Show a confirmation dialog before quitting."""

        def on_confirm(confirmed: bool | None) -> None:
            if confirmed:
                self.exit()

        self.push_screen(
            ConfirmScreen("[b]Quit the game?[/b]\n\nUnsaved progress will be lost."),
            on_confirm,
        )

    # UX: Help screen
    def action_show_help(self) -> None:
        """Show the help screen with keybindings and game mechanics."""
        self.push_screen(HelpScreen())

    def action_undo(self) -> None:
        """Restore the game state to before the last choice was made."""
        if not self.engine:
            return

        # Engine handles core state restoration
        if not self.engine.undo():
            self.notify("Nothing to undo.", severity="warning", timeout=2)
            return

        # UI-specific restoration
        # Note: self._current_story needs careful handling since it's the cumulative Markdown
        # Find the last separator and truncate back to it.
        sep = "\n\n> **You chose:**"
        last_choice_pos = self._current_story.rfind(sep)
        if last_choice_pos != -1:
            self._current_story = self._current_story[:last_choice_pos]

        self.query_one("#story-text", Markdown).update(self._current_story)
        self.query_one("#loading").add_class("hidden")
        self._scroll_to_bottom()

        # Remove the last journal entry
        journal_list = self.query_one("#journal-list", ListView)
        children = list(journal_list.children)
        if children:
            children[-1].remove()

        self.notify("↩ Undid last choice.", severity="information", timeout=2)

    def action_save_game(self) -> None:
        """Serialize the current game state to a JSON save file."""
        if not self.engine or not self.engine.story_title or not self.engine.current_node:
            self.notify("Nothing to save yet.", severity="warning", timeout=2)
            return

        os.makedirs(constants.SAVES_DIR, exist_ok=True)
        safe_title = "".join(
            c if c.isalnum() or c in " _-" else "_" for c in self.engine.story_title
        )
        save_path = os.path.join(constants.SAVES_DIR, f"{safe_title}_turn{self.engine.turn_count}.json")

        save_data = self.engine.get_save_data()
        save_data["current_story_text"] = self._current_story

        try:
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(save_data, f, indent=2, ensure_ascii=False)
            self.notify(f"💾 Game saved to {save_path}", severity="information", timeout=3)
        except OSError as e:
            self.notify(f"Save failed: {e}", severity="error", timeout=3)

    # UX: Load game from JSON
    def action_load_game(self) -> None:
        """Show available save files and load a selected one."""
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
        try:
            with open(save_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self.notify(f"Load failed: {e}", severity="error", timeout=3)
            return

        if not self.engine:
            return

        self._current_story = data.get("current_story_text", LOADING_ART)
        self._loading_suffix_shown = False

        self.engine.load_save_data(data)

        # Sync UI
        self.query_one("#story-text", Markdown).update(self._current_story)
        self._scroll_to_bottom()
        self.query_one("#choices-container").remove_children()
        self.query_one("#journal-list", ListView).clear()

        self.notify(
            f"📂 Loaded save from Turn {self.engine.turn_count}.",
            severity="information",
            timeout=3,
        )

    # Persist dark mode preference when toggled
    def action_toggle_dark(self) -> None:
        self.dark = not self.dark
        utils.save_config({"dark": self.dark})

    def action_toggle_journal(self) -> None:
        """Toggle the visibility of the side journal panel."""
        panel = self.query_one("#journal-panel")
        panel.toggle_class("hidden")

    def action_toggle_story_map(self) -> None:
        """Toggle the visibility of the story map panel."""
        panel = self.query_one("#story-map-panel")
        panel.toggle_class("hidden")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-new-adventure":
            await self.action_restart()
            return

        if event.button.id == "btn-retry":
            self.show_loading()
            if self.engine:
                await self.engine._generate_next()
            return

        button_id = event.button.id
        if button_id and button_id.startswith("choice-"):
            try:
                choice_idx = int(button_id.split("-")[-1])
                await self._trigger_choice(choice_idx)
            except (ValueError, IndexError):
                pass

    async def action_choose(self, number: str) -> None:
        """Select a choice by its 1-based index."""
        idx = int(number) - 1
        query = self.query(f"#choice-t{self.turn_count}-{idx}")
        if query:
            await self._trigger_choice(idx)

    @work(exclusive=True)
    async def action_branch_past(self) -> None:
        if not self.engine or not self.engine.db or not self.engine.current_scene_id:
            return

        history = await asyncio.to_thread(self.engine.db.get_scene_history_path, self.engine.current_scene_id)
        if not history or not history.get("scenes"):
            return

        def check_branch(idx: int | None) -> None:
            if idx is not None:
                self.restore_to_scene(idx, history)

        self.push_screen(BranchScreen(history["scenes"], history["choices"]), check_branch)

    @work(exclusive=True)
    async def restore_to_scene(self, idx: int, history: dict[str, Any]) -> None:
        if not self.engine:
            return

        self.query_one("#choices-container").remove_children()
        self.query_one("#loading").remove_class("hidden")

        fracture_msg = f"\n\n***\n\n**[Time fractures... you return to Turn {idx + 1}]**"
        self._current_story += fracture_msg
        self.query_one("#story-text", Markdown).update(self._current_story)
        self._scroll_to_bottom()

        # Update engine state manually for branch (future: engine should have a branch method)
        target_scene = history["scenes"][idx]
        self.engine.story_context = StoryContext(
            starting_prompt=self.engine.starting_prompt,
            token_budget=self.generator.token_budget if self.generator else 2048,
            token_counter=self.generator.provider.count_tokens if self.generator else None,
        )
        for i in range(idx):
            self.engine.story_context.add_turn(history["scenes"][i]["narrative"], history["choices"][i])

        self.engine.current_scene_id = target_scene["id"]
        self.engine.last_choice_text = history["choices"][idx - 1] if idx > 0 else None
        self.engine.turn_count = idx + 1
        self.engine.inventory = []
        self.engine.player_stats = {"health": 100, "gold": 0, "reputation": 0}

        # Clear and rebuild memories
        self.engine.memory = NarrativeMemory()
        self.engine.npc_memory = NPCMemory()
        for i in range(idx + 1):
            past_scene_id = history["scenes"][i]["id"]
            past_narrative = history["scenes"][i]["narrative"]
            await self.engine.memory.add_async(past_scene_id, past_narrative)

        # Restore model state
        if self.generator:
            state = self.engine.speculation_cache.get_state(self.engine.current_scene_id)
            if state:
                await self.generator.load_state_async(state)

        journal_list = self.query_one("#journal-list", ListView)
        journal_list.clear()
        for i in range(idx):
            journal_list.append(ListItem(Label(f"Turn {i + 1}: {history['choices'][i]}")))
        journal_list.scroll_end(animate=False)

        available = target_scene.get("available_choices") or []
        choices = [Choice(text=c) for c in available]
        node = StoryNode(
            narrative=target_scene["narrative"],
            choices=choices,
            is_ending=len(choices) == 0,
        )
        self.engine.current_node = node
        self.display_node(node)
        self.update_story_map()

    @work(exclusive=True)
    async def update_story_map(self) -> None:
        if not self.engine or not self.engine.db or not self.engine.story_title:
            return

        tree_data = await asyncio.to_thread(self.engine.db.get_story_tree, self.engine.story_title)
        if not tree_data:
            return

        try:
            tree = self.query_one("#story-map-tree", Tree)
        except Exception:  # noqa: BLE001
            return
        tree.clear()

        nodes = tree_data.get("nodes", {})
        edges = tree_data.get("edges", {})
        root_id = tree_data.get("root_id")

        if not root_id:
            return

        def add_children(parent_node: Any, scene_id: str) -> None:
            scene = nodes[scene_id]
            preview = scene["narrative"][:25].replace("\\n", " ").strip() + "..."
            if scene_id == self.engine.current_scene_id:
                label = f"[b][green]> {preview}[/green][/b]"
            else:
                label = preview

            tree_node = parent_node.add(label, expand=True)

            for edge in edges.get(scene_id, []):
                choice_text = edge["choice"]
                choice_preview = (
                    choice_text[: constants.MAX_CHOICE_PREVIEW_LEN] + "..."
                    if len(choice_text) > constants.MAX_CHOICE_PREVIEW_LEN
                    else choice_text
                )
                choice_label = f"[dim][i]- {choice_preview}[/i][/dim]"
                choice_node = tree_node.add(choice_label, expand=True)
                add_children(choice_node, edge["target_id"])

        tree.root.label = "Story Nodes"
        tree.root.expand()
        if root_id in nodes:
            add_children(tree.root, root_id)
