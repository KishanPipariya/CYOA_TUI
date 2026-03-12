import uuid
from textual.app import App, ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import Header, Footer, Markdown, Button, LoadingIndicator
from textual import work

from models import StoryNode
from llm_backend import StoryGenerator, StoryContext
from graph_db import CYOAGraphDB

DEFAULT_STARTING_PROMPT = """You are a dark fantasy text adventure game.
Describe the starting scenario where the player wakes up in a cold, unfamiliar dungeon cell.
Provide 2-3 choices for what they can do next.
Also, you MUST provide a creative 'title' for this new adventure in the JSON response.
Ensure your output is strictly valid JSON matching the requested schema.
"""

# Load the ASCII art for the initial screen
try:
    with open("loading_art.md", "r", encoding="utf-8") as f:
        LOADING_ART = f.read()
except FileNotFoundError:
    LOADING_ART = "# Welcome to the Adventure\n\n*Loading the AI model... Please wait.*"


class CYOAApp(App):
    """A Choose-Your-Own-Adventure Textual App."""

    CSS = """
    Screen {
        background: $surface;
    }

    #story-container {
        height: 1fr;
        border: solid $accent;
        padding: 1 2;
        margin: 1;
        background: $boost;
    }

    #choices-container {
        height: auto;
        border: solid $secondary;
        padding: 1;
        margin: 0 1 1 1;
    }

    Button {
        width: 100%;
        margin-bottom: 1;
    }

    .hidden {
        display: none;
    }
    """

    BINDINGS = [("d", "toggle_dark", "Toggle dark mode"), ("q", "quit", "Quit")]

    def __init__(self, model_path: str, starting_prompt: str = DEFAULT_STARTING_PROMPT, **kwargs):
        super().__init__(**kwargs)
        self.model_path = model_path
        # Fix #6: starting_prompt is now a constructor parameter with a sensible default
        self.starting_prompt = starting_prompt

        self.generator = None
        self.story_context = None
        self.db = None
        self.current_scene_id = None
        self.last_choice_text = None
        self.current_story_title = None

        # Fix #2 companion: store the raw narrative of the *last* node separately
        # so we can pass it cleanly to add_turn() rather than the accumulated markdown
        self._last_raw_narrative: str | None = None

        # The accumulated markdown shown in the UI (display-only)
        self._current_story = LOADING_ART

    def compose(self) -> ComposeResult:
        yield Header()
        with Container():
            with VerticalScroll(id="story-container"):
                yield Markdown(LOADING_ART, id="story-text")
            with Container(id="choices-container"):
                yield LoadingIndicator(id="loading")
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#choices-container").border_title = "Choices"
        self.query_one("#story-container").border_title = "Story"
        # Delay startup so UI finishes painting ASCII art before blocking on LLM load
        self.set_timer(0.1, lambda: self.initialize_and_start(self.model_path))

    @work(exclusive=True, thread=True)
    def initialize_and_start(self, model_path: str):
        self.generator = StoryGenerator(model_path=model_path)
        self.story_context = StoryContext(starting_prompt=self.starting_prompt)

        self.call_from_thread(self.show_loading)

        # Initialize GraphDB (silently disables itself if offline)
        self.db = CYOAGraphDB()

        # Generate first node
        node = self.generator.generate_next_node(self.story_context)
        self._last_raw_narrative = node.narrative

        # Register the story in the graph
        generated_title = node.title if node.title else "Untitled Adventure"
        self.current_story_title = self.db.create_story_node_and_get_title(generated_title)

        # Fix #3: story.md now written fresh using the actual LLM-generated title
        with open("story.md", "w", encoding="utf-8") as f:
            f.write(f"# {self.current_story_title}\n\n")

        # Fix #5: DB write is async; UI will update immediately via call_from_thread
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

    def show_loading(self):
        container = self.query_one("#choices-container")
        for btn in container.query(Button):
            btn.remove()

        self.query_one("#loading").remove_class("hidden")

        if "*(The ancient texts are shifting...)*" not in self._current_story:
            self._current_story += "\n\n*(The ancient texts are shifting...)*"
            self.query_one("#story-text", Markdown).update(self._current_story)
            story_container = self.query_one("#story-container")
            self.set_timer(0.05, lambda: story_container.scroll_end(animate=False))

    def display_node(self, node: StoryNode):
        self.query_one("#loading").add_class("hidden")

        story_md = self.query_one("#story-text", Markdown)

        # Strip the "shifting" loading placeholder before appending the new scene
        if self._current_story == LOADING_ART:
            self._current_story = node.narrative
        else:
            self._current_story = self._current_story.replace(
                "\n\n*(The ancient texts are shifting...)*", ""
            )
            self._current_story += f"\n\n---\n\n{node.narrative}"

        story_md.update(self._current_story)

        # Auto-scroll after layout recalculates
        story_container = self.query_one("#story-container")
        self.set_timer(0.05, lambda: story_container.scroll_end(animate=False))

        # Fix #3: Only write the delta (new narrative) to story.md, not the entire history
        with open("story.md", "a", encoding="utf-8") as f:
            f.write(f"{node.narrative}\n\n")

        choices_container = self.query_one("#choices-container")
        for choice in node.choices:
            # Fix #7: Use a unique short UUID so IDs never collide across turns
            btn_id = f"choice-{uuid.uuid4().hex[:8]}"
            btn = Button(str(choice.text), id=btn_id, variant="primary")
            btn.action_text = choice.text
            choices_container.mount(btn)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        choice_text = getattr(event.button, "action_text", str(event.button.label))

        self.last_choice_text = choice_text

        # Fix #2 companion: pass only the raw narrative, not the accumulated markdown
        self.story_context.add_turn(self._last_raw_narrative or "", choice_text)

        # Append choice to story log
        with open("story.md", "a", encoding="utf-8") as f:
            f.write(f"> **You chose:** {choice_text}\n\n---\n\n")

        # Show choice in UI then start generation
        self._current_story += f"\n\n> **You chose:** {choice_text}"
        self.show_loading()
        self.generate_next_step()

    @work(exclusive=True, thread=True)
    def generate_next_step(self):
        node = self.generator.generate_next_node(self.story_context)
        self._last_raw_narrative = node.narrative

        choices_text = [choice.text for choice in node.choices]

        # Capture scene ID at point-of-write since async completion updates it after
        prev_scene_id = self.current_scene_id
        prev_choice = self.last_choice_text

        # Fix #5: async, non-blocking DB save — UI updates immediately
        self.db.save_scene_async(
            narrative=node.narrative,
            available_choices=choices_text,
            story_title=self.current_story_title,
            source_scene_id=prev_scene_id,
            choice_text=prev_choice,
            on_complete=lambda sid: setattr(self, "current_scene_id", sid)
        )

        self.call_from_thread(self.display_node, node)
