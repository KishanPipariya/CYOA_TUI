import uuid
import json
import copy
import os
from textual.app import App, ComposeResult  # type: ignore
from textual.containers import Container, VerticalScroll, Horizontal  # type: ignore
from textual.widgets import (
    Header,
    Footer,
    Markdown,
    Button,
    ListView,
    ListItem,
    Label,
    Tree,
)  # type: ignore
from textual.reactive import reactive  # type: ignore
from textual import work  # type: ignore
from textual.theme import Theme  # type: ignore
from typing import Any, Optional, ClassVar
import asyncio

from cyoa.core.models import StoryNode, Choice
from cyoa.llm.broker import ModelBroker, StoryContext, DEFAULT_TOKEN_BUDGET, SpeculationCache
from cyoa.db.graph_db import CYOAGraphDB
from cyoa.db.rag_memory import NarrativeMemory, NPCMemory
from cyoa.ui.components import BranchScreen, ThemeSpinner, ConfirmScreen, HelpScreen
from cyoa.ui.ascii_art import SCENE_ART
from cyoa.core.events import bus
from cyoa.core import constants, utils

__all__ = ["CYOAApp"]


def _adaptive_throttle(story_length: int) -> int:
    """Return a throttle value that increases with story length to avoid
    expensive Markdown re-parses on long stories."""
    if story_length < 2000:
        return constants.STREAM_RENDER_THROTTLE_BASE
    elif story_length < 5000:
        return 16
    elif story_length < 10000:
        return 32
    return constants.STREAM_RENDER_THROTTLE_MAX


def _detect_scene_art(narrative: str) -> str | None:
    """Return ASCII art matching keywords found in the narrative, or None."""
    lower = narrative.lower()
    for scene_key, keywords in constants.SCENE_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return SCENE_ART.get(scene_key)
    return None


# Load the ASCII art for the initial screen
try:
    with open("loading_art.md", "r", encoding="utf-8") as f:
        LOADING_ART = f.read()
except FileNotFoundError:
    LOADING_ART = "# Welcome to the Adventure\n\n*Loading the AI model... Please wait.*"


class CYOAApp(App):
    """A Choose-Your-Adventure Textual App."""

    # Fix #8: CSS loaded from external file
    CSS_PATH = "styles.tcss"

    BINDINGS: ClassVar[list[Any]] = [
        ("d", "toggle_dark", "Toggle dark mode"),
        ("b", "branch_past", "Branch from Past"),
        ("j", "toggle_journal", "Toggle Journal"),
        ("m", "toggle_story_map", "Toggle Story Map"),
        ("h", "show_help", "Help"),
        ("u", "undo", "Undo"),
        ("s", "save_game", "Save"),
        ("l", "load_game", "Load"),
        ("q", "request_quit", "Quit"),
        ("r", "request_restart", "Restart"),
        ("space", "skip_typewriter", "Skip Narrator"),
        ("1", "choose('1')", "Choice 1"),
        ("2", "choose('2')", "Choice 2"),
        ("3", "choose('3')", "Choice 3"),
        ("4", "choose('4')", "Choice 4"),
    ]

    # Fix #4: Reactive turn counter displayed in footer
    turn_count: reactive[int] = reactive(1)

    def __init__(
        self,
        model_path: str,
        starting_prompt: str = constants.DEFAULT_STARTING_PROMPT,
        spinner_frames: Optional[list[str]] = None,
        accent_color: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.model_path = model_path
        self.starting_prompt = starting_prompt
        self.spinner_frames = spinner_frames or ["[-]", "[\\]", "[|]", "[/]"]
        self._accent_color = accent_color

        self.generator: Optional[ModelBroker] = None
        self.story_context: Optional[StoryContext] = None
        self.db: Optional[CYOAGraphDB] = None
        self.current_scene_id: Optional[str] = None
        self.last_choice_text: Optional[str] = None
        self.current_story_title: Optional[str] = None
        self._last_raw_narrative: Optional[str] = None

        self._loading_suffix_shown: bool = False
        self._current_story: str = LOADING_ART

        # Procedural inventory tracking
        self.inventory: list[str] = []
        self.player_stats: dict[str, int] = {"health": 100, "gold": 0, "reputation": 0}
        self.current_node: Optional[StoryNode] = None
        self._stream_token_buffer: int = 0
        # RAG: in-memory semantic scene store
        self.memory = NarrativeMemory()
        self.npc_memory = NPCMemory()
        self.speculation_cache = SpeculationCache()

        # Typewriter Narrator state
        self._typewriter_queue: asyncio.Queue[str] = asyncio.Queue()
        self._typewriter_target: str = ""  # The full text we WANT to display
        self._typewriter_active_chunk: list[str] = []
        self._is_typing: bool = False

        # Undo: snapshot of previous turn state
        self._undo_snapshot: Optional[dict[str, Any]] = None

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
        # Fix #4: Update footer subtitle with turn counter
        self.sub_title = f"Turn {count}" if count > 0 else ""

    async def on_mount(self) -> None:
        self.query_one("#choices-container").border_title = "Choices"
        self.query_one("#story-container").border_title = "Story"
        # Fix #6: Show spinner immediately before model even begins loading
        self.query_one("#loading").remove_class("hidden")
        # Start the typewriter narrator worker
        self._typewriter_worker()
        # Short delay to let the UI paint the ASCII art + spinner before blocking
        self.set_timer(0.1, lambda: self.initialize_and_start(self.model_path))

    def on_unmount(self) -> None:
        """Cancel all background work and release the LLM model and graph DB."""
        self.workers.cancel_all()
        if self.generator:
            self.generator.close()
        if self.db:
            self.db.close()

    @work(exclusive=True)
    async def initialize_and_start(self, model_path: str) -> None:
        """Load model and generate the first scene. Reuses existing model if already loaded."""
        if self.generator is None:
            self.generator = ModelBroker(model_path=model_path)

        self.story_context = StoryContext(
            starting_prompt=self.starting_prompt,
            token_budget=self.generator.token_budget,
            token_counter=self.generator.provider.count_tokens,
        )
        self.show_loading()

        if self.db is None:
            self.db = CYOAGraphDB()

        def on_token(partial: str) -> None:
            self._stream_narrative(partial)

        node = await self.generator.generate_next_node_async(
            self.story_context, on_token_chunk=on_token
        )
        self._last_raw_narrative = node.narrative
        self.current_node = node
        for item in getattr(node, "items_gained", []):
            if item not in self.inventory:
                self.inventory.append(item)
        for item in getattr(node, "items_lost", []):
            if item in self.inventory:
                self.inventory.remove(item)

        for stat, change in getattr(node, "stat_updates", {}).items():
            self.player_stats[stat] = self.player_stats.get(stat, 0) + change

        generated_title = node.title if node.title else "Untitled Adventure"
        self.current_story_title = await asyncio.to_thread(
            self.db.create_story_node_and_get_title, generated_title
        )

        bus.emit("story_started", title=self.current_story_title)

        if self.db:
            choices_text = [choice.text for choice in node.choices]
            new_id = await self.db.save_scene_async(
                narrative=node.narrative,
                available_choices=choices_text,
                story_title=self.current_story_title,
                source_scene_id=None,
                choice_text=None,
            )
            self.current_scene_id = new_id
            self.update_story_map()

        self.display_node(node)

    @work(group="speculation", exclusive=True)
    async def speculate_all_choices(self, node: StoryNode) -> None:
        """Sequential background generation of the most likely next scenes."""
        if not self.story_context or not self.generator:
            return

        # Give the UI some breathing room after the main generation finishes
        await asyncio.sleep(2.0)

        for i, choice in enumerate(node.choices):
            # If the user already picked a choice and main generation started,
            # this worker group will be canceled, so we don't need explicit checks here.
            key = f"{self.current_scene_id}:{choice.text}"
            if self.speculation_cache.get_node(self.current_scene_id or "", choice.text):
                continue

            # Clone context to speculate without polluting the main one
            spec_context = self.story_context.clone()
            spec_context.add_turn(node.narrative, choice.text, self.inventory, self.player_stats)

            try:
                # Low-priority generation (no streaming)
                spec_node = await self.generator.generate_next_node_async(spec_context)
                self.speculation_cache.set_node(self.current_scene_id or "", choice.text, spec_node)
                # logger.info("Speculated next scene for: %s", choice.text)
            except Exception: # noqa: BLE001
                # Failure in speculation is acceptable; main path will handle it
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
                # Catch up if the queue is backing up
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

    def _update_status_bar(self) -> None:
        """Refresh the two-row status bar with color-coded health."""
        health = self.player_stats.get("health", 0)
        gold = self.player_stats.get("gold", 0)
        rep = self.player_stats.get("reputation", 0)

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
            f"🎒 Inventory: {', '.join(self.inventory)}"
            if self.inventory
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
            # A small threshold (2.0) accounts for layout offsets or padding.
            return container.scroll_y >= container.max_scroll_y - 2
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

        is_error = node.narrative.startswith(constants.ERROR_NARRATIVE_PREFIX)

        story_md = self.query_one("#story-text", Markdown)

        # Detect and prepend matching ASCII art for this scene
        art = _detect_scene_art(node.narrative) if not is_error else None
        art_block = f"\n```\n{art}\n```\n" if art else ""

        # Fallback/Cache hit: nothing happened in _stream_narrative
        if self._loading_suffix_shown:
            self._loading_suffix_shown = False
            suffix = "\n\n*(The ancient texts are shifting...)*"
            if self._current_story.endswith(suffix):
                self._current_story = self._current_story[: -len(suffix)]

            sep = "\n\n---\n\n" if self._current_story != LOADING_ART else ""
            self._typewriter_queue.put_nowait(f"{sep}{art_block}{node.narrative}")
        else:
            # Streaming happened. To ensure ASCII art is included and any partial
            # stream is cleaned up, we sync to the finalized narrative.
            # We skip the narrator to avoid "double-typing" or missing art.
            self.action_skip_typewriter()

            last_sep = self._current_story.rfind("\n\n---\n\n")
            if last_sep != -1:
                prefix = self._current_story[: last_sep + len("\n\n---\n\n")]
                self._current_story = prefix + art_block + node.narrative
            else:
                # First node or missing separator
                self._current_story = art_block + node.narrative
        
        try:
            self.query_one("#story-text", Markdown).update(self._current_story)
        except Exception:
            pass

        if is_error:
            error_display = self._current_story
            # Append a visual error marker if not already present
            if "⚠️" not in node.narrative:
                error_suffix = "\n\n> ⚠️ **An error occurred.** The story engine could not generate a valid response."
                error_display = self._current_story + error_suffix
                self._current_story = error_display

        at_bottom = self._is_at_bottom()
        try:
            self.query_one("#story-text", Markdown).update(self._current_story)
        except Exception:
            pass

        # Smart following: only scroll to the new node if the user was already at the bottom
        if at_bottom:
            self._scroll_to_bottom()

        # memory.add() moved to worker thread (generate_next_step)
        # so chromadb embedding does not block the UI event loop here.

        self._update_status_bar()

        choices_container = self.query_one("#choices-container")
        # Clear any leftover stale buttons (e.g. the disabled selected-choice button)
        choices_container.remove_children()

        # Error UX: show a Retry button alongside the fallback choice
        if is_error:
            retry_btn = Button("🔄 Retry Generation", id="btn-retry", variant="warning")
            choices_container.mount(retry_btn)
            for i, choice in enumerate(node.choices):
                btn_id = f"choice-{uuid.uuid4().hex[:8]}"
                label = f"[{i + 1}] {choice.text}"
                btn = Button(label, id=btn_id, variant="default")
                choices_container.mount(btn)
            return

        if node.is_ending:
            end_btn = Button(
                "✦ Start a New Adventure", id="btn-new-adventure", variant="success"
            )
            choices_container.mount(end_btn)
            return

        for i, choice in enumerate(node.choices):
            btn_id = f"choice-t{self.turn_count}-{i}"
            label = f"[{i + 1}] {choice.text}"
            btn = Button(label, id=btn_id, variant="primary")
            choices_container.mount(btn)

        # Trigger background speculation for the current node's choices
        self.speculate_all_choices(node)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        # Handle the end-game "New Adventure" button
        if event.button.id == "btn-new-adventure":
            await self.action_restart()
            return

        # Handle the error retry button
        if event.button.id == "btn-retry":
            self.show_loading()
            self.generate_next_step()
            return

        # Find which choice button was actually clicked by its ID (choice-t1-0, etc)
        button_id = event.button.id
        if button_id and button_id.startswith("choice-"):
            try:
                # Part 0: 'choice', Part 1: 'tX', Part 2: '0'
                choice_idx = int(button_id.split("-")[-1])
                self._trigger_choice(choice_idx)
            except (ValueError, IndexError):
                pass

    # Fix #1: Keyboard number shortcut to select a choice
    def action_choose(self, number: str) -> None:
        """Select a choice by its 1-based index using number keys."""
        idx = int(number) - 1
        # Check if the specifically indexed choice button exists for this turn
        query = self.query(f"#choice-t{self.turn_count}-{idx}")
        if query:
            self._trigger_choice(idx)

    def _trigger_choice(self, choice_idx: int) -> None:
        """Handle choice selection, update the narrative, and query LLM."""
        if (
            not self.story_context
            or not self.current_node
            or choice_idx >= len(self.current_node.choices)
        ):
            return

        # NEW: Ensure previous turn's typing is finished before starting a new turn
        self.action_skip_typewriter()

        # Snapshot state for undo before making changes
        self._undo_snapshot = {
            "turn_count": self.turn_count,
            "current_story": self._current_story,
            "current_node": self.current_node,
            "current_scene_id": self.current_scene_id,
            "last_choice_text": self.last_choice_text,
            "last_raw_narrative": self._last_raw_narrative,
            "inventory": list(self.inventory),
            "player_stats": dict(self.player_stats),
            "story_context_history": copy.deepcopy(self.story_context.history),
        }

        choice_text = self.current_node.choices[choice_idx].text
        selected_label = f"[{choice_idx + 1}] {choice_text}"
        self.last_choice_text = choice_text
        if self.story_context:
            self.story_context.add_turn(
                self.current_node.narrative,
                choice_text,
                self.inventory,
                self.player_stats,
            )
        self.turn_count += 1

        bus.emit("choice_made", choice_text=choice_text)

        self._current_story += f"\n\n> **You chose:** {choice_text}"

        # Append enriched choice to the journal (includes narrative summary)
        journal_list = self.query_one("#journal-list", ListView)
        narrative_preview = self.current_node.narrative[:60].replace("\n", " ").strip()
        if len(self.current_node.narrative) > 60:
            narrative_preview += "…"
        journal_entry = f"Turn {self.turn_count}: {choice_text} → {narrative_preview}"
        journal_list.append(ListItem(Label(journal_entry)))
        journal_list.scroll_end(animate=False)

        self.show_loading(selected_label=selected_label)
        # Cancel any ongoing speculation for other choices
        self.workers.cancel_group(self, "speculation")
        self.generate_next_step(choice_text=choice_text)

    # Fix #2: In-app restart without reloading the model
    async def action_restart(self) -> None:
        """Reset story state and start a new adventure without reloading the model."""
        self._current_story = LOADING_ART
        self.turn_count = 1
        self.current_scene_id = None
        self.last_choice_text = None
        self._last_raw_narrative = None
        self._stream_token_buffer = 0
        self.inventory = []
        self.player_stats = {"health": 100, "gold": 0, "reputation": 0}
        self.current_node = None  # Added
        # Fix #8: reset memory so the new adventure doesn't inherit old scene embeddings
        self.memory = NarrativeMemory()
        self.npc_memory = NPCMemory()

        self.query_one("#story-text", Markdown).update(LOADING_ART)
        # Fix #3: use remove_children() instead of query+remove loop
        self.query_one("#choices-container").remove_children()
        self.query_one("#journal-list", ListView).clear()

        # Reset status bar
        self._update_status_bar()

        self.set_timer(0.1, lambda: self.initialize_and_start(self.model_path))

    # UX: Confirmation before restart
    def action_request_restart(self) -> None:
        """Show a confirmation dialog before restarting the adventure."""

        def on_confirm(confirmed: bool | None) -> None:
            if confirmed:
                self.run_worker(self.action_restart(), exclusive=True)

        self.push_screen(
            ConfirmScreen(
                "[b]Restart the adventure?[/b]\n\nAll progress will be lost."
            ),
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

    # UX: Single-turn undo
    def action_undo(self) -> None:
        """Restore the game state to before the last choice was made."""
        snap = self._undo_snapshot
        if not snap:
            self.notify("Nothing to undo.", severity="warning", timeout=2)
            return

        self.turn_count = snap["turn_count"]
        self._current_story = snap["current_story"]
        self.current_node = snap["current_node"]
        self.current_scene_id = snap["current_scene_id"]
        self.last_choice_text = snap["last_choice_text"]
        self._last_raw_narrative = snap["last_raw_narrative"]
        self.inventory = list(snap["inventory"])
        self.player_stats = dict(snap["player_stats"])
        if self.story_context:
            self.story_context.history = snap["story_context_history"]

        # Restore KV cache state if available for faster re-generation
        if self.generator and self.current_scene_id:
            state = self.speculation_cache.get_state(self.current_scene_id)
            if state:
                self.run_worker(self.generator.load_state_async(state))

        self._undo_snapshot = None  # Only one level of undo
        self._loading_suffix_shown = False

        # Re-render UI — directly re-mount buttons (don't call display_node
        # because it re-processes story text and would add duplicate art)
        self.query_one("#story-text", Markdown).update(self._current_story)
        self.query_one("#loading").add_class("hidden")
        self._scroll_to_bottom()
        choices_container = self.query_one("#choices-container")
        choices_container.remove_children()
        if self.current_node:
            for i, choice in enumerate(self.current_node.choices):
                btn_id = f"choice-{uuid.uuid4().hex[:8]}"
                label = f"[{i + 1}] {choice.text}"
                btn = Button(label, id=btn_id, variant="primary")
                choices_container.mount(btn)
        self._update_status_bar()

        # Remove the last journal entry
        journal_list = self.query_one("#journal-list", ListView)
        children = list(journal_list.children)
        if children:
            children[-1].remove()

        self.notify("↩ Undid last choice.", severity="information", timeout=2)

    # UX: Save game to JSON
    def action_save_game(self) -> None:
        """Serialize the current game state to a JSON save file."""
        if not self.current_story_title or not self.current_node:
            self.notify("Nothing to save yet.", severity="warning", timeout=2)
            return

        os.makedirs(constants.SAVES_DIR, exist_ok=True)
        # Build a safe filename from the story title
        safe_title = "".join(
            c if c.isalnum() or c in " _-" else "_" for c in self.current_story_title
        )
        save_path = os.path.join(constants.SAVES_DIR, f"{safe_title}_turn{self.turn_count}.json")

        save_data = {
            "version": 1,
            "story_title": self.current_story_title,
            "turn_count": self.turn_count,
            "current_story_text": self._current_story,
            "inventory": self.inventory,
            "player_stats": self.player_stats,
            "starting_prompt": self.starting_prompt,
            "current_node": self.current_node.model_dump()
            if self.current_node
            else None,
            "context_history": self.story_context.history if self.story_context else [],
            "current_scene_id": self.current_scene_id,
            "last_choice_text": self.last_choice_text,
            "last_raw_narrative": self._last_raw_narrative,
        }

        try:
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(save_data, f, indent=2, ensure_ascii=False)
            self.notify(
                f"💾 Game saved to {save_path}", severity="information", timeout=3
            )
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
        """Load game state from a JSON save file."""
        try:
            with open(save_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self.notify(f"Load failed: {e}", severity="error", timeout=3)
            return

        bus.emit("story_started", title=self.current_story_title)

        self.turn_count = data.get("turn_count", 1)
        self._current_story = data.get("current_story_text", LOADING_ART)
        self.inventory = data.get("inventory", [])
        self.player_stats = data.get(
            "player_stats", {"health": 100, "gold": 0, "reputation": 0}
        )
        self.current_story_title = data.get("story_title")
        self.current_scene_id = data.get("current_scene_id")
        self.last_choice_text = data.get("last_choice_text")
        self._last_raw_narrative = data.get("last_raw_narrative")
        self._undo_snapshot = None
        self._loading_suffix_shown = False

        node_data = data.get("current_node")
        if node_data:
            self.current_node = StoryNode(**node_data)
        else:
            self.current_node = None

        # Restore story context
        context_history = data.get("context_history", [])
        self.story_context = StoryContext(
            starting_prompt=data.get("starting_prompt", self.starting_prompt),
            token_budget=self.generator.token_budget if self.generator else DEFAULT_TOKEN_BUDGET,
            token_counter=self.generator.provider.count_tokens if self.generator else None,
        )
        self.story_context.history = context_history

        # Re-render the UI
        self.query_one("#story-text", Markdown).update(self._current_story)
        self._scroll_to_bottom()
        self.query_one("#choices-container").remove_children()
        self.query_one("#journal-list", ListView).clear()
        if self.current_node:
            self.display_node(self.current_node)
        self._update_status_bar()
        self.notify(
            f"📂 Loaded save from Turn {self.turn_count}.",
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

    @work(exclusive=True)
    async def generate_next_step(self, choice_text: Optional[str] = None) -> None:  # noqa: C901
        # RAG: retrieve relevant past scenes and inject as memory
        if (
            self._last_raw_narrative
            and self.story_context
            and self.generator
            and self.db
            and self.current_story_title
        ):
            memories = await self.memory.query_async(self._last_raw_narrative, n=3)

            # Inject NPC-specific memories based on previous scene's NPCs
            if self.current_node and getattr(self.current_node, "npcs_present", None):
                for npc in self.current_node.npcs_present:
                    npc_memories = await self.npc_memory.query_async(
                        npc, self._last_raw_narrative, n=2
                    )
                    for mem in npc_memories:
                        if mem not in memories:
                            memories.append(mem)

            self.story_context.inject_memory(memories)

            # ── Rolling Summarization ────────────────────────────────────────
            # When the context is ~80 % full, compress the oldest turn pairs
            # into a concise "Story So Far" paragraph so narrative momentum is
            # never lost when the sliding window trims old content.
            if self.story_context.needs_summarization():
                self.notify(
                    "📜 Archiving old chapters…",
                    severity="information",
                    timeout=4,
                )
                turns_to_compress = self.story_context.get_turns_for_summary()
                summary = await self.generator.generate_summary_async(turns_to_compress)
                if summary:
                    self.story_context.set_rolling_summary(summary)
            # ────────────────────────────────────────────────────────────────

            # Check speculation cache
            cached_node = None
            if choice_text:
                cached_node = self.speculation_cache.get_node(self.current_scene_id or "", choice_text)

            if cached_node:
                # Performance optimization: use the pre-calculated node
                node = cached_node
                # Simulated minimal stream to maintain UI feel
                self._stream_narrative("*(Recalling future memories...)* ")
                await asyncio.sleep(0.1)
            else:
                # Streaming: pass on_token callback so typewriter fires live
                def on_token(partial: str) -> None:
                    self._stream_narrative(partial)

                node = await self.generator.generate_next_node_async(
                    self.story_context, on_token_chunk=on_token
                )

            # Update KV cache state for the *current* scene (the one we just entered)
            # This allows instant rewinding back to this scene later.
            state = await self.generator.save_state_async()
            if state and self.current_scene_id:
                self.speculation_cache.set_state(self.current_scene_id, state)
            self._last_raw_narrative = node.narrative
            self.current_node = node  # Added

            for item in getattr(node, "items_gained", []):
                if item not in self.inventory:
                    self.inventory.append(item)
            for item in getattr(node, "items_lost", []):
                if item in self.inventory:
                    self.inventory.remove(item)

            for stat, change in getattr(node, "stat_updates", {}).items():
                self.player_stats[stat] = self.player_stats.get(stat, 0) + change

            # Fix #4: embed the scene in the RAG store from the worker thread,
            # not from display_node() on the UI thread.
            scene_id = self.current_scene_id or str(uuid.uuid4())
            await self.memory.add_async(scene_id, node.narrative)

            # Embed NPC-specific memory
            if getattr(node, "npcs_present", None):
                for npc in node.npcs_present:
                    await self.npc_memory.add_async(npc, scene_id, node.narrative)

            choices_text = [choice.text for choice in node.choices]
            prev_scene_id = self.current_scene_id
            prev_choice = self.last_choice_text

            new_id = await self.db.save_scene_async(
                narrative=node.narrative,
                available_choices=choices_text,
                story_title=self.current_story_title,
                source_scene_id=prev_scene_id,
                choice_text=prev_choice,
            )
            self.current_scene_id = new_id
            self.update_story_map()

            # Flush any remaining throttled stream chars before final render
            if self._stream_token_buffer > 0:
                self.query_one("#story-text", Markdown).update(
                    self._current_story
                )
            self.display_node(node)

    @work(exclusive=True)
    async def action_branch_past(self) -> None:
        if not self.db or not self.current_scene_id:
            return

        history = await asyncio.to_thread(
            self.db.get_scene_history_path, self.current_scene_id
        )
        if not history or not history.get("scenes"):
            return

        def check_branch(idx: int | None) -> None:
            if idx is not None:
                self.restore_to_scene(idx, history)

        self.push_screen(
            BranchScreen(history["scenes"], history["choices"]), check_branch
        )

    @work(exclusive=True)
    async def restore_to_scene(self, idx: int, history: dict[str, Any]) -> None:
        self.query_one("#choices-container").remove_children()
        self.query_one("#loading").remove_class("hidden")
        # Strip shifting text if present
        suffix = "\n\n*(The ancient texts are shifting...)*"
        if self._loading_suffix_shown and self._current_story.endswith(suffix):
            self._current_story = self._current_story[: -len(suffix)]
            self._loading_suffix_shown = False

        fracture_msg = (
            f"\n\n***\n\n**[Time fractures... you return to Turn {idx + 1}]**"
        )
        self._current_story += fracture_msg
        
        bus.emit("choice_made", choice_text=f"Time fracture back to Turn {idx + 1}")

        self.query_one("#story-text", Markdown).update(self._current_story)
        self._scroll_to_bottom()

        target_scene = history["scenes"][idx]

        self.story_context = StoryContext(starting_prompt=self.starting_prompt)
        for i in range(idx):
            self.story_context.add_turn(
                history["scenes"][i]["narrative"], history["choices"][i]
            )

        self.current_scene_id = target_scene["id"]
        self.last_choice_text = history["choices"][idx - 1] if idx > 0 else None
        self._last_raw_narrative = target_scene["narrative"]
        self.turn_count = idx + 1
        # TODO: A fully correct branch integration requires tracking `items` per-turn in Neo4j.
        # For now, we blank it gracefully on branch, requiring the player to re-find items or the LLM to hallucinate them back.
        self.inventory = []
        self.player_stats = {"health": 100, "gold": 0, "reputation": 0}
        self.memory = NarrativeMemory()
        self.npc_memory = NPCMemory()
        for i in range(idx + 1):
            past_scene_id = history["scenes"][i]["id"]
            past_narrative = history["scenes"][i]["narrative"]
            await self.memory.add_async(past_scene_id, past_narrative)

            if (
                "npcs_present" in history["scenes"][i]
                and history["scenes"][i]["npcs_present"]
            ):
                for npc in history["scenes"][i]["npcs_present"]:
                    await self.npc_memory.add_async(npc, past_scene_id, past_narrative)

        # ── KV-Cache optimization ───────────────────────────────────────────
        # Restore model state for this scene if we have a checkpoint cached
        if self.generator:
            state = self.speculation_cache.get_state(self.current_scene_id)
            if state:
                await self.generator.load_state_async(state)
        # ────────────────────────────────────────────────────────────────────

        journal_list = self.query_one("#journal-list", ListView)
        journal_list.clear()
        for i in range(idx):
            journal_list.append(
                ListItem(Label(f"Turn {i + 1}: {history['choices'][i]}"))
            )
        journal_list.scroll_end(animate=False)

        available = target_scene.get("available_choices") or []
        choices = [Choice(text=c) for c in available]
        node = StoryNode(
            narrative=target_scene["narrative"],
            choices=choices,
            is_ending=len(choices) == 0,
        )

        self.display_node(node)
        self.update_story_map()

    @work(exclusive=True)
    async def update_story_map(self) -> None:
        if not self.db or not self.current_story_title:
            return

        tree_data = await asyncio.to_thread(self.db.get_story_tree, self.current_story_title)
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
            if scene_id == self.current_scene_id:
                label = f"[b][green]> {preview}[/green][/b]"
            else:
                label = preview

            tree_node = parent_node.add(label, expand=True)

            for edge in edges.get(scene_id, []):
                choice_text = edge["choice"]
                choice_preview = (
                    choice_text[:constants.MAX_CHOICE_PREVIEW_LEN] + "..."
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
