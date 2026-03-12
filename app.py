from textual.app import App, ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import Header, Footer, Static, Markdown, Button, LoadingIndicator
from textual.reactive import reactive
from textual.worker import Worker, WorkerState
from textual import work

from models import StoryNode
from llm_backend import StoryGenerator, StoryContext
from graph_db import CYOAGraphDB

STARTING_PROMPT = """You are a dark fantasy text adventure game. 
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
    LOADING_ART = "# Welcome to the Adventure\n\n*Loading the AI model and generating the physical world... Please wait.*"

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
        self.db = None
        self.current_scene_id = None
        self.last_choice_text = None
        self.current_story_title = None
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
        # Hide choices initially
        self.query_one("#choices-container").border_title = "Choices"
        self.query_one("#story-container").border_title = "Story"
        
        # Start the LLM generation in a worker thread after the UI has successfully painted the ASCII art.
        self.set_timer(0.1, lambda: self.initialize_and_start(self.model_path))
        
    @work(exclusive=True, thread=True)
    def initialize_and_start(self, model_path: str):
        # Initialized here to avoid showing Llama.cpp loading screen
        self.generator = StoryGenerator(model_path=model_path)
        self.story_context = StoryContext(starting_prompt=STARTING_PROMPT)
        
        with open("story.md", "w", encoding="utf-8") as f:
            f.write("# New Adventure\n\n")
            
        # Tell UI to show the loading screen FIRST, before we block on Neo4j/LLM
        self.call_from_thread(self.show_loading)

        try:
            self.db = CYOAGraphDB()
        except Exception as e:
            print(f"Warning: Could not connect to Neo4j Graph DB: {e}")
            self.db = None
            
        # Generate first node
        node = self.generator.generate_next_node(self.story_context)
        
        if self.db:
            # We asked the LLM for a title on the very first node
            generated_title = node.title if node.title else "Untitled Adventure"
            self.current_story_title = self.db.create_story_node_and_get_title(generated_title)
            
            choices_text = [choice.text for choice in node.choices]
            self.current_scene_id = self.db.create_scene_node(node.narrative, choices_text, self.current_story_title)
            
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
            
            # Auto-scroll so the loading indicator text and choice is visible
            story_container = self.query_one("#story-container")
            self.set_timer(0.05, lambda: story_container.scroll_end(animate=False))

    def display_node(self, node: StoryNode):
        loading = self.query_one("#loading")
        loading.add_class("hidden")
        
        story_md = self.query_one("#story-text", Markdown)
        
        # If the current story is just the loading art, replace it; otherwise, append.
        if self._current_story == LOADING_ART:
            self._current_story = node.narrative
        else:
            # We already appended the user's choice and the "shifting texts" 
            # message, so we just clean that up and properly append the story.
            self._current_story = self._current_story.replace("\n\n*(The ancient texts are shifting...)*", "")
            self._current_story += f"\n\n---\n\n{node.narrative}"
            
        story_md.update(self._current_story)
        
        # Auto-scroll to the bottom so the new text and choices are visible
        # We use a short timer to let Textual recalculate layout heights after the markdown update
        story_container = self.query_one("#story-container")
        self.set_timer(0.05, lambda: story_container.scroll_end(animate=False))
        
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
        
        self.last_choice_text = choice_text
        
        # Add the turn to our context
        self.story_context.add_turn(self._current_story, choice_text)
        
        # Append user choice to the file
        with open("story.md", "a", encoding="utf-8") as f:
            f.write(f"> **You chose:** {choice_text}\n\n---\n\n")
        
        # Show the choice in the UI and trigger loading
        self._current_story += f"\n\n> **You chose:** {choice_text}"
        self.show_loading()
        self.generate_next_step()

    @work(exclusive=True, thread=True)
    def generate_next_step(self):
        node = self.generator.generate_next_node(self.story_context)
        
        if self.db and self.current_story_title:
            choices_text = [choice.text for choice in node.choices]
            new_scene_id = self.db.create_scene_node(node.narrative, choices_text, self.current_story_title)
            if self.current_scene_id and self.last_choice_text:
                self.db.create_choice_edge(self.current_scene_id, new_scene_id, self.last_choice_text)
            self.current_scene_id = new_scene_id
            
        self.call_from_thread(self.display_node, node)
