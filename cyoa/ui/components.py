from typing import Any, cast

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.markup import escape
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Input,
    Label,
    ListItem,
    ListView,
    Markdown,
    ProgressBar,
    Static,
    Tree,
)

from cyoa.core import constants
from cyoa.core.model_download import DownloadProgress, ModelRecommendation
from cyoa.ui.presenters import (
    build_branch_preview,
    build_help_text,
    format_directives_label,
    format_inventory_label,
    format_objectives_label,
    format_runtime_text,
    format_save_display_name,
    format_stats_text,
    loading_story_text,
)

__all__ = [
    "ActionPanel",
    "ChoicePanel",
    "BranchScreen",
    "ThemeSpinner",
    "ConfirmScreen",
    "DialogActions",
    "DialogFrame",
    "HelpScreen",
    "LoadGameScreen",
    "GameWorkspace",
    "ModelDownloadScreen",
    "NotificationHistoryScreen",
    "OptionListScreen",
    "MainGamePanel",
    "FirstRunSetupScreen",
    "JournalPanel",
    "StartupChoiceScreen",
    "SettingsScreen",
    "StoryMapPanel",
    "StoryPane",
    "StatusBar",
    "TextPromptScreen",
    "JournalListItem",
    "SceneListItem",
    "SaveListItem",
    "StatusDisplay",
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


class OptionListItem(ListItem):
    """List item that carries an arbitrary string value."""

    def __init__(self, *args: Any, option_value: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.option_value = option_value


class JournalListItem(ListItem):
    """ListItem that points to a narrative turn in the story pane."""

    def __init__(
        self,
        *args: Any,
        scene_index: int = 0,
        entry_kind: str = "choice",
        label_text: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.scene_index = scene_index
        self.entry_kind = entry_kind
        self.label_text = label_text


class StoryPane(Container):
    """Organism for the story stream and contextual ASCII art."""

    def __init__(self, *, screen_reader_mode: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._screen_reader_mode = screen_reader_mode

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="story-container"):
            yield Markdown(
                loading_story_text(screen_reader_mode=self._screen_reader_mode),
                classes="story-turn",
                id="initial-turn",
            )
            yield Static("", id="scene-art", classes="hidden" if self._screen_reader_mode else "")


class StatusBar(Container):
    """Organism for loading state and runtime/player status."""

    def __init__(self, *, spinner_frames: list[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._spinner_frames = spinner_frames

    def compose(self) -> ComposeResult:
        yield ThemeSpinner(frames=self._spinner_frames, id="loading", classes="hidden")
        yield StatusDisplay(id="status-display")


class ChoicePanel(Container):
    """Organism that hosts the current turn's available actions."""


class ActionPanel(Container):
    """Shared lower dock for runtime status and available actions."""

    def __init__(self, *, spinner_frames: list[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._spinner_frames = spinner_frames

    def compose(self) -> ComposeResult:
        yield StatusBar(spinner_frames=self._spinner_frames, id="status-bar")
        yield ChoicePanel(id="choices-container")


class JournalPanel(Container):
    """Organism for the in-game journal side panel."""

    def compose(self) -> ComposeResult:
        with Container(classes="side-panel-shell side-panel-journal"):
            yield Label("In-Game Journal", id="journal-title")
            yield Label("Recent decisions and fractures", classes="side-panel-kicker")
            with Container(classes="side-panel-body"):
                yield Label("Timeline Log", classes="side-panel-section-title")
                yield ListView(id="journal-list")


class StoryMapPanel(Container):
    """Organism for the branching story-map side panel."""

    def compose(self) -> ComposeResult:
        with Container(classes="side-panel-shell side-panel-map"):
            yield Label("Story Map", id="story-map-title")
            yield Label("Branch structure and current route", classes="side-panel-kicker")
            with Container(classes="side-panel-body"):
                yield Label("Adventure Topology", classes="side-panel-section-title")
                yield Tree("Story", id="story-map-tree")


class MainGamePanel(Vertical):
    """Organism for the main play area within the workspace template."""

    def __init__(self, *, spinner_frames: list[str], screen_reader_mode: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._spinner_frames = spinner_frames
        self._screen_reader_mode = screen_reader_mode

    def compose(self) -> ComposeResult:
        yield StoryPane(screen_reader_mode=self._screen_reader_mode)
        yield ActionPanel(spinner_frames=self._spinner_frames, id="action-panel")


class GameWorkspace(Horizontal):
    """Template for the primary in-game workspace."""

    def __init__(self, *, spinner_frames: list[str], screen_reader_mode: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._spinner_frames = spinner_frames
        self._screen_reader_mode = screen_reader_mode

    def compose(self) -> ComposeResult:
        yield MainGamePanel(
            spinner_frames=self._spinner_frames,
            screen_reader_mode=self._screen_reader_mode,
            id="main-container",
        )
        yield JournalPanel(id="journal-panel", classes="panel-collapsed")
        yield StoryMapPanel(id="story-map-panel", classes="panel-collapsed")


class DialogFrame(Container):
    """Reusable modal dialog shell."""


class DialogActions(Horizontal):
    """Reusable modal action row."""


class ButtonGroupScreen(ModalScreen[Any]):
    """Modal helper that provides keyboard-first button group navigation."""

    def _action_buttons(self) -> list[Button]:
        return [button for button in self.query(Button) if not button.disabled]

    def _focus_first_action_button(self) -> None:
        buttons = self._action_buttons()
        if buttons:
            self.call_after_refresh(buttons[0].focus)

    def _move_action_focus(self, step: int) -> None:
        buttons = self._action_buttons()
        if not buttons:
            return

        focused = self.focused
        try:
            current_index = buttons.index(focused) if isinstance(focused, Button) else -1
        except ValueError:
            current_index = -1

        if current_index == -1:
            target = buttons[0] if step > 0 else buttons[-1]
        else:
            target = buttons[(current_index + step) % len(buttons)]
        target.focus()

    def action_focus_next_button(self) -> None:
        self._move_action_focus(1)

    def action_focus_previous_button(self) -> None:
        self._move_action_focus(-1)


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
        max-width: 90%;
        max-height: 90%;
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

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, scenes: list[dict[str, Any]], choices: list[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.scenes = scenes
        self.choices = choices

    @staticmethod
    def _build_scene_preview(scene: dict[str, Any], turn_index: int, choice_text: str) -> str:
        """Build a compact but information-dense branch preview label."""
        return build_branch_preview(scene, turn_index, choice_text)

    def compose(self) -> ComposeResult:
        with DialogFrame(id="branch-dialog", classes="dialog-frame dialog-frame-scroll"):
            yield Label(
                "[b]Rewind & Branch:[/b] Select a past moment to alter your fate.",
                id="branch-title",
                classes="dialog-title",
            )
            list_view = ListView(id="branch-list", classes="dialog-list")
            yield list_view
            yield Button("Cancel", id="cancel-branch", variant="error")

    def on_mount(self) -> None:
        list_view = self.query_one("#branch-list", ListView)
        for i, scene in enumerate(self.scenes):
            choice_text = self.choices[i] if i < len(self.choices) else "Current Scene"
            label_text = self._build_scene_preview(scene, i, choice_text)
            item = SceneListItem(Label(label_text, classes="scene-preview"), scene_index=i)
            list_view.append(item)
        self.call_after_refresh(list_view.focus)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, SceneListItem):
            self.dismiss(event.item.scene_index)

    def on_button_pressed(self, event: Button.Pressed) -> None:  # noqa: C901
        if event.button.id == "cancel-branch":
            self.dismiss(None)

    def action_cancel(self) -> None:
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
        try:
            reduced_motion = bool(getattr(self.app, "reduced_motion", False))
        except Exception:
            reduced_motion = False
        if "hidden" in self.classes or reduced_motion:
            return
        self._frame_idx = (self._frame_idx + 1) % len(self.frames)
        self.update(escape(self.frames[self._frame_idx]))


class ConfirmScreen(ButtonGroupScreen):
    """A simple Yes/No confirmation dialog."""

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
        background: $background 80%;
    }
    #confirm-dialog {
        width: 50;
    }
    #confirm-buttons {
        width: 1fr;
        margin-top: 1;
    }
    """

    BINDINGS = [
        ("y", "confirm", "Yes"),
        ("n", "cancel", "No"),
        ("left", "focus_previous_button", "Previous"),
        ("right", "focus_next_button", "Next"),
        ("up", "focus_previous_button", "Previous"),
        ("down", "focus_next_button", "Next"),
        ("tab", "focus_next_button", "Next"),
        ("shift+tab", "focus_previous_button", "Previous"),
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._message = message

    def compose(self) -> ComposeResult:
        with DialogFrame(id="confirm-dialog", classes="dialog-frame"):
            yield Label(self._message, id="confirm-message", classes="dialog-message")
            with DialogActions(id="confirm-buttons", classes="dialog-actions"):
                yield Button("[b]Y[/b]es", id="btn-confirm-yes", variant="error")
                yield Button("[b]N[/b]o", id="btn-confirm-no", variant="primary")

    def on_mount(self) -> None:
        self._focus_first_action_button()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-confirm-yes":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


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

    def __init__(self, *, screen_reader_mode: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._screen_reader_mode = screen_reader_mode

    def compose(self) -> ComposeResult:
        with DialogFrame(id="help-dialog", classes="dialog-frame dialog-frame-scroll dialog-frame-accent"):
            with Container(id="help-content", classes="dialog-content"):
                yield Markdown(build_help_text(screen_reader_mode=self._screen_reader_mode), id="help-text")
            yield Button("Close [b](Esc)[/b]", id="btn-help-close", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#btn-help-close", Button).focus()

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
    }
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, save_files: list[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._save_files = save_files

    def compose(self) -> ComposeResult:
        with DialogFrame(id="load-dialog", classes="dialog-frame dialog-frame-scroll"):
            yield Label("[b]Load Game[/b] \u2014 Select a save file", id="load-title", classes="dialog-title")
            yield ListView(id="load-list", classes="dialog-list")
            yield Button("Cancel [b](Esc)[/b]", id="btn-load-cancel", variant="error")

    def on_mount(self) -> None:
        list_view = self.query_one("#load-list", ListView)
        for save_file in self._save_files:
            display_name = format_save_display_name(save_file)
            item = SaveListItem(
                Label(display_name, classes="dialog-entry"), save_filename=save_file
            )
            list_view.append(item)
        self.call_after_refresh(list_view.focus if self._save_files else self.query_one("#btn-load-cancel", Button).focus)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, SaveListItem):
            self.dismiss(event.item.save_filename)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-load-cancel":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class OptionListScreen(ModalScreen[str]):
    """Generic modal selection list used for bookmark restore/export flows."""

    DEFAULT_CSS = LoadGameScreen.DEFAULT_CSS
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, options: list[str], *, empty_message: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._title = title
        self._options = options
        self._empty_message = empty_message

    def compose(self) -> ComposeResult:
        with DialogFrame(id="load-dialog", classes="dialog-frame dialog-frame-scroll"):
            yield Label(self._title, id="load-title", classes="dialog-title")
            yield ListView(id="load-list", classes="dialog-list")
            yield Button("Cancel [b](Esc)[/b]", id="btn-load-cancel", variant="error")

    def on_mount(self) -> None:
        list_view = self.query_one("#load-list", ListView)
        if not self._options:
            list_view.append(OptionListItem(Label(self._empty_message, classes="dialog-entry"), option_value=""))
        else:
            for option in self._options:
                list_view.append(OptionListItem(Label(option, classes="dialog-entry"), option_value=option))
        self.call_after_refresh(list_view.focus if self._options else self.query_one("#btn-load-cancel", Button).focus)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, OptionListItem) and event.item.option_value:
            self.dismiss(event.item.option_value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-load-cancel":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class StartupChoiceScreen(ButtonGroupScreen):
    """Startup modal that lets the player resume or begin a fresh run."""

    DEFAULT_CSS = """
    StartupChoiceScreen {
        align: center middle;
        background: $background 80%;
    }
    #startup-dialog {
        width: 72;
        max-width: 92%;
    }
    #startup-buttons {
        width: 1fr;
        margin-top: 1;
    }
    #startup-buttons Button {
        width: 1fr;
        min-width: 20;
    }
    """

    BINDINGS = [
        ("r", "resume", "Resume"),
        ("n", "new_game", "New Game"),
        ("left", "focus_previous_button", "Previous"),
        ("right", "focus_next_button", "Next"),
        ("up", "focus_previous_button", "Previous"),
        ("down", "focus_next_button", "Next"),
        ("tab", "focus_next_button", "Next"),
        ("shift+tab", "focus_previous_button", "Previous"),
    ]

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._message = message

    def compose(self) -> ComposeResult:
        with DialogFrame(id="startup-dialog", classes="dialog-frame dialog-frame-accent"):
            yield Static("AUTOSAVE DETECTED", id="startup-kicker")
            yield Label("[b]Continue or Start Over[/b]", id="startup-title", classes="dialog-title")
            yield Static(self._message, id="startup-message", classes="dialog-message")
            yield Label(
                "Resume picks up exactly where you left off. New Game discards the autosave.",
                id="startup-hint",
            )
            with DialogActions(id="startup-buttons", classes="dialog-actions"):
                yield Button("[b]R[/b]esume Previous Save", id="btn-startup-resume", variant="primary")
                yield Button("[b]N[/b]ew Game", id="btn-startup-new", variant="success")

    def on_mount(self) -> None:
        self._focus_first_action_button()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-startup-resume":
            self.dismiss("resume")
        elif event.button.id == "btn-startup-new":
            self.dismiss("new")

    def action_resume(self) -> None:
        self.dismiss("resume")

    def action_new_game(self) -> None:
        self.dismiss("new")


class FirstRunSetupScreen(ButtonGroupScreen):
    """First-run setup modal for choosing a safe runtime path."""

    DEFAULT_CSS = """
    FirstRunSetupScreen {
        align: center middle;
        background: $background 80%;
    }
    #first-run-dialog {
        width: 78;
        max-width: 94%;
    }
    .first-run-option {
        width: 100%;
        margin-top: 1;
    }
    .first-run-note {
        color: $text-muted;
    }
    """

    BINDINGS = [
        ("q", "quick_demo", "Quick Demo"),
        ("d", "download_model", "Download Local Model"),
        ("tab", "focus_next_button", "Next"),
        ("shift+tab", "focus_previous_button", "Previous"),
        ("up", "focus_previous_button", "Previous"),
        ("down", "focus_next_button", "Next"),
    ]

    def __init__(
        self,
        *,
        general_notes: tuple[str, ...] = (),
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._general_notes = general_notes

    def compose(self) -> ComposeResult:
        with DialogFrame(id="first-run-dialog", classes="dialog-frame dialog-frame-accent dialog-frame-scroll"):
            yield Static("FIRST RUN SETUP", id="first-run-kicker")
            yield Label("[b]Choose How To Start[/b]", id="first-run-title", classes="dialog-title")
            yield Static(
                "Pick a runtime path before the adventure begins. This choice is saved for later launches.",
                id="first-run-message",
                classes="dialog-message",
            )
            for note in self._general_notes:
                yield Label(note, classes="first-run-note")
            yield Button(
                "[b]Q[/b]uick Demo",
                id="btn-first-run-mock",
                variant="primary",
                classes="first-run-option",
            )
            yield Label(
                "Start immediately with the built-in mock engine. Best for first launch and smoke testing.",
                classes="first-run-note",
            )
            yield Button(
                "[b]D[/b]ownload Local Model",
                id="btn-first-run-download",
                variant="default",
                classes="first-run-option",
            )
            yield Label(
                "Download a recommended GGUF into the app data folder and use it on future launches.",
                classes="first-run-note",
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-first-run-mock":
            self.dismiss("mock")
        elif event.button.id == "btn-first-run-download":
            self.dismiss("download")

    def on_mount(self) -> None:
        self._focus_first_action_button()

    def action_quick_demo(self) -> None:
        self.dismiss("mock")

    def action_download_model(self) -> None:
        self.dismiss("download")


class ModelDownloadScreen(ButtonGroupScreen):
    """Modal that guides users through downloading a recommended local model."""

    DEFAULT_CSS = """
    ModelDownloadScreen {
        align: center middle;
        background: $background 80%;
    }
    #model-download-dialog {
        width: 82;
        max-width: 94%;
    }
    #model-download-progress {
        margin: 1 0;
    }
    #model-download-actions {
        width: 100%;
        margin-top: 1;
    }
    #model-download-actions Button {
        width: 1fr;
    }
    .model-download-note {
        color: $text-muted;
    }
    """

    BINDINGS = [
        ("tab", "focus_next_button", "Next"),
        ("shift+tab", "focus_previous_button", "Previous"),
        ("left", "focus_previous_button", "Previous"),
        ("right", "focus_next_button", "Next"),
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        recommendation: ModelRecommendation,
        *,
        models_dir: str,
        preflight_notes: tuple[str, ...] = (),
        blocked_reason: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._recommendation = recommendation
        self._models_dir = models_dir
        self._preflight_notes = preflight_notes
        self._blocked_reason = blocked_reason
        self._started = False
        self._finished = False

    def compose(self) -> ComposeResult:
        with DialogFrame(
            id="model-download-dialog",
            classes="dialog-frame dialog-frame-accent dialog-frame-scroll",
        ):
            yield Static("LOCAL MODEL SETUP", id="model-download-kicker")
            yield Label("[b]Download A Recommended Model[/b]", classes="dialog-title")
            yield Static(
                (
                    f"Recommended for this machine: {self._recommendation.label} "
                    f"({self._recommendation.filename})"
                ),
                id="model-download-summary",
                classes="dialog-message",
            )
            yield Label(
                f"Source: {self._recommendation.repo_id}",
                id="model-download-source",
                classes="model-download-note",
            )
            yield Label(
                f"Storage: {self._models_dir}",
                id="model-download-target",
                classes="model-download-note",
            )
            for note in self._preflight_notes:
                yield Label(note, classes="model-download-note")
            yield ProgressBar(total=100, show_percentage=True, show_eta=False, id="model-download-progress")
            yield Label(
                "Local download unavailable." if self._blocked_reason else "Ready to download.",
                id="model-download-stage",
            )
            yield Label(
                self._blocked_reason
                or "Cancellation is best-effort and may wait for the current transfer step to finish.",
                id="model-download-detail",
                classes="model-download-note",
            )
            with DialogActions(id="model-download-actions", classes="dialog-actions"):
                yield Button(
                    "Start Download",
                    id="btn-model-download-start",
                    variant="primary",
                    disabled=self._blocked_reason is not None,
                )
                yield Button("Cancel", id="btn-model-download-cancel", variant="error")

    def on_mount(self) -> None:
        self._focus_first_action_button()

    def on_button_pressed(self, event: Button.Pressed) -> None:  # noqa: C901
        if event.button.id == "btn-model-download-start" and not self._started:
            self._started = True
            self._set_busy_state()
            if self.app is not None:
                cast(Any, self.app).begin_first_run_model_download(self)
        elif event.button.id == "btn-model-download-cancel":
            if self._finished:
                self.dismiss(None)
            elif self.app is not None:
                cast(Any, self.app).cancel_first_run_model_download()
                self.mark_cancelling()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _set_busy_state(self) -> None:
        self.query_one("#btn-model-download-start", Button).disabled = True

    def update_progress(self, progress: DownloadProgress) -> None:
        self.query_one("#model-download-progress", ProgressBar).progress = progress.percent
        self.query_one("#model-download-stage", Label).update(progress.stage)
        self.query_one("#model-download-detail", Label).update(progress.detail)

    def mark_cancelling(self) -> None:
        self.query_one("#model-download-stage", Label).update("Cancelling")
        self.query_one("#model-download-detail", Label).update(
            "Stopping after the current transfer step finishes."
        )
        self.query_one("#btn-model-download-cancel", Button).disabled = True

    def mark_failed(self, message: str) -> None:
        self._finished = True
        self.query_one("#model-download-stage", Label).update("Download failed")
        self.query_one("#model-download-detail", Label).update(message)
        self.query_one("#btn-model-download-cancel", Button).label = "Close"
        self.query_one("#btn-model-download-cancel", Button).disabled = False

    def mark_complete(self, path: str) -> None:
        self._finished = True
        self.query_one("#model-download-progress", ProgressBar).progress = 100
        self.query_one("#model-download-stage", Label).update("Download complete")
        self.query_one("#model-download-detail", Label).update(f"Saved model to {path}")
        self.query_one("#btn-model-download-cancel", Button).label = "Continue"
        self.query_one("#btn-model-download-cancel", Button).disabled = False


class SettingsScreen(ModalScreen[dict[str, Any]]):
    """Modal settings screen for persisted consumer-facing preferences."""

    DEFAULT_CSS = """
    SettingsScreen {
        align: center middle;
        background: $background 80%;
    }
    #settings-dialog {
        width: 86;
        height: 90%;
        max-width: 96%;
    }
    .settings-section {
        margin-top: 1;
    }
    .settings-label {
        margin-top: 1;
    }
    .settings-value {
        color: $text-muted;
        margin-bottom: 1;
    }
    .settings-row {
        width: 100%;
        height: auto;
    }
    .settings-row Button {
        width: 1fr;
        min-width: 12;
    }
    #settings-model-path {
        margin: 1 0;
    }
    #settings-actions {
        width: 100%;
        margin-top: 1;
    }
    #settings-actions Button {
        width: 1fr;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel"), ("enter", "save", "Save")]

    def __init__(
        self,
        *,
        provider: str | None,
        model_path: str | None,
        theme: str,
        dark: bool,
        reduced_motion: bool,
        screen_reader_mode: bool,
        text_scale: str,
        line_width: str,
        line_spacing: str,
        typewriter: bool,
        typewriter_speed: str,
        diagnostics_enabled: bool,
        available_themes: list[str],
        high_contrast: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._provider = provider if provider in {"mock", "llama_cpp"} else "mock"
        self._model_path = model_path or ""
        self._theme_names = available_themes or [theme]
        self._theme_index = self._resolve_theme_index(theme)
        self._dark = dark
        self._high_contrast = high_contrast
        self._reduced_motion = reduced_motion
        self._screen_reader_mode = screen_reader_mode
        self._text_scale = (
            text_scale if text_scale in constants.TEXT_SCALE_OPTIONS else "standard"
        )
        self._line_width = (
            line_width if line_width in constants.READING_WIDTH_OPTIONS else "standard"
        )
        self._line_spacing = (
            line_spacing if line_spacing in constants.LINE_SPACING_OPTIONS else "standard"
        )
        self._typewriter = typewriter
        self._typewriter_speed = (
            typewriter_speed if typewriter_speed in constants.TYPEWRITER_SPEEDS else "normal"
        )
        self._diagnostics_enabled = diagnostics_enabled

    def _resolve_theme_index(self, theme: str) -> int:
        try:
            return self._theme_names.index(theme)
        except ValueError:
            self._theme_names = [theme, *self._theme_names]
            return 0

    @property
    def _current_theme(self) -> str:
        return self._theme_names[self._theme_index]

    def compose(self) -> ComposeResult:
        with DialogFrame(id="settings-dialog", classes="dialog-frame dialog-frame-scroll dialog-frame-accent"):
            yield Static("SETTINGS", id="settings-kicker")
            yield Label("[b]Adventure Settings[/b]", classes="dialog-title")
            yield Static(
                "Dark mode and typewriter updates apply immediately. Runtime provider, model path, theme pack, and diagnostics apply on restart.",
                classes="dialog-message",
            )

            yield Label("Runtime Provider", classes="settings-label")
            with Horizontal(classes="settings-row settings-section"):
                yield Button("Quick Demo", id="btn-settings-provider-mock")
                yield Button("Local Model", id="btn-settings-provider-llama")
            yield Label("", id="settings-provider-value", classes="settings-value")

            yield Label("Local Model Path", classes="settings-label")
            yield Input(
                value=self._model_path,
                placeholder="/path/to/model.gguf",
                id="settings-model-path",
            )
            yield Label(
                "Used on next restart when Local Model is selected. Leave blank to keep demo mode safe.",
                classes="settings-value",
            )

            yield Label("Theme Pack", classes="settings-label")
            with Horizontal(classes="settings-row settings-section"):
                yield Button("Previous", id="btn-settings-theme-prev")
                yield Button("Next", id="btn-settings-theme-next")
            yield Label("", id="settings-theme-value", classes="settings-value")

            yield Label("Appearance", classes="settings-label")
            with Horizontal(classes="settings-row settings-section"):
                yield Button("Dark", id="btn-settings-dark-on")
                yield Button("Light", id="btn-settings-dark-off")

            yield Label("Contrast", classes="settings-label")
            with Horizontal(classes="settings-row settings-section"):
                yield Button("Standard", id="btn-settings-contrast-standard")
                yield Button("High Contrast", id="btn-settings-contrast-high")
            yield Label(
                "High Contrast uses a fixed accessible palette for story cards, choices, and panel states.",
                classes="settings-value",
            )

            yield Label("Motion", classes="settings-label")
            with Horizontal(classes="settings-row settings-section"):
                yield Button("Standard", id="btn-settings-motion-standard")
                yield Button("Reduced", id="btn-settings-motion-reduced")
            yield Label(
                "Reduced motion disables spinner animation and renders narrated text instantly.",
                classes="settings-value",
            )

            yield Label("Accessibility", classes="settings-label")
            with Horizontal(classes="settings-row settings-section"):
                yield Button("Standard", id="btn-settings-screen-reader-off")
                yield Button("Screen Reader Friendly", id="btn-settings-screen-reader-on")
            yield Label(
                "Screen Reader Friendly mode removes ASCII art, uses plain status labels, and keeps the latest status message visible.",
                classes="settings-value",
            )

            yield Label("Text Scale", classes="settings-label")
            with Horizontal(classes="settings-row settings-section"):
                yield Button("100%", id="btn-settings-scale-standard")
                yield Button("150%", id="btn-settings-scale-large")
                yield Button("200%", id="btn-settings-scale-xlarge")
            yield Label(
                "Large and 200% equivalent modes add roomier story cards, taller choices, and stacked status metadata.",
                classes="settings-value",
            )

            yield Label("Line Width", classes="settings-label")
            with Horizontal(classes="settings-row settings-section"):
                yield Button("Focused", id="btn-settings-width-focused")
                yield Button("Standard", id="btn-settings-width-standard")
                yield Button("Full", id="btn-settings-width-full")
            yield Label(
                "Focused keeps a shorter reading line. Full uses more of the available panel width.",
                classes="settings-value",
            )

            yield Label("Line Spacing", classes="settings-label")
            with Horizontal(classes="settings-row settings-section"):
                yield Button("Compact", id="btn-settings-spacing-compact")
                yield Button("Standard", id="btn-settings-spacing-standard")
                yield Button("Relaxed", id="btn-settings-spacing-relaxed")
            yield Label(
                "Relaxed spacing adds breathing room between story cards, status blocks, and choice labels.",
                classes="settings-value",
            )

            yield Label("Typewriter", classes="settings-label")
            with Horizontal(classes="settings-row settings-section"):
                yield Button("On", id="btn-settings-typewriter-on")
                yield Button("Off", id="btn-settings-typewriter-off")

            yield Label("Typewriter Speed", classes="settings-label")
            with Horizontal(classes="settings-row settings-section"):
                yield Button("Slow", id="btn-settings-speed-slow")
                yield Button("Normal", id="btn-settings-speed-normal")
                yield Button("Fast", id="btn-settings-speed-fast")
                yield Button("Instant", id="btn-settings-speed-instant")

            yield Label("Diagnostics", classes="settings-label")
            with Horizontal(classes="settings-row settings-section"):
                yield Button("Off", id="btn-settings-diagnostics-off")
                yield Button("On", id="btn-settings-diagnostics-on")
            yield Label(
                "Enables advanced RAG diagnostics for future launches.",
                id="settings-diagnostics-value",
                classes="settings-value",
            )

            yield Label("Recovery & Support", classes="settings-label")
            with Horizontal(classes="settings-row settings-section"):
                yield Button("Test Backend", id="btn-settings-test-backend")
                yield Button("Reveal Saves", id="btn-settings-reveal-saves")
            with Horizontal(classes="settings-row"):
                yield Button("Reset Settings", id="btn-settings-reset", variant="warning")
            yield Label(
                "Use these tools to verify your configured backend, open the save folder, or return to safe defaults.",
                classes="settings-value",
            )

            with DialogActions(id="settings-actions", classes="dialog-actions"):
                yield Button("Save", id="btn-settings-save", variant="primary")
                yield Button("Cancel", id="btn-settings-cancel", variant="error")

    def on_mount(self) -> None:
        self._refresh_state()
        self.query_one("#settings-model-path", Input).focus()

    def _set_selected(self, button_id: str, selected: bool) -> None:
        button = self.query_one(f"#{button_id}", Button)
        button.variant = "primary" if selected else "default"

    def _refresh_state(self) -> None:
        self._set_selected("btn-settings-provider-mock", self._provider == "mock")
        self._set_selected("btn-settings-provider-llama", self._provider == "llama_cpp")
        self.query_one("#settings-provider-value", Label).update(
            "Quick Demo keeps startup safe." if self._provider == "mock" else "Use a saved GGUF on restart."
        )

        self._set_selected("btn-settings-dark-on", self._dark)
        self._set_selected("btn-settings-dark-off", not self._dark)
        self._set_selected("btn-settings-contrast-standard", not self._high_contrast)
        self._set_selected("btn-settings-contrast-high", self._high_contrast)
        self._set_selected("btn-settings-motion-standard", not self._reduced_motion)
        self._set_selected("btn-settings-motion-reduced", self._reduced_motion)
        self._set_selected("btn-settings-screen-reader-on", self._screen_reader_mode)
        self._set_selected("btn-settings-screen-reader-off", not self._screen_reader_mode)
        for scale in constants.TEXT_SCALE_OPTIONS:
            self._set_selected(f"btn-settings-scale-{scale}", self._text_scale == scale)
        for width in constants.READING_WIDTH_OPTIONS:
            self._set_selected(f"btn-settings-width-{width}", self._line_width == width)
        for spacing in constants.LINE_SPACING_OPTIONS:
            self._set_selected(
                f"btn-settings-spacing-{spacing}",
                self._line_spacing == spacing,
            )
        self._set_selected("btn-settings-typewriter-on", self._typewriter)
        self._set_selected("btn-settings-typewriter-off", not self._typewriter)

        for speed in constants.TYPEWRITER_SPEEDS:
            self._set_selected(
                f"btn-settings-speed-{speed}",
                self._typewriter_speed == speed,
            )

        self._set_selected("btn-settings-diagnostics-on", self._diagnostics_enabled)
        self._set_selected("btn-settings-diagnostics-off", not self._diagnostics_enabled)
        self.query_one("#settings-theme-value", Label).update(
            f"{self._current_theme} ({self._theme_index + 1}/{len(self._theme_names)})"
        )

    def _dismiss_with_value(self) -> None:
        model_path = self.query_one("#settings-model-path", Input).value.strip() or None
        self.dismiss(
            {
                "provider": self._provider,
                "model_path": model_path,
                "theme": self._current_theme,
                "dark": self._dark,
                "high_contrast": self._high_contrast,
                "reduced_motion": self._reduced_motion,
                "screen_reader_mode": self._screen_reader_mode,
                "text_scale": self._text_scale,
                "line_width": self._line_width,
                "line_spacing": self._line_spacing,
                "typewriter": self._typewriter,
                "typewriter_speed": self._typewriter_speed,
                "diagnostics_enabled": self._diagnostics_enabled,
            }
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:  # noqa: C901
        button_id = event.button.id
        if button_id == "btn-settings-save":
            self._dismiss_with_value()
            return
        if button_id == "btn-settings-cancel":
            self.dismiss(None)
            return
        if button_id == "btn-settings-test-backend":
            self.dismiss({"action": "test_backend"})
            return
        if button_id == "btn-settings-reveal-saves":
            self.dismiss({"action": "reveal_saves"})
            return
        if button_id == "btn-settings-reset":
            self.dismiss({"action": "reset_settings"})
            return
        if button_id == "btn-settings-provider-mock":
            self._provider = "mock"
        elif button_id == "btn-settings-provider-llama":
            self._provider = "llama_cpp"
        elif button_id == "btn-settings-theme-prev":
            self._theme_index = (self._theme_index - 1) % len(self._theme_names)
        elif button_id == "btn-settings-theme-next":
            self._theme_index = (self._theme_index + 1) % len(self._theme_names)
        elif button_id == "btn-settings-dark-on":
            self._dark = True
        elif button_id == "btn-settings-dark-off":
            self._dark = False
        elif button_id == "btn-settings-contrast-standard":
            self._high_contrast = False
        elif button_id == "btn-settings-contrast-high":
            self._high_contrast = True
        elif button_id == "btn-settings-motion-standard":
            self._reduced_motion = False
        elif button_id == "btn-settings-motion-reduced":
            self._reduced_motion = True
        elif button_id == "btn-settings-screen-reader-on":
            self._screen_reader_mode = True
        elif button_id == "btn-settings-screen-reader-off":
            self._screen_reader_mode = False
        elif button_id and button_id.startswith("btn-settings-scale-"):
            self._text_scale = button_id.removeprefix("btn-settings-scale-")
        elif button_id and button_id.startswith("btn-settings-width-"):
            self._line_width = button_id.removeprefix("btn-settings-width-")
        elif button_id and button_id.startswith("btn-settings-spacing-"):
            self._line_spacing = button_id.removeprefix("btn-settings-spacing-")
        elif button_id == "btn-settings-typewriter-on":
            self._typewriter = True
        elif button_id == "btn-settings-typewriter-off":
            self._typewriter = False
        elif button_id == "btn-settings-diagnostics-on":
            self._diagnostics_enabled = True
        elif button_id == "btn-settings-diagnostics-off":
            self._diagnostics_enabled = False
        elif button_id and button_id.startswith("btn-settings-speed-"):
            self._typewriter_speed = button_id.rsplit("-", 1)[-1]
        self._refresh_state()

    def action_save(self) -> None:
        self._dismiss_with_value()

    def action_cancel(self) -> None:
        self.dismiss(None)


class NotificationHistoryScreen(ModalScreen[None]):
    """Modal screen that exposes recent notifications in chronological order."""

    DEFAULT_CSS = LoadGameScreen.DEFAULT_CSS
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, entries: list[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._entries = entries

    def compose(self) -> ComposeResult:
        with DialogFrame(id="notification-history-dialog", classes="dialog-frame dialog-frame-scroll"):
            yield Label("[b]Notification History[/b]", id="notification-history-title", classes="dialog-title")
            if self._entries:
                yield ListView(id="notification-history-list", classes="dialog-list")
            else:
                yield Static("No notifications yet.", id="notification-history-empty", classes="dialog-entry")
            yield Button("Close [b](Esc)[/b]", id="btn-notification-history-close", variant="primary")

    def on_mount(self) -> None:
        if not self._entries:
            self.query_one("#btn-notification-history-close", Button).focus()
            return

        list_view = self.query_one("#notification-history-list", ListView)
        for index, entry in enumerate(self._entries, start=1):
            list_view.append(
                ListItem(Label(escape(f"{index}. {entry}"), classes="dialog-entry"))
            )
        list_view.index = len(self._entries) - 1
        list_view.scroll_end(animate=False)
        self.call_after_refresh(list_view.focus)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-notification-history-close":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class TextPromptScreen(ModalScreen[str]):
    """Simple text-entry modal for bookmark/directive editing."""

    DEFAULT_CSS = """
    TextPromptScreen {
        align: center middle;
        background: $background 80%;
    }
    #text-prompt-dialog {
        width: 70;
    }
    #text-prompt-input {
        margin: 1 0;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel"), ("enter", "submit", "Submit")]

    def __init__(self, title: str, *, value: str = "", placeholder: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._title = title
        self._value = value
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with DialogFrame(id="text-prompt-dialog", classes="dialog-frame"):
            yield Label(self._title, id="load-title", classes="dialog-title")
            yield Input(value=self._value, placeholder=self._placeholder, id="text-prompt-input")
            with DialogActions(id="text-prompt-buttons", classes="dialog-actions"):
                yield Button("Save", id="btn-prompt-save", variant="primary")
                yield Button("Cancel", id="btn-prompt-cancel", variant="error")

    def on_mount(self) -> None:
        self.query_one("#text-prompt-input", Input).focus()

    def _dismiss_with_value(self) -> None:
        self.dismiss(self.query_one("#text-prompt-input", Input).value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-prompt-save":
            self._dismiss_with_value()
        elif event.button.id == "btn-prompt-cancel":
            self.dismiss(None)

    def action_submit(self) -> None:
        self._dismiss_with_value()

    def action_cancel(self) -> None:
        self.dismiss(None)

class StatusDisplay(Static):
    """A reactive status area that groups player state and runtime metadata."""

    health = reactive(100)
    gold = reactive(0)
    reputation = reactive(0)
    inventory: reactive[list[str]] = reactive([])
    objectives: reactive[list[str]] = reactive([])
    directives: reactive[list[str]] = reactive([])
    generation_preset = reactive("balanced")
    runtime_profile = reactive("custom")
    provider_label = reactive("llama_cpp")
    engine_phase = reactive("idle")
    latest_status = reactive("Status: Waiting for adventure updates.")
    screen_reader_mode = reactive(False)

    def compose(self) -> ComposeResult:
        with Horizontal(id="stats-row"):
            yield Label("Health", id="health-label")
            yield ProgressBar(total=100, show_percentage=False, show_eta=False, id="health-bar")
            yield Label("100% Stable", id="health-value")
        with Container(id="status-meta-row"):
            yield Label("", id="stats-text")
            yield Label("", id="runtime-text")
        yield Label("", id="inventory-label")
        yield Label("", id="objectives-label")
        yield Label("", id="directives-label")
        yield Label("", id="latest-status-label")

    def on_mount(self) -> None:
        self._refresh_accessibility_labels()

    def watch_health(self, health: int) -> None:
        self.query_one("#health-bar", ProgressBar).progress = health
        self.query_one("#health-value", Label).update(f"{health}% {self._health_status_text(health)}")
        self._update_stats_text()
        self._set_health_class(health)

    def watch_gold(self, gold: int) -> None:
        self._update_stats_text()

    def watch_reputation(self, reputation: int) -> None:
        self._update_stats_text()

    def watch_inventory(self, inventory: list[str]) -> None:
        self.query_one("#inventory-label", Label).update(
            format_inventory_label(inventory, screen_reader_mode=self.screen_reader_mode)
        )

    def watch_objectives(self, objectives: list[str]) -> None:
        self.query_one("#objectives-label", Label).update(
            format_objectives_label(objectives, screen_reader_mode=self.screen_reader_mode)
        )

    def _update_stats_text(self) -> None:
        self.query_one(
            "#stats-text",
            Label,
        ).update(
            format_stats_text(
                gold=self.gold,
                reputation=self.reputation,
                screen_reader_mode=self.screen_reader_mode,
            )
        )
        self.query_one(
            "#runtime-text",
            Label,
        ).update(
            format_runtime_text(
                generation_preset=self.generation_preset,
                engine_phase=self.engine_phase,
                provider_label=self.provider_label,
                runtime_profile=self.runtime_profile,
                screen_reader_mode=self.screen_reader_mode,
            )
        )

    def watch_directives(self, directives: list[str]) -> None:
        self.query_one("#directives-label", Label).update(
            format_directives_label(directives, screen_reader_mode=self.screen_reader_mode)
        )

    def watch_latest_status(self, latest_status: str) -> None:
        self.query_one("#latest-status-label", Label).update(latest_status)

    def watch_screen_reader_mode(self, _enabled: bool) -> None:
        self._refresh_accessibility_labels()

    def watch_generation_preset(self, _preset: str) -> None:
        self._update_stats_text()

    def watch_runtime_profile(self, _profile: str) -> None:
        self._update_stats_text()

    def watch_provider_label(self, _provider: str) -> None:
        self._update_stats_text()

    def watch_engine_phase(self, _phase: str) -> None:
        self._update_stats_text()

    def _set_health_class(self, health: int) -> None:
        self.remove_class("health-high", "health-mid", "health-low")
        if health < 30:
            self.add_class("health-low")
        elif health < 70:
            self.add_class("health-mid")
        else:
            self.add_class("health-high")

    @staticmethod
    def _health_status_text(health: int) -> str:
        if health < 30:
            return "Critical"
        if health < 70:
            return "Watch"
        return "Stable"

    def _refresh_accessibility_labels(self) -> None:
        self.watch_inventory(self.inventory)
        self.watch_objectives(self.objectives)
        self.watch_directives(self.directives)
        self.watch_latest_status(self.latest_status)
        self._update_stats_text()
