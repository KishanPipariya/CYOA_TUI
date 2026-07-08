from typing import Any, cast

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.markup import escape
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListView, ProgressBar, Static

from cyoa.core.model_download import DownloadProgress, ModelRecommendation
from cyoa.core.user_config import (
    FIRST_RUN_ACCESSIBILITY_PRESET_OPTIONS,
    StartupAccessibilityRecommendation,
    accessibility_preset_overrides,
)
from cyoa.ui.keybindings import CommandPaletteEntry, search_command_palette
from cyoa.ui.presenters import build_branch_preview, format_save_display_name
from cyoa.ui.widgets import CommandPaletteListItem, OptionListItem, SaveListItem, SceneListItem

__all__ = [
    "DialogFrame",
    "DialogActions",
    "ButtonGroupScreen",
    "BranchScreen",
    "ConfirmScreen",
    "CommandPaletteScreen",
    "LoadGameScreen",
    "OptionListScreen",
    "StartupChoiceScreen",
    "FirstRunSetupScreen",
    "StartupAccessibilityRecommendationScreen",
    "ModelDownloadScreen",
    "TextPromptScreen",
]


class DialogFrame(Container):
    """Reusable modal dialog shell."""


class DialogActions(Horizontal):
    """Reusable modal action row."""


class ButtonGroupScreen(ModalScreen[Any]):
    """Modal helper that provides keyboard-first button group navigation."""

    def _action_buttons(self) -> list[Button]:
        return [button for button in self.query(Button) if not button.disabled]

    def _focus_first_action_button(self) -> None:
        def apply_focus() -> None:
            buttons = self._action_buttons()
            if not buttons:
                return
            if isinstance(self.focused, Button) and self.focused in buttons:
                return
            buttons[0].focus()

        self.call_after_refresh(apply_focus)

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


class CommandPaletteScreen(ModalScreen[str]):
    """Searchable command launcher for keyboard-first action discovery."""

    DEFAULT_CSS = """
    CommandPaletteScreen {
        align: center middle;
        background: $background 80%;
    }
    #command-palette-dialog {
        width: 84;
        height: 80%;
        max-width: 96%;
    }
    #command-palette-search {
        margin: 1 0;
    }
    #command-palette-list {
        height: 1fr;
    }
    #btn-command-palette-close {
        width: 100%;
        margin-top: 1;
    }
    """

    BINDINGS = [
        ("escape", "close", "Close"),
        ("down", "focus_results", "Results"),
        ("ctrl+n", "focus_results", "Results"),
        ("up", "focus_search", "Search"),
        ("ctrl+p", "focus_search", "Search"),
    ]

    def __init__(self, entries: tuple[CommandPaletteEntry, ...], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._entries = entries

    def compose(self) -> ComposeResult:
        with DialogFrame(
            id="command-palette-dialog",
            classes="dialog-frame dialog-frame-scroll dialog-frame-accent",
        ):
            yield Label(
                "[b]Command Palette[/b]", id="command-palette-title", classes="dialog-title"
            )
            yield Label(
                "Search actions, settings, and help text. Press Enter to run the top match.",
                id="command-palette-kicker",
                classes="dialog-entry",
            )
            yield Input(placeholder="Search commands", id="command-palette-search")
            yield ListView(id="command-palette-list", classes="dialog-list")
            yield Button("Close [b](Esc)[/b]", id="btn-command-palette-close", variant="primary")

    def on_mount(self) -> None:
        self._refresh_results()
        self.query_one("#command-palette-search", Input).focus()

    def _refresh_results(self) -> None:
        query = self.query_one("#command-palette-search", Input).value
        matches = search_command_palette(self._entries, query)
        list_view = self.query_one("#command-palette-list", ListView)
        list_view.clear()
        if not matches:
            list_view.append(
                CommandPaletteListItem(
                    Label("No matching commands.", classes="dialog-entry"),
                    action_value="",
                )
            )
            return

        for entry in matches:
            label = self._format_entry_label(entry)
            list_view.append(
                CommandPaletteListItem(
                    Label(escape(label), classes="dialog-entry"),
                    action_value=entry.action,
                )
            )
        list_view.index = 0

    @staticmethod
    def _format_entry_label(entry: CommandPaletteEntry) -> str:
        return f"{entry.title} ({entry.keybinding})\n{entry.description} [{entry.section}]"

    def _dismiss_top_result(self) -> None:
        list_view = self.query_one("#command-palette-list", ListView)
        for item in list_view.query(CommandPaletteListItem):
            if item.action_value:
                self.dismiss(item.action_value)
                return

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "command-palette-search":
            self._refresh_results()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "command-palette-search":
            self._dismiss_top_result()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, CommandPaletteListItem) and event.item.action_value:
            self.dismiss(event.item.action_value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-command-palette-close":
            self.dismiss(None)

    def action_focus_results(self) -> None:
        self.query_one("#command-palette-list", ListView).focus()

    def action_focus_search(self) -> None:
        self.query_one("#command-palette-search", Input).focus()

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
            yield Label(
                "[b]Load Game[/b] \u2014 Select a save file",
                id="load-title",
                classes="dialog-title",
            )
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
        self.call_after_refresh(
            list_view.focus
            if self._save_files
            else self.query_one("#btn-load-cancel", Button).focus
        )

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

    def __init__(
        self, title: str, options: list[str], *, empty_message: str, **kwargs: Any
    ) -> None:
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
            list_view.append(
                OptionListItem(Label(self._empty_message, classes="dialog-entry"), option_value="")
            )
        else:
            for option in self._options:
                list_view.append(
                    OptionListItem(Label(option, classes="dialog-entry"), option_value=option)
                )
        self.call_after_refresh(
            list_view.focus if self._options else self.query_one("#btn-load-cancel", Button).focus
        )

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
                yield Button(
                    "[b]R[/b]esume Previous Save", id="btn-startup-resume", variant="primary"
                )
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
        ("1", "select_default_preset", "Default Preset"),
        ("2", "select_high_contrast_preset", "High Contrast Preset"),
        ("3", "select_reduced_motion_preset", "Reduced Motion Preset"),
        ("4", "select_screen_reader_preset", "Screen Reader Preset"),
        ("tab", "focus_next_button", "Next"),
        ("shift+tab", "focus_previous_button", "Previous"),
        ("up", "focus_previous_button", "Previous"),
        ("down", "focus_next_button", "Next"),
    ]

    def __init__(
        self,
        *,
        general_notes: tuple[str, ...] = (),
        selected_accessibility_preset: str = "default",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._general_notes = general_notes
        self._accessibility_preset = (
            selected_accessibility_preset
            if selected_accessibility_preset in FIRST_RUN_ACCESSIBILITY_PRESET_OPTIONS
            else "default"
        )

    @staticmethod
    def _preset_title(preset: str) -> str:
        return {
            "default": "Default",
            "high_contrast": "High Contrast",
            "reduced_motion": "Reduced Motion",
            "screen_reader_friendly": "Screen Reader Friendly",
        }.get(preset, "Default")

    @staticmethod
    def _preset_note(preset: str) -> str:
        if preset == "high_contrast":
            return "Locks in the high-contrast palette for clearer focus, disabled, warning, and error states."
        if preset == "reduced_motion":
            return "Disables motion-heavy effects and makes narrated text appear instantly."
        if preset == "screen_reader_friendly":
            return "Removes decorative output, keeps plain status text visible, and also enables reduced motion."
        return "Keeps the standard visual defaults. You can adjust accessibility settings later."

    def _set_selected(self, button_id: str, selected: bool) -> None:
        self.query_one(f"#{button_id}", Button).variant = "primary" if selected else "default"

    def _refresh_accessibility_preset_state(self) -> None:
        for preset in FIRST_RUN_ACCESSIBILITY_PRESET_OPTIONS:
            self._set_selected(
                f"btn-first-run-preset-{preset}",
                self._accessibility_preset == preset,
            )
        preset_title = self._preset_title(self._accessibility_preset)
        preset_note = self._preset_note(self._accessibility_preset)
        overrides = accessibility_preset_overrides(self._accessibility_preset)
        enabled = [
            label
            for key, label in (
                ("high_contrast", "High Contrast"),
                ("reduced_motion", "Reduced Motion"),
                ("screen_reader_mode", "Screen Reader Friendly"),
            )
            if overrides[key]
        ]
        summary = (
            "No accessibility overrides enabled."
            if not enabled
            else "Enables: " + ", ".join(enabled) + "."
        )
        self.query_one("#first-run-preset-value", Label).update(f"{preset_title}: {preset_note}")
        self.query_one("#first-run-preset-summary", Label).update(summary)

    def _select_accessibility_preset(self, preset: str) -> None:
        if preset not in FIRST_RUN_ACCESSIBILITY_PRESET_OPTIONS:
            return
        self._accessibility_preset = preset
        if self.is_mounted:
            self._refresh_accessibility_preset_state()

    def _build_selection(self, runtime_choice: str) -> dict[str, str]:
        return {
            "runtime": runtime_choice,
            "accessibility_preset": self._accessibility_preset,
        }

    def compose(self) -> ComposeResult:
        with DialogFrame(
            id="first-run-dialog", classes="dialog-frame dialog-frame-accent dialog-frame-scroll"
        ):
            yield Static("FIRST RUN SETUP", id="first-run-kicker")
            yield Label("[b]Choose How to Start[/b]", id="first-run-title", classes="dialog-title")
            yield Static(
                "Pick a runtime path before the adventure begins. This choice is saved for later launches.",
                id="first-run-message",
                classes="dialog-message",
            )
            yield Label("Accessibility Preset", classes="settings-label")
            with Horizontal(classes="settings-row settings-section"):
                yield Button("Default", id="btn-first-run-preset-default")
                yield Button("High Contrast", id="btn-first-run-preset-high_contrast")
            with Horizontal(classes="settings-row"):
                yield Button("Reduced Motion", id="btn-first-run-preset-reduced_motion")
                yield Button(
                    "Screen Reader Friendly",
                    id="btn-first-run-preset-screen_reader_friendly",
                )
            yield Label("", id="first-run-preset-value", classes="settings-value")
            yield Label("", id="first-run-preset-summary", classes="first-run-note")
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
        button_id = event.button.id
        if button_id == "btn-first-run-mock":
            self.dismiss(self._build_selection("mock"))
        elif button_id == "btn-first-run-download":
            self.dismiss(self._build_selection("download"))
        elif button_id and button_id.startswith("btn-first-run-preset-"):
            self._select_accessibility_preset(button_id.removeprefix("btn-first-run-preset-"))

    def on_mount(self) -> None:
        self._refresh_accessibility_preset_state()
        self._focus_first_action_button()

    def action_quick_demo(self) -> None:
        self.dismiss(self._build_selection("mock"))

    def action_download_model(self) -> None:
        self.dismiss(self._build_selection("download"))

    def action_select_default_preset(self) -> None:
        self._select_accessibility_preset("default")

    def action_select_high_contrast_preset(self) -> None:
        self._select_accessibility_preset("high_contrast")

    def action_select_reduced_motion_preset(self) -> None:
        self._select_accessibility_preset("reduced_motion")

    def action_select_screen_reader_preset(self) -> None:
        self._select_accessibility_preset("screen_reader_friendly")


class StartupAccessibilityRecommendationScreen(ButtonGroupScreen):
    """Modal that suggests accessibility defaults for constrained startup environments."""

    DEFAULT_CSS = """
    StartupAccessibilityRecommendationScreen {
        align: center middle;
        background: $background 80%;
    }
    #startup-accessibility-dialog {
        width: 82;
        max-width: 94%;
    }
    #startup-accessibility-actions {
        width: 100%;
        margin-top: 1;
    }
    #startup-accessibility-actions Button {
        width: 1fr;
    }
    .startup-accessibility-note {
        color: $text-muted;
    }
    """

    BINDINGS = [
        ("a", "accept", "Accept"),
        ("d", "dismiss_recommendation", "Dismiss"),
        ("l", "later", "Later"),
        ("tab", "focus_next_button", "Next"),
        ("shift+tab", "focus_previous_button", "Previous"),
        ("left", "focus_previous_button", "Previous"),
        ("right", "focus_next_button", "Next"),
        ("escape", "later", "Later"),
    ]

    def __init__(self, recommendation: StartupAccessibilityRecommendation, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._recommendation = recommendation

    @staticmethod
    def _preset_label(preset: str) -> str:
        return {
            "high_contrast": "High Contrast",
            "reduced_motion": "Reduced Motion",
            "screen_reader_friendly": "Screen Reader Friendly",
        }.get(preset, "Accessibility Preset")

    def compose(self) -> ComposeResult:
        preset_label = self._preset_label(self._recommendation.accessibility_preset)
        with DialogFrame(
            id="startup-accessibility-dialog",
            classes="dialog-frame dialog-frame-accent dialog-frame-scroll",
        ):
            yield Static("ACCESSIBILITY RECOMMENDATION", id="startup-accessibility-kicker")
            yield Label(
                f"[b]{escape(self._recommendation.title)}[/b]",
                classes="dialog-title",
            )
            yield Static(self._recommendation.message, classes="dialog-message")
            for reason in self._recommendation.reasons:
                yield Label(reason, classes="startup-accessibility-note")
            if self._recommendation.rescue_mode_active:
                yield Label(
                    "Compact rescue mode will stay active while the terminal remains narrow.",
                    classes="startup-accessibility-note",
                )
            yield Label(
                "Accept applies the recommendation now. Dismiss hides this suggestion for the same startup condition. Later skips it for this launch.",
                classes="startup-accessibility-note",
            )
            with DialogActions(
                id="startup-accessibility-actions",
                classes="dialog-actions",
            ):
                yield Button(
                    f"[b]A[/b]pply {preset_label}",
                    id="btn-startup-accessibility-accept",
                    variant="primary",
                )
                yield Button(
                    "[b]D[/b]ismiss",
                    id="btn-startup-accessibility-dismiss",
                    variant="default",
                )
                yield Button(
                    "[b]L[/b]ater",
                    id="btn-startup-accessibility-later",
                    variant="default",
                )

    def on_mount(self) -> None:
        self._focus_first_action_button()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-startup-accessibility-accept":
            self.dismiss("accept")
        elif event.button.id == "btn-startup-accessibility-dismiss":
            self.dismiss("dismiss")
        elif event.button.id == "btn-startup-accessibility-later":
            self.dismiss("later")

    def action_accept(self) -> None:
        self.dismiss("accept")

    def action_dismiss_recommendation(self) -> None:
        self.dismiss("dismiss")

    def action_later(self) -> None:
        self.dismiss("later")


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
            yield Label("[b]Download a Recommended Model[/b]", classes="dialog-title")
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
            yield ProgressBar(
                total=100, show_percentage=True, show_eta=False, id="model-download-progress"
            )
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
        self.query_one("#model-download-stage", Label).update("Canceling")
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

    def __init__(
        self, title: str, *, value: str = "", placeholder: str = "", **kwargs: Any
    ) -> None:
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
