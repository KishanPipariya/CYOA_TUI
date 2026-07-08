from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from cyoa.core import constants
from cyoa.core.user_config import (
    TerminalAccessibilityFallback,
    build_accessibility_profile_report,
    build_safety_profile_report,
)
from cyoa.ui.dialogs import DialogActions, DialogFrame
from cyoa.ui.keybindings import (
    binding_input_id,
    effective_keybindings,
    iter_binding_sections,
    validate_keybindings,
)

__all__ = [
    "SettingsScreen",
]


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
    .settings-subsection {
        color: $text-muted;
        margin-top: 1;
    }
    .settings-keybinding-row {
        width: 100%;
        height: auto;
        align: left middle;
        margin-bottom: 1;
    }
    .settings-keybinding-label {
        width: 1fr;
        padding-right: 1;
    }
    .settings-keybinding-input {
        width: 24;
    }
    .settings-validation-error {
        color: $error;
    }
    .settings-validation-feedback {
        margin-bottom: 1;
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
        cognitive_load_reduction_mode: bool,
        text_scale: str,
        line_width: str,
        line_spacing: str,
        notification_verbosity: str,
        scene_recap_verbosity: str,
        runtime_metadata_verbosity: str,
        locked_choice_verbosity: str,
        input_timing_profile: str = "default",
        confirm_high_impact_actions: bool = False,
        keybindings: dict[str, str] | None = None,
        typewriter: bool,
        typewriter_speed: str,
        diagnostics_enabled: bool,
        available_themes: list[Any],
        terminal_accessibility_fallback: TerminalAccessibilityFallback | None = None,
        high_contrast: bool = False,
        initial_feedback: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._provider = provider if provider in {"mock", "llama_cpp"} else "mock"
        self._model_path = model_path or ""
        self._theme_options = self._normalize_theme_options(available_themes or [theme])
        self._theme_index = self._resolve_theme_index(theme)
        self._dark = dark
        self._high_contrast = high_contrast
        self._reduced_motion = reduced_motion
        self._screen_reader_mode = screen_reader_mode
        self._cognitive_load_reduction_mode = cognitive_load_reduction_mode
        self._text_scale = text_scale if text_scale in constants.TEXT_SCALE_OPTIONS else "standard"
        self._line_width = (
            line_width if line_width in constants.READING_WIDTH_OPTIONS else "standard"
        )
        self._line_spacing = (
            line_spacing if line_spacing in constants.LINE_SPACING_OPTIONS else "standard"
        )
        self._notification_verbosity = (
            notification_verbosity
            if notification_verbosity in constants.VERBOSITY_OPTIONS
            else "standard"
        )
        self._scene_recap_verbosity = (
            scene_recap_verbosity
            if scene_recap_verbosity in constants.VERBOSITY_OPTIONS
            else "standard"
        )
        self._runtime_metadata_verbosity = (
            runtime_metadata_verbosity
            if runtime_metadata_verbosity in constants.VERBOSITY_OPTIONS
            else "standard"
        )
        self._locked_choice_verbosity = (
            locked_choice_verbosity
            if locked_choice_verbosity in constants.VERBOSITY_OPTIONS
            else "standard"
        )
        self._input_timing_profile = (
            input_timing_profile
            if input_timing_profile in constants.INPUT_TIMING_PROFILE_OPTIONS
            else "default"
        )
        self._confirm_high_impact_actions = confirm_high_impact_actions
        self._effective_keybindings = effective_keybindings(keybindings)
        self._typewriter = typewriter
        self._typewriter_speed = (
            typewriter_speed if typewriter_speed in constants.TYPEWRITER_SPEEDS else "normal"
        )
        self._diagnostics_enabled = diagnostics_enabled
        self._terminal_accessibility_fallback = terminal_accessibility_fallback
        self._initial_feedback = initial_feedback.strip()

    @staticmethod
    def _normalize_theme_options(available_themes: list[Any]) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        for entry in available_themes:
            if isinstance(entry, str):
                theme_id = entry.strip()
                if not theme_id:
                    continue
                normalized.append(
                    {
                        "id": theme_id,
                        "name": theme_id.replace("_", " ").title(),
                        "description": "",
                        "campaign_name": "",
                        "campaign_description": "",
                    }
                )
                continue
            if not isinstance(entry, dict):
                continue
            theme_id = str(entry.get("id") or "").strip()
            if not theme_id:
                continue
            normalized.append(
                {
                    "id": theme_id,
                    "name": str(entry.get("name") or theme_id).strip() or theme_id,
                    "description": str(entry.get("description") or "").strip(),
                    "campaign_name": str(entry.get("campaign_name") or "").strip(),
                    "campaign_description": str(entry.get("campaign_description") or "").strip(),
                }
            )
        return normalized or [
            {
                "id": "dark_dungeon",
                "name": "Dark Dungeon",
                "description": "",
                "campaign_name": "",
                "campaign_description": "",
            }
        ]

    def _resolve_theme_index(self, theme: str) -> int:
        for index, option in enumerate(self._theme_options):
            if option["id"] == theme:
                return index
        self._theme_options = [
            {
                "id": theme,
                "name": theme.replace("_", " ").title(),
                "description": "",
                "campaign_name": "",
                "campaign_description": "",
            },
            *self._theme_options,
        ]
        return 0

    @property
    def _current_theme(self) -> str:
        return self._current_theme_option["id"]

    @property
    def _theme_names(self) -> list[str]:
        return [option["id"] for option in self._theme_options]

    @property
    def _current_theme_option(self) -> dict[str, str]:
        return self._theme_options[self._theme_index]

    def compose(self) -> ComposeResult:
        with DialogFrame(
            id="settings-dialog", classes="dialog-frame dialog-frame-scroll dialog-frame-accent"
        ):
            yield Static("SETTINGS", id="settings-kicker")
            yield Label("[b]Adventure Settings[/b]", classes="dialog-title")
            yield Static(
                "Dark mode and typewriter updates apply immediately. Runtime provider, model path, story pack, and diagnostics apply on restart.",
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
                "",
                id="settings-model-path-feedback",
                classes="settings-value settings-validation-feedback",
            )
            yield Label(
                "Used on next restart when Local Model is selected. Leave blank to keep demo mode safe.",
                classes="settings-value",
            )

            yield Label("Story Pack", classes="settings-label")
            with Horizontal(classes="settings-row settings-section"):
                yield Button("Previous", id="btn-settings-theme-prev")
                yield Button("Next", id="btn-settings-theme-next")
            yield Label("", id="settings-theme-value", classes="settings-value")
            yield Label("", id="settings-theme-summary", classes="settings-value")

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

            yield Label("Cognitive Load", classes="settings-label")
            with Horizontal(classes="settings-row settings-section"):
                yield Button("Standard", id="btn-settings-cognitive-standard")
                yield Button("Reduced", id="btn-settings-cognitive-reduced")
            yield Label(
                "Reduced mode simplifies wording and hides lower-priority status detail so the story stays central.",
                classes="settings-value",
            )
            yield Label("Accessibility Profile", classes="settings-label")
            yield Label("", id="settings-accessibility-summary", classes="settings-value")
            yield Label(
                "",
                id="settings-accessibility-advisories",
                classes="settings-value settings-validation-feedback",
            )

            yield Label("Input Timing", classes="settings-label")
            with Horizontal(classes="settings-row settings-section"):
                yield Button("Default", id="btn-settings-input-timing-default")
                yield Button("Gentle", id="btn-settings-input-timing-gentle")
                yield Button("Steady", id="btn-settings-input-timing-steady")
            yield Label(
                "Gentle and Steady slow repeated focus movement and choice activation to reduce accidental repeats.",
                classes="settings-value",
            )

            yield Label("Protected Actions", classes="settings-label")
            with Horizontal(classes="settings-row settings-section"):
                yield Button("Standard", id="btn-settings-confirm-standard")
                yield Button("Expanded", id="btn-settings-confirm-expanded")
            yield Label(
                "Expanded confirmations also protect loading a save, restoring a checkpoint, branching from history, and starting over from an ending screen.",
                classes="settings-value",
            )

            yield Label("Safety Profile", classes="settings-label")
            yield Label("", id="settings-safety-summary", classes="settings-value")
            yield Label(
                "",
                id="settings-safety-advisories",
                classes="settings-value settings-validation-feedback",
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

            yield Label("Verbosity", classes="settings-label")
            yield Label(
                "Set how much detail appears per surface. Screen Reader Friendly keeps plain wording, and Cognitive Load Reduction can still hide lower-priority runtime metadata.",
                classes="settings-value",
            )

            yield Label("Notifications", classes="settings-label settings-subsection")
            with Horizontal(classes="settings-row settings-section"):
                yield Button("Minimal", id="btn-settings-notification-verbosity-minimal")
                yield Button("Standard", id="btn-settings-notification-verbosity-standard")
                yield Button("Detailed", id="btn-settings-notification-verbosity-detailed")

            yield Label("Scene Recap", classes="settings-label settings-subsection")
            with Horizontal(classes="settings-row settings-section"):
                yield Button("Minimal", id="btn-settings-recap-verbosity-minimal")
                yield Button("Standard", id="btn-settings-recap-verbosity-standard")
                yield Button("Detailed", id="btn-settings-recap-verbosity-detailed")

            yield Label("Runtime Metadata", classes="settings-label settings-subsection")
            with Horizontal(classes="settings-row settings-section"):
                yield Button("Minimal", id="btn-settings-runtime-verbosity-minimal")
                yield Button("Standard", id="btn-settings-runtime-verbosity-standard")
                yield Button("Detailed", id="btn-settings-runtime-verbosity-detailed")

            yield Label("Locked Choices", classes="settings-label settings-subsection")
            with Horizontal(classes="settings-row settings-section"):
                yield Button("Minimal", id="btn-settings-locked-choice-verbosity-minimal")
                yield Button("Standard", id="btn-settings-locked-choice-verbosity-standard")
                yield Button("Detailed", id="btn-settings-locked-choice-verbosity-detailed")

            yield Label("Key Bindings", classes="settings-label")
            yield Label(
                "Edit the keys for major actions here. Leave a field blank to restore its default key. Conflicts block saving.",
                classes="settings-value",
            )
            for section_title, specs in iter_binding_sections():
                yield Label(section_title, classes="settings-label settings-subsection")
                for spec in specs:
                    with Horizontal(classes="settings-keybinding-row"):
                        yield Label(spec.settings_label, classes="settings-keybinding-label")
                        yield Input(
                            value=self._effective_keybindings.get(spec.id, spec.key),
                            placeholder=spec.key,
                            id=binding_input_id(spec.id),
                            classes="settings-keybinding-input",
                        )
            yield Label("", id="settings-keybindings-feedback", classes="settings-value")

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
                yield Button("Capture Snapshot", id="btn-settings-capture-snapshot")
                yield Button("Reveal Saves", id="btn-settings-reveal-saves")
            with Horizontal(classes="settings-row"):
                yield Button("Reset Settings", id="btn-settings-reset", variant="warning")
            yield Label(
                "Use these tools to verify your configured backend, capture a redacted diagnostics snapshot, open the save folder, or return to safe defaults.",
                classes="settings-value",
            )
            yield Label(
                self._initial_feedback,
                id="settings-feedback",
                classes="settings-value settings-validation-feedback",
            )

            with DialogActions(id="settings-actions", classes="dialog-actions"):
                yield Button("Save", id="btn-settings-save", variant="primary")
                yield Button("Cancel", id="btn-settings-cancel", variant="error")

    def on_mount(self) -> None:
        self._refresh_state()
        self._set_model_path_feedback("")
        self._set_settings_feedback(self._initial_feedback)
        self.query_one("#settings-model-path", Input).focus()

    def _set_selected(self, button_id: str, selected: bool) -> None:
        button = self.query_one(f"#{button_id}", Button)
        button.variant = "primary" if selected else "default"

    def _refresh_state(self) -> None:
        self._set_selected("btn-settings-provider-mock", self._provider == "mock")
        self._set_selected("btn-settings-provider-llama", self._provider == "llama_cpp")
        self.query_one("#settings-provider-value", Label).update(
            "Quick Demo keeps startup safe."
            if self._provider == "mock"
            else "Use a saved GGUF on restart."
        )

        self._set_selected("btn-settings-dark-on", self._dark)
        self._set_selected("btn-settings-dark-off", not self._dark)
        self._set_selected("btn-settings-contrast-standard", not self._high_contrast)
        self._set_selected("btn-settings-contrast-high", self._high_contrast)
        self._set_selected("btn-settings-motion-standard", not self._reduced_motion)
        self._set_selected("btn-settings-motion-reduced", self._reduced_motion)
        self._set_selected("btn-settings-screen-reader-on", self._screen_reader_mode)
        self._set_selected("btn-settings-screen-reader-off", not self._screen_reader_mode)
        self._set_selected(
            "btn-settings-cognitive-standard",
            not self._cognitive_load_reduction_mode,
        )
        self._set_selected(
            "btn-settings-cognitive-reduced",
            self._cognitive_load_reduction_mode,
        )
        for scale in constants.TEXT_SCALE_OPTIONS:
            self._set_selected(f"btn-settings-scale-{scale}", self._text_scale == scale)
        for width in constants.READING_WIDTH_OPTIONS:
            self._set_selected(f"btn-settings-width-{width}", self._line_width == width)
        for spacing in constants.LINE_SPACING_OPTIONS:
            self._set_selected(
                f"btn-settings-spacing-{spacing}",
                self._line_spacing == spacing,
            )
        for verbosity in constants.VERBOSITY_OPTIONS:
            self._set_selected(
                f"btn-settings-notification-verbosity-{verbosity}",
                self._notification_verbosity == verbosity,
            )
            self._set_selected(
                f"btn-settings-recap-verbosity-{verbosity}",
                self._scene_recap_verbosity == verbosity,
            )
            self._set_selected(
                f"btn-settings-runtime-verbosity-{verbosity}",
                self._runtime_metadata_verbosity == verbosity,
            )
            self._set_selected(
                f"btn-settings-locked-choice-verbosity-{verbosity}",
                self._locked_choice_verbosity == verbosity,
            )
        for profile in constants.INPUT_TIMING_PROFILE_OPTIONS:
            self._set_selected(
                f"btn-settings-input-timing-{profile}",
                self._input_timing_profile == profile,
            )
        self._set_selected("btn-settings-confirm-standard", not self._confirm_high_impact_actions)
        self._set_selected("btn-settings-confirm-expanded", self._confirm_high_impact_actions)
        self._set_selected("btn-settings-typewriter-on", self._typewriter)
        self._set_selected("btn-settings-typewriter-off", not self._typewriter)

        for speed in constants.TYPEWRITER_SPEEDS:
            self._set_selected(
                f"btn-settings-speed-{speed}",
                self._typewriter_speed == speed,
            )

        self._set_selected("btn-settings-diagnostics-on", self._diagnostics_enabled)
        self._set_selected("btn-settings-diagnostics-off", not self._diagnostics_enabled)
        current_theme = self._current_theme_option
        theme_label = f"{current_theme['name']} id: {current_theme['id']}"
        self.query_one("#settings-theme-value", Label).update(
            f"{theme_label} ({self._theme_index + 1}/{len(self._theme_options)})"
        )
        theme_summary = current_theme["description"] or "No pack description is available."
        if current_theme["campaign_name"]:
            theme_summary = (
                f"{theme_summary} Campaign: {current_theme['campaign_name']}. "
                f"{current_theme['campaign_description'] or 'Includes chapter-based progression.'}"
            )
        else:
            theme_summary = f"{theme_summary} Standalone adventure."
        self.query_one("#settings-theme-summary", Label).update(theme_summary)
        self._refresh_accessibility_profile_report()
        self._refresh_safety_profile_report()

    def _effective_accessibility_state(self) -> dict[str, bool]:
        effective = {
            "high_contrast": self._high_contrast,
            "reduced_motion": self._reduced_motion,
            "screen_reader_mode": self._screen_reader_mode,
        }
        if self._terminal_accessibility_fallback is None:
            return effective

        for key, enabled in self._terminal_accessibility_fallback.overrides.items():
            if enabled:
                effective[key] = True
        return effective

    def _refresh_accessibility_profile_report(self) -> None:
        effective = self._effective_accessibility_state()
        report = build_accessibility_profile_report(
            high_contrast=effective["high_contrast"],
            reduced_motion=effective["reduced_motion"],
            screen_reader_mode=effective["screen_reader_mode"],
            cognitive_load_reduction_mode=self._cognitive_load_reduction_mode,
            text_scale=self._text_scale,
            line_width=self._line_width,
            line_spacing=self._line_spacing,
            runtime_metadata_verbosity=self._runtime_metadata_verbosity,
            typewriter=self._typewriter,
            terminal_fallback=self._terminal_accessibility_fallback,
        )
        self.query_one("#settings-accessibility-summary", Label).update(report.summary)
        advisories = self.query_one("#settings-accessibility-advisories", Label)
        advisories.update(" ".join(report.advisory_lines))
        advisories.set_class(bool(report.advisory_lines), "settings-validation-error")

    def _refresh_safety_profile_report(self) -> None:
        report = build_safety_profile_report(
            input_timing_profile=self._input_timing_profile,
            confirm_high_impact_actions=self._confirm_high_impact_actions,
        )
        self.query_one("#settings-safety-summary", Label).update(report.summary)
        advisories = self.query_one("#settings-safety-advisories", Label)
        advisories.update(" ".join(report.advisory_lines))
        advisories.set_class(bool(report.advisory_lines), "settings-validation-error")

    def _dismiss_with_value(self) -> None:
        validation = validate_keybindings(self._collect_keybinding_values())
        if validation.errors:
            self._set_keybinding_feedback(" ".join(validation.errors))
            return

        self._set_keybinding_feedback("")
        payload = self._build_settings_payload(validation.overrides, require_model_path=True)
        if payload is None:
            return
        self.dismiss(payload)

    def _collect_keybinding_values(self) -> dict[str, str]:
        collected: dict[str, str] = {}
        for _section_title, specs in iter_binding_sections():
            for spec in specs:
                collected[spec.id] = self.query_one(f"#{binding_input_id(spec.id)}", Input).value
        return collected

    def _set_keybinding_feedback(self, message: str) -> None:
        feedback = self.query_one("#settings-keybindings-feedback", Label)
        feedback.update(message)
        feedback.set_class(bool(message), "settings-validation-error")

    def _set_model_path_feedback(self, message: str) -> None:
        feedback = self.query_one("#settings-model-path-feedback", Label)
        feedback.update(message)
        feedback.set_class(bool(message), "settings-validation-error")

    def _set_settings_feedback(self, message: str) -> None:
        feedback = self.query_one("#settings-feedback", Label)
        feedback.update(message)
        feedback.set_class(bool(message), "settings-validation-error")

    def _resolve_model_path(self, *, require_model_path: bool) -> str | None:
        raw_value = self.query_one("#settings-model-path", Input).value.strip()
        if self._provider != "llama_cpp":
            self._set_model_path_feedback("")
            return None
        if not raw_value:
            if require_model_path:
                self._set_model_path_feedback(
                    "Enter a GGUF file path or switch Runtime Provider to Quick Demo."
                )
            return None

        candidate = Path(raw_value).expanduser()
        if candidate.suffix.lower() != ".gguf":
            self._set_model_path_feedback("Local Model requires a `.gguf` file.")
            return None
        if not candidate.exists():
            self._set_model_path_feedback(f"Model path was not found: {candidate}")
            return None
        if not candidate.is_file():
            self._set_model_path_feedback(f"Model path must point to a file: {candidate}")
            return None

        self._set_model_path_feedback("")
        return str(candidate)

    def _build_settings_payload(
        self,
        keybinding_overrides: dict[str, str],
        *,
        require_model_path: bool,
    ) -> dict[str, Any] | None:
        self._set_settings_feedback("")
        model_path = self._resolve_model_path(require_model_path=require_model_path)
        if self._provider == "llama_cpp" and require_model_path and model_path is None:
            self.query_one("#settings-model-path", Input).focus()
            return None
        return {
            "provider": self._provider,
            "model_path": model_path,
            "theme": self._current_theme,
            "dark": self._dark,
            "high_contrast": self._high_contrast,
            "reduced_motion": self._reduced_motion,
            "screen_reader_mode": self._screen_reader_mode,
            "cognitive_load_reduction_mode": self._cognitive_load_reduction_mode,
            "text_scale": self._text_scale,
            "line_width": self._line_width,
            "line_spacing": self._line_spacing,
            "notification_verbosity": self._notification_verbosity,
            "scene_recap_verbosity": self._scene_recap_verbosity,
            "runtime_metadata_verbosity": self._runtime_metadata_verbosity,
            "locked_choice_verbosity": self._locked_choice_verbosity,
            "input_timing_profile": self._input_timing_profile,
            "confirm_high_impact_actions": self._confirm_high_impact_actions,
            "keybindings": keybinding_overrides,
            "typewriter": self._typewriter,
            "typewriter_speed": self._typewriter_speed,
            "diagnostics_enabled": self._diagnostics_enabled,
        }

    def on_button_pressed(self, event: Button.Pressed) -> None:  # noqa: C901
        button_id = event.button.id
        if button_id == "btn-settings-save":
            self._dismiss_with_value()
            return
        if button_id == "btn-settings-cancel":
            self.dismiss(None)
            return
        if button_id == "btn-settings-test-backend":
            payload = self._build_settings_payload(
                validate_keybindings(self._collect_keybinding_values()).overrides,
                require_model_path=False,
            )
            if payload is None:
                return
            self.dismiss({"action": "test_backend", "draft_settings": payload})
            return
        if button_id == "btn-settings-capture-snapshot":
            self.dismiss({"action": "capture_accessibility_snapshot"})
            return
        if button_id == "btn-settings-reveal-saves":
            self.dismiss({"action": "reveal_saves"})
            return
        if button_id == "btn-settings-reset":
            self.dismiss({"action": "reset_settings"})
            return
        self._set_settings_feedback("")
        if button_id == "btn-settings-provider-mock":
            self._provider = "mock"
        elif button_id == "btn-settings-provider-llama":
            self._provider = "llama_cpp"
        elif button_id == "btn-settings-theme-prev":
            self._theme_index = (self._theme_index - 1) % len(self._theme_options)
        elif button_id == "btn-settings-theme-next":
            self._theme_index = (self._theme_index + 1) % len(self._theme_options)
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
        elif button_id == "btn-settings-cognitive-standard":
            self._cognitive_load_reduction_mode = False
        elif button_id == "btn-settings-cognitive-reduced":
            self._cognitive_load_reduction_mode = True
        elif button_id and button_id.startswith("btn-settings-scale-"):
            self._text_scale = button_id.removeprefix("btn-settings-scale-")
        elif button_id and button_id.startswith("btn-settings-width-"):
            self._line_width = button_id.removeprefix("btn-settings-width-")
        elif button_id and button_id.startswith("btn-settings-spacing-"):
            self._line_spacing = button_id.removeprefix("btn-settings-spacing-")
        elif button_id and button_id.startswith("btn-settings-notification-verbosity-"):
            self._notification_verbosity = button_id.removeprefix(
                "btn-settings-notification-verbosity-"
            )
        elif button_id and button_id.startswith("btn-settings-recap-verbosity-"):
            self._scene_recap_verbosity = button_id.removeprefix("btn-settings-recap-verbosity-")
        elif button_id and button_id.startswith("btn-settings-runtime-verbosity-"):
            self._runtime_metadata_verbosity = button_id.removeprefix(
                "btn-settings-runtime-verbosity-"
            )
        elif button_id and button_id.startswith("btn-settings-locked-choice-verbosity-"):
            self._locked_choice_verbosity = button_id.removeprefix(
                "btn-settings-locked-choice-verbosity-"
            )
        elif button_id and button_id.startswith("btn-settings-input-timing-"):
            self._input_timing_profile = button_id.removeprefix("btn-settings-input-timing-")
        elif button_id == "btn-settings-confirm-standard":
            self._confirm_high_impact_actions = False
        elif button_id == "btn-settings-confirm-expanded":
            self._confirm_high_impact_actions = True
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

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id and event.input.id.startswith("settings-binding-"):
            self._set_keybinding_feedback("")
            self._set_settings_feedback("")
            return
        if event.input.id == "settings-model-path":
            self._set_model_path_feedback("")
            self._set_settings_feedback("")

    def action_save(self) -> None:
        self._dismiss_with_value()

    def action_cancel(self) -> None:
        self.dismiss(None)
