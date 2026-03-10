from textual.app import App, ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import Header, Footer, Static, Markdown, Button, LoadingIndicator
from textual.reactive import reactive
from textual.worker import Worker, WorkerState
from textual import work

from models import StoryNode
from llm_backend import StoryGenerator, StoryContext

STARTING_PROMPT = """You are a dark fantasy text adventure game. 
Describe the starting scenario where the player wakes up in a cold, unfamiliar dungeon cell. 
Provide 2-3 choices for what they can do next. 
Ensure your output is strictly valid JSON matching the requested schema.
"""

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

    def __init__(self, model_path: str, **kwargs):
        super().__init__(**kwargs)
        self.model_path = model_path
        self.generator = None
        self.story_context = None
        self._current_story = "# Welcome to the Adventure\nLoading the model and generating the intro..."

    def compose(self) -> ComposeResult:
        yield Header()
        with Container():
            with VerticalScroll(id="story-container"):
                yield Markdown("# Welcome to the Adventure\nLoading the model and generating the intro...", id="story-text")
            with Container(id="choices-container"):
                yield LoadingIndicator(id="loading")
        yield Footer()

    async def on_mount(self) -> None:
        # Hide choices initially
        self.query_one("#choices-container").border_title = "Choices"
        self.query_one("#story-container").border_title = "Story"
        
        # Start the LLM generation in a worker thread
        self.initialize_and_start(self.model_path)
        
    @work(exclusive=True, thread=True)
    def initialize_and_start(self, model_path: str):
        # Initialized here to avoid showing Llama.cpp loading screen
        self.generator = StoryGenerator(model_path=model_path)
        self.story_context = StoryContext(starting_prompt=STARTING_PROMPT)
        
        with open("story.md", "w", encoding="utf-8") as f:
            f.write("# New Adventure\n\n")
            
        # Generate first node
        self.call_from_thread(self.show_loading)
        node = self.generator.generate_next_node(self.story_context)
        self.call_from_thread(self.display_node, node)

    def show_loading(self):
        container = self.query_one("#choices-container")
        # remove all existing buttons
        for btn in container.query(Button):
            btn.remove()
        
        loading = self.query_one("#loading")
        loading.remove_class("hidden")
        
        # update story text to indicate loading
        story_md = self.query_one("#story-text", Markdown)
        if "*(The ancient texts are shifting...)*" not in self._current_story:
            self._current_story += "\n\n*(The ancient texts are shifting...)*"
            story_md.update(self._current_story)

    def display_node(self, node: StoryNode):
        loading = self.query_one("#loading")
        loading.add_class("hidden")
        
        story_md = self.query_one("#story-text", Markdown)
        # Replacing it feels more like a paginated CYOA, let's just replace it.
        self._current_story = node.narrative
        story_md.update(self._current_story)
        
        # Append latest narrative text to the file
        with open("story.md", "a", encoding="utf-8") as f:
            f.write(f"{self._current_story}\n\n")
        
        choices_container = self.query_one("#choices-container")
        
        # Add new buttons for choices
        for idx, choice in enumerate(node.choices):
            btn = Button(str(choice.text), id=f"choice-{idx}", variant="primary")
            # store the actual text so we can use it in the prompt
            btn.action_text = choice.text
            choices_container.mount(btn)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        choice_text = getattr(event.button, 'action_text', str(event.button.label))
        
        # Add the turn to our context
        self.story_context.add_turn(self._current_story, choice_text)
        
        # Append user choice to the file
        with open("story.md", "a", encoding="utf-8") as f:
            f.write(f"> **You chose:** {choice_text}\n\n---\n\n")
        
        # Show loading and start generation worker
        self.show_loading()
        self.generate_next_step()

    @work(exclusive=True, thread=True)
    def generate_next_step(self):
        node = self.generator.generate_next_node(self.story_context)
        self.call_from_thread(self.display_node, node)
