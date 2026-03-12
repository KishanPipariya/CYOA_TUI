import uuid
import json
import os
from textual.app import App, ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import Header, Footer, Markdown, Button, LoadingIndicator
from textual.reactive import reactive
from textual import work

from models import StoryNode
from llm_backend import StoryGenerator, StoryContext
from graph_db import CYOAGraphDB
from rag_memory import NarrativeMemory

DEFAULT_STARTING_PROMPT = """You are a dark fantasy interactive fiction engine.
Describe the starting scenario where the player wakes up in a cold, unfamiliar dungeon cell.
Provide 2-3 choices for what they can do next.
You MUST provide a creative 'title' for this new adventure in the JSON response.
When the story reaches a definitive conclusion (victory, death, escape, etc), set 'is_ending' to true and provide an empty choices list.
Ensure your output is strictly valid JSON matching the requested schema.
"""

CONFIG_FILE = ".config.json"

# Fix #9: Persist dark mode preference
def _load_config() -> dict:
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_config(data: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f)

# Load the ASCII art for the initial screen
try:
    with open("loading_art.md", "r", encoding="utf-8") as f:
        LOADING_ART = f.read()
except FileNotFoundError:
    LOADING_ART = "# Welcome to the Adventure\n\n*Loading the AI model... Please wait.*"


class CYOAApp(App):
    """A Choose-Your-Own-Adventure Textual App."""

    # Fix #8: CSS loaded from external file
    CSS_PATH = "styles.tcss"

    # Fix #1: Number key bindings to select choices
    BINDINGS = [
        ("d", "toggle_dark", "Toggle dark mode"),
        ("q", "quit", "Quit"),
        ("r", "restart", "Restart"),
        ("1", "choose('1')", "Choice 1"),
        ("2", "choose('2')", "Choice 2"),
        ("3", "choose('3')", "Choice 3"),
        ("4", "choose('4')", "Choice 4"),
    ]

    # Fix #4: Reactive turn counter displayed in footer
    turn_count: reactive[int] = reactive(1)

    def __init__(self, model_path: str, starting_prompt: str = DEFAULT_STARTING_PROMPT, **kwargs):
        super().__init__(**kwargs)
        self.model_path = model_path
        self.starting_prompt = starting_prompt

        self.generator = None
        self.story_context = None
        self.db = None
        self.current_scene_id = None
        self.last_choice_text = None
        self.current_story_title = None
        self._last_raw_narrative: str | None = None
        self._loading_suffix_shown: bool = False
        self._current_story = LOADING_ART
        self._story_file = None
        # RAG: in-memory semantic scene store
        self.memory = NarrativeMemory()

        # Fix #9: Restore dark mode preference
        config = _load_config()
        self.dark = config.get("dark", True)

    def compose(self) -> ComposeResult:
        yield Header()
        with Container():
            with VerticalScroll(id="story-container"):
                yield Markdown(LOADING_ART, id="story-text")
            # Fix #5: Dedicated status bar between story and choices
            with Container(id="status-bar"):
                yield LoadingIndicator(id="loading")
            with Container(id="choices-container"):
                pass
        yield Footer()

    def watch_turn_count(self, count: int) -> None:
        # Fix #4: Update footer subtitle with turn counter
        self.sub_title = f"Turn {count}" if count > 0 else ""

    async def on_mount(self) -> None:
        self.query_one("#choices-container").border_title = "Choices"
        self.query_one("#story-container").border_title = "Story"
        # Fix #6: Show spinner immediately before model even begins loading
        self.query_one("#loading").remove_class("hidden")
        # Short delay to let the UI paint the ASCII art + spinner before blocking
        self.set_timer(0.1, lambda: self.initialize_and_start(self.model_path))

    @work(exclusive=True, thread=True)
    def initialize_and_start(self, model_path: str):
        """Load model and generate the first scene. Reuses existing model if already loaded."""
        if self.generator is None:
            self.generator = StoryGenerator(model_path=model_path)

        self.story_context = StoryContext(starting_prompt=self.starting_prompt)
        self.call_from_thread(self.show_loading)

        if self.db is None:
            self.db = CYOAGraphDB()

        node = self.generator.generate_next_node(self.story_context)
        self._last_raw_narrative = node.narrative

        generated_title = node.title if node.title else "Untitled Adventure"
        self.current_story_title = self.db.create_story_node_and_get_title(generated_title)

        with open("story.md", "w", encoding="utf-8") as f:
            f.write(f"# {self.current_story_title}\n\n")
        # Perf #3: Open the story log once and keep it open for the session
        if self._story_file:
            self._story_file.close()
        self._story_file = open("story.md", "a", encoding="utf-8")

        choices_text = [choice.text for choice in node.choices]
        self.db.save_scene_async(
            narrative=node.narrative,
            available_choices=choices_text,
            story_title=self.current_story_title,
            source_scene_id=None,
            choice_text=None,
            on_complete=lambda sid: setattr(self, "current_scene_id", sid)
        )

        self.call_from_thread(self.display_node, node)

    def _stream_narrative(self, partial: str) -> None:
        """
        Streaming callback: called from the worker thread via call_from_thread.
        Appends each new character to the live Markdown widget (typewriter effect).
        On the very first token, hides the spinner and removes the 'shifting' placeholder.
        """
        story_md = self.query_one("#story-text", Markdown)

        if self._loading_suffix_shown:
            # First token arrived — strip the loading placeholder
            suffix = "\n\n*(The ancient texts are shifting...)*"
            if self._current_story.endswith(suffix):
                self._current_story = self._current_story[: -len(suffix)]
            self._loading_suffix_shown = False
            # Hide spinner and start a fresh narrative section
            self.query_one("#loading").add_class("hidden")
            if self._current_story == LOADING_ART:
                self._current_story = partial
            else:
                self._current_story += f"\n\n---\n\n{partial}"
        else:
            # Subsequent tokens: just append the new character(s)
            self._current_story += partial

        story_md.update(self._current_story)

    def show_loading(self):
        """Clear choice buttons, show spinner, append 'shifting' text."""
        # Perf #6: remove_children() is O(1) vs query(Button) DOM traversal
        self.query_one("#choices-container").remove_children()
        self.query_one("#loading").remove_class("hidden")

        if not self._loading_suffix_shown:
            # Perf #2: append suffix and set flag — avoids str.replace later
            self._current_story += "\n\n*(The ancient texts are shifting...)*"
            self._loading_suffix_shown = True
            self.query_one("#story-text", Markdown).update(self._current_story)
            story_container = self.query_one("#story-container")
            self.set_timer(0.05, lambda: story_container.scroll_end(animate=False))

    def display_node(self, node: StoryNode):
        """Render a newly generated StoryNode to the UI (after streaming completes)."""
        self.query_one("#loading").add_class("hidden")

        story_md = self.query_one("#story-text", Markdown)

        # If streaming happened, _stream_narrative already updated the story;
        # only do a full replace if we somehow ended up in non-streaming mode.
        if self._current_story == LOADING_ART:
            self._current_story = node.narrative
        elif self._loading_suffix_shown:
            # No streaming happened (fallback) — strip suffix and append
            suffix = "\n\n*(The ancient texts are shifting...)*"
            if self._current_story.endswith(suffix):
                self._current_story = self._current_story[: -len(suffix)]
            self._current_story += f"\n\n---\n\n{node.narrative}"
        # else: streaming already updated _current_story incrementally
        self._loading_suffix_shown = False

        story_md.update(self._current_story)

        story_container = self.query_one("#story-container")
        self.set_timer(0.05, lambda: story_container.scroll_end(animate=False))

        if self._story_file:
            self._story_file.write(f"{node.narrative}\n\n")
            self._story_file.flush()

        # RAG: store this scene in memory for future retrieval
        scene_id = self.current_scene_id or str(uuid.uuid4())
        self.memory.add(scene_id, node.narrative)

        choices_container = self.query_one("#choices-container")

        if node.is_ending:
            end_btn = Button("✦ Start a New Adventure", id="btn-new-adventure", variant="success")
            choices_container.mount(end_btn)
            return

        for choice in node.choices:
            btn_id = f"choice-{uuid.uuid4().hex[:8]}"
            btn = Button(str(choice.text), id=btn_id, variant="primary")
            btn.action_text = choice.text
            choices_container.mount(btn)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        # Fix #7: Handle the end-game "New Adventure" button
        if event.button.id == "btn-new-adventure":
            await self.action_restart()
            return

        choice_text = getattr(event.button, "action_text", str(event.button.label))
        self._trigger_choice(choice_text)

    # Fix #1: Keyboard number shortcut to select a choice
    def action_choose(self, number: str) -> None:
        """Select a choice by its 1-based index using number keys."""
        buttons = list(self.query_one("#choices-container").query(Button))
        idx = int(number) - 1
        if 0 <= idx < len(buttons):
            btn = buttons[idx]
            choice_text = getattr(btn, "action_text", str(btn.label))
            self._trigger_choice(choice_text)

    def _trigger_choice(self, choice_text: str):
        """Shared logic for both click and keyboard choice selection."""
        self.last_choice_text = choice_text
        self.story_context.add_turn(self._last_raw_narrative or "", choice_text)
        self.turn_count += 1  # Fix #4

        # Perf #3: write choice to persistent file handle
        if self._story_file:
            self._story_file.write(f"> **You chose:** {choice_text}\n\n---\n\n")
            self._story_file.flush()

        self._current_story += f"\n\n> **You chose:** {choice_text}"
        self.show_loading()
        self.generate_next_step()

    # Fix #2: In-app restart without reloading the model
    async def action_restart(self) -> None:
        """Reset story state and start a new adventure without reloading the model."""
        self._current_story = LOADING_ART
        self.turn_count = 0
        self.current_scene_id = None
        self.last_choice_text = None
        self._last_raw_narrative = None

        # Reset UI to loading state
        self.query_one("#story-text", Markdown).update(LOADING_ART)
        for btn in self.query_one("#choices-container").query(Button):
            btn.remove()

        self.set_timer(0.1, lambda: self.initialize_and_start(self.model_path))

    # Fix #9: Persist dark mode preference when toggled
    def action_toggle_dark(self) -> None:
        self.dark = not self.dark
        _save_config({"dark": self.dark})

    @work(exclusive=True, thread=True)
    def generate_next_step(self):
        # RAG: retrieve relevant past scenes and inject as memory
        if self._last_raw_narrative:
            memories = self.memory.query(self._last_raw_narrative, n=3)
            self.story_context.inject_memory(memories)

        # Streaming: pass on_token callback so typewriter fires live
        def on_token(partial: str):
            self.call_from_thread(self._stream_narrative, partial)

        node = self.generator.generate_next_node(self.story_context, on_token=on_token)
        self._last_raw_narrative = node.narrative

        choices_text = [choice.text for choice in node.choices]
        prev_scene_id = self.current_scene_id
        prev_choice = self.last_choice_text

        self.db.save_scene_async(
            narrative=node.narrative,
            available_choices=choices_text,
            story_title=self.current_story_title,
            source_scene_id=prev_scene_id,
            choice_text=prev_choice,
            on_complete=lambda sid: setattr(self, "current_scene_id", sid)
        )

        self.call_from_thread(self.display_node, node)
