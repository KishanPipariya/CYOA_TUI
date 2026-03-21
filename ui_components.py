from typing import Any
from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Button, ListView, ListItem, Label, Static
from textual.screen import ModalScreen

__all__ = ["BranchScreen", "ThemeSpinner"]

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
