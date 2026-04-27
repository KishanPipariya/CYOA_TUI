from collections.abc import Mapping
from dataclasses import dataclass

from textual.binding import Binding, InvalidBinding


@dataclass(frozen=True, slots=True)
class AppBindingSpec:
    id: str
    key: str
    action: str
    description: str
    settings_label: str
    settings_section: str
    show: bool = True
    key_display: str | None = None


APP_BINDING_SPECS: tuple[AppBindingSpec, ...] = (
    AppBindingSpec(
        "focus_previous_choice",
        "up",
        "focus_previous_choice",
        "Prev Choice",
        "Previous choice",
        "Navigation",
        show=False,
    ),
    AppBindingSpec(
        "focus_next_choice",
        "down",
        "focus_next_choice",
        "Next Choice",
        "Next choice",
        "Navigation",
        show=False,
    ),
    AppBindingSpec(
        "focus_story_region",
        "shift+s",
        "focus_story_region",
        "Story",
        "Jump to story",
        "Navigation",
        show=False,
    ),
    AppBindingSpec(
        "focus_choices_region",
        "shift+c",
        "focus_choices_region",
        "Choices",
        "Jump to choices",
        "Navigation",
        show=False,
    ),
    AppBindingSpec(
        "focus_status_region",
        "shift+i",
        "focus_status_region",
        "Status",
        "Jump to status",
        "Navigation",
        show=False,
    ),
    AppBindingSpec(
        "focus_journal_region",
        "shift+j",
        "focus_journal_region",
        "Journal",
        "Jump to journal",
        "Navigation",
        show=False,
    ),
    AppBindingSpec(
        "focus_story_map_region",
        "shift+m",
        "focus_story_map_region",
        "Map",
        "Jump to story map",
        "Navigation",
        show=False,
    ),
    AppBindingSpec(
        "focus_notifications_region",
        "shift+n",
        "focus_notifications_region",
        "Notifications",
        "Open notifications",
        "Navigation",
        show=False,
    ),
    AppBindingSpec(
        "toggle_dark",
        "d",
        "toggle_dark",
        "Theme",
        "Toggle dark/light theme",
        "Panels And Help",
    ),
    AppBindingSpec(
        "toggle_journal",
        "j",
        "toggle_journal",
        "Journal",
        "Toggle journal panel",
        "Panels And Help",
    ),
    AppBindingSpec(
        "toggle_story_map",
        "m",
        "toggle_story_map",
        "Map",
        "Toggle story map panel",
        "Panels And Help",
    ),
    AppBindingSpec(
        "show_help",
        "h",
        "show_help",
        "Help",
        "Open help",
        "Panels And Help",
    ),
    AppBindingSpec(
        "repeat_latest_status",
        "n",
        "repeat_latest_status",
        "Repeat Status",
        "Repeat latest status",
        "Panels And Help",
    ),
    AppBindingSpec(
        "show_scene_recap",
        "i",
        "show_scene_recap",
        "Recap",
        "Open scene recap",
        "Panels And Help",
    ),
    AppBindingSpec(
        "show_settings",
        "o",
        "show_settings",
        "Settings",
        "Open settings",
        "Panels And Help",
    ),
    AppBindingSpec(
        "branch_past",
        "b",
        "branch_past",
        "Branch",
        "Branch from past scene",
        "Adventure Actions",
    ),
    AppBindingSpec(
        "undo",
        "u",
        "undo",
        "Undo",
        "Undo last choice",
        "Adventure Actions",
    ),
    AppBindingSpec(
        "redo",
        "y",
        "redo",
        "Redo",
        "Redo last choice",
        "Adventure Actions",
    ),
    AppBindingSpec(
        "create_bookmark",
        "k",
        "create_bookmark",
        "Bookmark",
        "Create bookmark",
        "Adventure Actions",
    ),
    AppBindingSpec(
        "restore_bookmark",
        "p",
        "restore_bookmark",
        "Restore Mark",
        "Restore bookmark",
        "Adventure Actions",
    ),
    AppBindingSpec(
        "save_game",
        "s",
        "save_game",
        "Save",
        "Save game",
        "Adventure Actions",
    ),
    AppBindingSpec(
        "load_game",
        "l",
        "load_game",
        "Load",
        "Load game",
        "Adventure Actions",
    ),
    AppBindingSpec(
        "export_story",
        "e",
        "export_story",
        "Export",
        "Export story",
        "Adventure Actions",
    ),
    AppBindingSpec(
        "request_quit",
        "q",
        "request_quit",
        "Quit",
        "Quit game",
        "Adventure Actions",
    ),
    AppBindingSpec(
        "request_restart",
        "r",
        "request_restart",
        "Restart",
        "Restart adventure",
        "Adventure Actions",
    ),
    AppBindingSpec(
        "toggle_typewriter",
        "t",
        "toggle_typewriter",
        "Typewriter",
        "Toggle typewriter",
        "Adventure Actions",
    ),
    AppBindingSpec(
        "cycle_typewriter_speed",
        "v",
        "cycle_typewriter_speed",
        "Speed",
        "Cycle typewriter speed",
        "Adventure Actions",
    ),
    AppBindingSpec(
        "cycle_generation_preset",
        "g",
        "cycle_generation_preset",
        "Preset",
        "Cycle generation preset",
        "Adventure Actions",
    ),
    AppBindingSpec(
        "edit_directives",
        "x",
        "edit_directives",
        "Directives",
        "Edit directives",
        "Adventure Actions",
    ),
    AppBindingSpec(
        "skip_typewriter",
        "space",
        "skip_typewriter",
        "Skip",
        "Skip typewriter narrator",
        "Adventure Actions",
    ),
    AppBindingSpec(
        "choose_1",
        "1",
        "choose('1')",
        "Choice 1",
        "Select choice 1",
        "Choice Shortcuts",
        show=False,
    ),
    AppBindingSpec(
        "choose_2",
        "2",
        "choose('2')",
        "Choice 2",
        "Select choice 2",
        "Choice Shortcuts",
        show=False,
    ),
    AppBindingSpec(
        "choose_3",
        "3",
        "choose('3')",
        "Choice 3",
        "Select choice 3",
        "Choice Shortcuts",
        show=False,
    ),
    AppBindingSpec(
        "choose_4",
        "4",
        "choose('4')",
        "Choice 4",
        "Select choice 4",
        "Choice Shortcuts",
        show=False,
    ),
)

APP_BINDING_SPEC_BY_ID = {spec.id: spec for spec in APP_BINDING_SPECS}
APP_BINDING_SECTION_ORDER = (
    "Navigation",
    "Panels And Help",
    "Adventure Actions",
    "Choice Shortcuts",
)
KEY_DISPLAY_OVERRIDES = {
    "space": "Space",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "escape": "Esc",
    "enter": "Enter",
    "pagedown": "PageDown",
    "pageup": "PageUp",
}


@dataclass(frozen=True, slots=True)
class KeybindingValidationResult:
    effective_bindings: dict[str, str]
    overrides: dict[str, str]
    invalid_messages: tuple[str, ...]
    conflict_messages: tuple[str, ...]

    @property
    def errors(self) -> tuple[str, ...]:
        return self.invalid_messages + self.conflict_messages


def build_app_bindings() -> list[Binding]:
    return [
        Binding(
            spec.key,
            spec.action,
            spec.description,
            show=spec.show,
            key_display=spec.key_display,
            id=spec.id,
        )
        for spec in APP_BINDING_SPECS
    ]


def default_keybindings() -> dict[str, str]:
    return {spec.id: spec.key for spec in APP_BINDING_SPECS}


def iter_binding_sections() -> tuple[tuple[str, tuple[AppBindingSpec, ...]], ...]:
    return tuple(
        (
            section,
            tuple(spec for spec in APP_BINDING_SPECS if spec.settings_section == section),
        )
        for section in APP_BINDING_SECTION_ORDER
    )


def binding_input_id(binding_id: str) -> str:
    return f"settings-binding-{binding_id}"


def normalize_key_string(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Key binding cannot be empty.")

    try:
        bindings = list(Binding.make_bindings([Binding(cleaned, "noop")]))
    except InvalidBinding as exc:
        raise ValueError(str(exc)) from exc

    return ",".join(binding.key for binding in bindings)


def resolve_keybinding_overrides(overrides: object) -> dict[str, str]:
    if not isinstance(overrides, Mapping):
        return {}

    defaults = default_keybindings()
    resolved: dict[str, str] = {}
    for binding_id, value in overrides.items():
        if binding_id not in APP_BINDING_SPEC_BY_ID or not isinstance(value, str):
            continue
        try:
            normalized = normalize_key_string(value)
        except ValueError:
            continue
        if normalized != defaults[binding_id]:
            resolved[binding_id] = normalized
    return resolved


def effective_keybindings(overrides: object) -> dict[str, str]:
    return {**default_keybindings(), **resolve_keybinding_overrides(overrides)}


def validate_keybindings(raw_values: Mapping[str, str | None]) -> KeybindingValidationResult:
    defaults = default_keybindings()
    effective: dict[str, str] = {}
    invalid_messages: list[str] = []

    for spec in APP_BINDING_SPECS:
        raw_value = raw_values.get(spec.id)
        if raw_value is None or not raw_value.strip():
            effective[spec.id] = defaults[spec.id]
            continue
        try:
            effective[spec.id] = normalize_key_string(raw_value)
        except ValueError as exc:
            invalid_messages.append(f"{spec.settings_label}: {exc}")

    conflicts: dict[str, list[str]] = {}
    for binding_id, key in effective.items():
        conflicts.setdefault(key, []).append(binding_id)

    conflict_messages: list[str] = []
    for key, binding_ids in conflicts.items():
        if len(binding_ids) < 2:
            continue
        labels = ", ".join(
            APP_BINDING_SPEC_BY_ID[binding_id].settings_label for binding_id in binding_ids
        )
        conflict_messages.append(f"{format_key_for_display(key)} is assigned to: {labels}.")

    overrides = {
        binding_id: key for binding_id, key in effective.items() if defaults[binding_id] != key
    }
    return KeybindingValidationResult(
        effective_bindings=effective,
        overrides=overrides,
        invalid_messages=tuple(invalid_messages),
        conflict_messages=tuple(conflict_messages),
    )


def format_key_for_display(key: str) -> str:
    return " / ".join(_format_single_key(part) for part in key.split(","))


def _format_single_key(key: str) -> str:
    formatted = []
    for piece in key.split("+"):
        lowered = piece.lower()
        formatted.append(
            KEY_DISPLAY_OVERRIDES.get(lowered, piece.upper() if len(piece) == 1 else piece.title())
        )
    return "+".join(formatted)
