import uuid
import json
import os
from textual.app import App, ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import Header, Footer, Markdown, Button, LoadingIndicator, ListView, ListItem, Label, Static
from textual.screen import ModalScreen
from textual.reactive import reactive
from textual import work
from typing import Any, Optional

from models import StoryNode, Choice
from llm_backend import StoryGenerator, StoryContext
from graph_db import CYOAGraphDB
from rag_memory import NarrativeMemory

__all__ = ["CYOAApp", "DEFAULT_STARTING_PROMPT"]

# Fix #1: Only re-render Markdown every N streamed characters to avoid
# re-parsing the full story string on every single token.
_STREAM_RENDER_THROTTLE = 8

DEFAULT_STARTING_PROMPT = """You are a dark fantasy interactive fiction engine.
Describe the starting scenario where the player wakes up in a cold, unfamiliar dungeon cell.
Provide 2-3 choices for what they can do next.
You MUST provide a creative 'title' for this new adventure in the JSON response.
When the story reaches a definitive conclusion (victory, death, escape, etc), set 'is_ending' to true and provide an empty choices list.
Ensure your output is strictly valid JSON matching the requested schema.
"""

CONFIG_FILE = ".config.json"

# Fix #9: Persist dark mode preference
def _load_config() -> dict[str, Any]:
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_config(data: dict[str, Any]) -> None:
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f)

# Load the ASCII art for the initial screen
try:
    with open("loading_art.md", "r", encoding="utf-8") as f:
        LOADING_ART = f.read()
except FileNotFoundError:
    LOADING_ART = "# Welcome to the Adventure\n\n*Loading the AI model... Please wait.*"


class BranchScreen(ModalScreen[int]):
    """Screen to select a past scene to branch from."""
    
    DEFAULT_CSS = """
    BranchScreen {
        align: center middle;
        background: $background 80%;
    }
    #branch-dialog {
        width: 80%;
        height: 80%;
        border: thick $primary;
        background: $surface;
        padding: 1;
    }
    #branch-list {
        height: 1fr;
        border: solid $secondary;
        margin-bottom: 1;
    }
    .scene-preview {
        padding: 1;
    }
    """
    
    def __init__(self, scenes: list[dict[str, Any]], choices: list[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.scenes = scenes
        self.choices = choices
        
    def compose(self) -> ComposeResult:
        with Container(id="branch-dialog"):
            yield Label("[b]Rewind & Branch:[/b] Select a past moment to alter your fate.", id="branch-title")
            list_view = ListView(id="branch-list")
            yield list_view
            yield Button("Cancel", id="cancel-branch", variant="error")

    def on_mount(self) -> None:
        list_view = self.query_one("#branch-list", ListView)
        for i, scene in enumerate(self.scenes):
            preview = scene["narrative"][:100].replace("\n", " ") + "..."
            choice_text = self.choices[i] if i < len(self.choices) else "Current Scene"
            label_text = f"Turn {i+1}: {preview}\n[i]Choice made: {choice_text}[/i]"
            item = ListItem(Label(label_text, classes="scene-preview"))
            item.scene_index = i
            list_view.append(item)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = getattr(event.item, "scene_index", None)
        if idx is not None:
            self.dismiss(idx)
            
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-branch":
            self.dismiss(None)


class ThemeSpinner(Static):
    """Custom spinner that cycles through configured ASCII frames."""
    def __init__(self, frames: list[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.frames = frames
        self._frame_idx = 0
        
    def on_mount(self) -> None:
        self.update(self.frames[0])
        self.set_interval(0.5, self.tick)
        
    def tick(self) -> None:
        self._frame_idx = (self._frame_idx + 1) % len(self.frames)
        self.update(self.frames[self._frame_idx])


class CYOAApp(App):
    """A Choose-Your-Adventure Textual App."""

    # Fix #8: CSS loaded from external file
    CSS_PATH = "styles.tcss"

    # Fix #1: Number key bindings to select choices
    BINDINGS = [
        ("d", "toggle_dark", "Toggle dark mode"),
        ("b", "branch_past", "Branch from Past"),
        ("q", "quit", "Quit"),
        ("r", "restart", "Restart"),
        ("1", "choose('1')", "Choice 1"),
        ("2", "choose('2')", "Choice 2"),
        ("3", "choose('3')", "Choice 3"),
        ("4", "choose('4')", "Choice 4"),
    ]

    # Fix #4: Reactive turn counter displayed in footer
    turn_count: reactive[int] = reactive(1)

    def __init__(self, model_path: str, starting_prompt: str = DEFAULT_STARTING_PROMPT, spinner_frames: Optional[list[str]] = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.model_path = model_path
        self.starting_prompt = starting_prompt
        self.spinner_frames = spinner_frames or ["[-]", "[\\]", "[|]", "[/]"]

        self.generator: Optional[StoryGenerator] = None
        self.story_context: Optional[StoryContext] = None
        self.db: Optional[CYOAGraphDB] = None
        self.current_scene_id: Optional[str] = None
        self.last_choice_text: Optional[str] = None
        self.current_story_title: Optional[str] = None
        self._last_raw_narrative: str | None = None
        self._loading_suffix_shown: bool = False
        self._current_story: str = LOADING_ART
        self._story_file: Optional[Any] = None
        # Fix #1: token accumulator for throttled streaming re-renders
        self._stream_token_buffer: int = 0
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
                yield ThemeSpinner(frames=self.spinner_frames, id="loading")
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
    def initialize_and_start(self, model_path: str) -> None:
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
        
        def on_complete(sid: str) -> None:
            self.current_scene_id = sid
            
        self.db.save_scene_async(
            narrative=node.narrative,
            available_choices=choices_text,
            story_title=self.current_story_title,
            source_scene_id=None,
            choice_text=None,
            on_complete=on_complete
        )

        self.call_from_thread(self.display_node, node)

    def _stream_narrative(self, partial: str) -> None:
        """
        Streaming callback called via call_from_thread for each batch of chars.
        Fix #1: throttles Markdown re-renders to every _STREAM_RENDER_THROTTLE
        characters so the full story string is not re-parsed on every token.
        """
        story_md = self.query_one("#story-text", Markdown)

        if self._loading_suffix_shown:
            # First token batch arrived — strip the loading placeholder
            suffix = "\n\n*(The ancient texts are shifting...)*"
            if self._current_story.endswith(suffix):
                self._current_story = self._current_story[: -len(suffix)]
            self._loading_suffix_shown = False
            self.query_one("#loading").add_class("hidden")
            if self._current_story == LOADING_ART:
                self._current_story = partial
            else:
                self._current_story += f"\n\n---\n\n{partial}"
            # Always render immediately on first token so text appears
            self._stream_token_buffer = 0
            story_md.update(self._current_story)
        else:
            self._current_story += partial
            self._stream_token_buffer += len(partial)
            # Fix #1: throttle — only re-render when buffer threshold is reached
            if self._stream_token_buffer >= _STREAM_RENDER_THROTTLE:
                self._stream_token_buffer = 0
                story_md.update(self._current_story)

    def show_loading(self) -> None:
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

    def display_node(self, node: StoryNode) -> None:
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

        # Fix #4: memory.add() moved to worker thread (generate_next_step)
        # so chromadb embedding does not block the UI event loop here.

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

    def _trigger_choice(self, choice_text: str) -> None:
        """Shared logic for both click and keyboard choice selection."""
        self.last_choice_text = choice_text
        if self.story_context:
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
        self.turn_count = 1
        self.current_scene_id = None
        self.last_choice_text = None
        self._last_raw_narrative = None
        self._stream_token_buffer = 0
        # Fix #8: reset memory so the new adventure doesn't inherit old scene embeddings
        self.memory = NarrativeMemory()

        self.query_one("#story-text", Markdown).update(LOADING_ART)
        # Fix #3: use remove_children() instead of query+remove loop
        self.query_one("#choices-container").remove_children()

        self.set_timer(0.1, lambda: self.initialize_and_start(self.model_path))

    # Fix #9: Persist dark mode preference when toggled
    def action_toggle_dark(self) -> None:
        self.dark = not self.dark
        _save_config({"dark": self.dark})

    @work(exclusive=True, thread=True)
    def generate_next_step(self) -> None:
        # RAG: retrieve relevant past scenes and inject as memory
        if self._last_raw_narrative and self.story_context and self.generator and self.db and self.current_story_title:
            memories = self.memory.query(self._last_raw_narrative, n=3)
            self.story_context.inject_memory(memories)

            # Streaming: pass on_token callback so typewriter fires live
            def on_token(partial: str) -> None:
                self.call_from_thread(self._stream_narrative, partial)

            node = self.generator.generate_next_node(self.story_context, on_token=on_token)
            self._last_raw_narrative = node.narrative

            # Fix #4: embed the scene in the RAG store from the worker thread,
            # not from display_node() on the UI thread.
            scene_id = self.current_scene_id or str(uuid.uuid4())
            self.memory.add(scene_id, node.narrative)

            choices_text = [choice.text for choice in node.choices]
            prev_scene_id = self.current_scene_id
            prev_choice = self.last_choice_text

            def on_complete(sid: str) -> None:
                self.current_scene_id = sid

            self.db.save_scene_async(
                narrative=node.narrative,
                available_choices=choices_text,
                story_title=self.current_story_title,
                source_scene_id=prev_scene_id,
                choice_text=prev_choice,
                on_complete=on_complete
            )

            # Flush any remaining throttled stream chars before final render
            if self._stream_token_buffer > 0:
                self.call_from_thread(
                    lambda: self.query_one("#story-text", Markdown).update(self._current_story)
                )
            self.call_from_thread(self.display_node, node)

    @work(exclusive=True, thread=True)
    def action_branch_past(self) -> None:
        if not self.db or not self.current_scene_id:
            return
            
        history = self.db.get_scene_history_path(self.current_scene_id)
        if not history or not history.get("scenes"):
            return
            
        def show_branch_screen():
            def check_branch(idx: int | None) -> None:
                if idx is not None:
                    self.restore_to_scene(idx, history)
            self.push_screen(BranchScreen(history["scenes"], history["choices"]), check_branch)
            
        self.call_from_thread(show_branch_screen)

    @work(exclusive=True, thread=True)
    def restore_to_scene(self, idx: int, history: dict[str, Any]) -> None:
        def pre_update() -> None:
            self.query_one("#choices-container").remove_children()
            self.query_one("#loading").remove_class("hidden")
            # Strip shifting text if present
            suffix = "\n\n*(The ancient texts are shifting...)*"
            if self._loading_suffix_shown and self._current_story.endswith(suffix):
                self._current_story = self._current_story[: -len(suffix)]
                self._loading_suffix_shown = False

            fracture_msg = f"\n\n***\n\n**[Time fractures... you return to Turn {idx + 1}]**"
            self._current_story += fracture_msg
            if self._story_file:
                self._story_file.write(f"{fracture_msg}\n\n")
                self._story_file.flush()
                
            self.query_one("#story-text", Markdown).update(self._current_story)
            story_container = self.query_one("#story-container")
            self.set_timer(0.05, lambda: story_container.scroll_end(animate=False))
                
        self.call_from_thread(pre_update)
        
        target_scene = history["scenes"][idx]
        
        self.story_context = StoryContext(starting_prompt=self.starting_prompt)
        for i in range(idx):
            self.story_context.add_turn(history["scenes"][i]["narrative"], history["choices"][i])
            
        self.current_scene_id = target_scene["id"]
        self.last_choice_text = history["choices"][idx-1] if idx > 0 else None
        self._last_raw_narrative = target_scene["narrative"]
        self.turn_count = idx + 1
        
        self.memory = NarrativeMemory()
        for i in range(idx + 1):
            self.memory.add(history["scenes"][i]["id"], history["scenes"][i]["narrative"])
            
        available = target_scene.get("available_choices") or []
        choices = [Choice(text=c) for c in available]
        node = StoryNode(
            narrative=target_scene["narrative"],
            choices=choices,
            is_ending=len(choices) == 0
        )
        
        self.call_from_thread(self.display_node, node)

