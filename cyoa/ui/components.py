from typing import Any

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.markup import escape
from textual.screen import ModalScreen
from textual.widgets import Button, Label, ListItem, ListView, Markdown, Static

__all__ = [
    "BranchScreen",
    "ThemeSpinner",
    "ConfirmScreen",
    "HelpScreen",
    "LoadGameScreen",
    "SceneListItem",
    "SaveListItem",
]


class SceneListItem(ListItem):
    """ListItem that carries a scene index for branch selection."""

    def __init__(self, *args: Any, scene_index: int = 0, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.scene_index = scene_index


class SaveListItem(ListItem):
    """ListItem that carries a save filename for loading."""

    def __init__(self, *args: Any, save_filename: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.save_filename = save_filename


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
            yield Label(
                "[b]Rewind & Branch:[/b] Select a past moment to alter your fate.",
                id="branch-title",
            )
            list_view = ListView(id="branch-list")
            yield list_view
            yield Button("Cancel", id="cancel-branch", variant="error")

    def on_mount(self) -> None:
        list_view = self.query_one("#branch-list", ListView)
        for i, scene in enumerate(self.scenes):
            preview = scene["narrative"][:100].replace("\n", " ") + "..."
            choice_text = self.choices[i] if i < len(self.choices) else "Current Scene"
            label_text = f"Turn {i + 1}: {preview}\n[i]Choice made: {choice_text}[/i]"
            item = SceneListItem(Label(label_text, classes="scene-preview"), scene_index=i)
            list_view.append(item)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, SceneListItem):
            self.dismiss(event.item.scene_index)

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
        self.update(escape(self.frames[0]))
        self.set_interval(0.1, self.tick)

    def tick(self) -> None:
        self._frame_idx = (self._frame_idx + 1) % len(self.frames)
        self.update(escape(self.frames[self._frame_idx]))


class ConfirmScreen(ModalScreen[bool]):
    """A simple Yes/No confirmation dialog."""

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
        background: $background 80%;
    }
    #confirm-dialog {
        width: 50;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    #confirm-message {
        text-align: center;
        margin-bottom: 1;
    }
    #confirm-buttons {
        align: center middle;
        height: auto;
    }
    #confirm-buttons Button {
        width: auto;
        min-width: 12;
        margin: 0 1;
    }
    """

    BINDINGS = [
        ("y", "confirm", "Yes"),
        ("n", "cancel", "No"),
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._message = message

    def compose(self) -> ComposeResult:
        with Container(id="confirm-dialog"):
            yield Label(self._message, id="confirm-message")
            with Horizontal(id="confirm-buttons"):
                yield Button("[b]Y[/b]es", id="btn-confirm-yes", variant="error")
                yield Button("[b]N[/b]o", id="btn-confirm-no", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-confirm-yes":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


HELP_TEXT = """\
# ⌨️ Keyboard Shortcuts

| Key | Action |
|:---:|:-------|
| [b][reverse] 1 – 4 [/reverse][/b] | Select a choice by number |
| [b][reverse]  D  [/reverse][/b] | Change Theme (Dark/Light) |
| [b][reverse]  J  [/reverse][/b] | Toggle Journal panel |
| [b][reverse]  M  [/reverse][/b] | Toggle Story Map panel |
| [b][reverse]  B  [/reverse][/b] | Branch from past scene |
| [b][reverse]  U  [/reverse][/b] | Undo last choice |
| [b][reverse]  S  [/reverse][/b] | Save Game |
| [b][reverse]  L  [/reverse][/b] | Load Game |
| [b][reverse]  R  [/reverse][/b] | Restart Adventure |
| [b][reverse]  T  [/reverse][/b] | Toggle Typewriter |
| [b][reverse]  H  [/reverse][/b] | Show this help screen |
| [b][reverse]SPACE[/reverse][/b] | Skip typewriter narrator |
| [b][reverse]  Q  [/reverse][/b] | Quit Game |

---

# 📊 Player Stats

| Stat | Description |
|:-----|:------------|
| ❤️ **Health** | Your vitality. Low health disables risky choices. |
| 🪙 **Gold** | Currency earned through the adventure. |
| 🌟 **Reputation** | Your standing — high rep unlocks dialogue. |
| 🎒 **Inventory** | Items you carry. Some unlock special choices! |

---

*Press Escape or click Close to return to the adventure.*
"""


class HelpScreen(ModalScreen[None]):
    """Full-screen help overlay showing keybindings and game mechanics."""

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
        background: $background 80%;
    }
    #help-dialog {
        width: 70;
        height: 80%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #help-content {
        height: 1fr;
        overflow-y: auto;
    }
    #btn-help-close {
        width: 100%;
        margin-top: 1;
    }
    """

    BINDINGS = [
        ("escape", "close", "Close"),
        ("h", "close", "Close"),
    ]

    def compose(self) -> ComposeResult:
        with Container(id="help-dialog"):
            with Container(id="help-content"):
                yield Markdown(HELP_TEXT, id="help-text")
            yield Button("Close [b](Esc)[/b]", id="btn-help-close", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-help-close":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class LoadGameScreen(ModalScreen[str]):
    """Modal screen listing available save files for loading."""

    DEFAULT_CSS = """
    LoadGameScreen {
        align: center middle;
        background: $background 80%;
    }
    #load-dialog {
        width: 70;
        height: 70%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    #load-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    #load-list {
        height: 1fr;
        border: solid $secondary;
        margin-bottom: 1;
    }
    .save-entry {
        padding: 0 1;
    }
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, save_files: list[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._save_files = save_files

    def compose(self) -> ComposeResult:
        with Container(id="load-dialog"):
            yield Label("[b]📂 Load Game[/b] \u2014 Select a save file", id="load-title")
            yield ListView(id="load-list")
            yield Button("Cancel [b](Esc)[/b]", id="btn-load-cancel", variant="error")

    def on_mount(self) -> None:
        list_view = self.query_one("#load-list", ListView)
        for save_file in self._save_files:
            display_name = save_file.replace(".json", "").replace("_", " ")
            item = SaveListItem(
                Label(f"💾 {display_name}", classes="save-entry"), save_filename=save_file
            )
            list_view.append(item)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, SaveListItem):
            self.dismiss(event.item.save_filename)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-load-cancel":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
