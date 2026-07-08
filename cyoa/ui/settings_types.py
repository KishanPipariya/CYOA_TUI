from typing import Literal, Required, TypedDict

SettingsAction = Literal[
    "test_backend",
    "capture_accessibility_snapshot",
    "reveal_saves",
    "reset_settings",
]


class SettingsDraft(TypedDict, total=False):
    provider: str
    model_path: str | None
    theme: str
    dark: bool
    high_contrast: bool
    reduced_motion: bool
    screen_reader_mode: bool
    cognitive_load_reduction_mode: bool
    text_scale: str
    line_width: str
    line_spacing: str
    notification_verbosity: str
    scene_recap_verbosity: str
    runtime_metadata_verbosity: str
    locked_choice_verbosity: str
    input_timing_profile: str
    confirm_high_impact_actions: bool
    keybindings: dict[str, str]
    typewriter: bool
    typewriter_speed: str
    diagnostics_enabled: bool


class SettingsPayload(TypedDict):
    provider: str
    model_path: str | None
    theme: str
    dark: bool
    high_contrast: bool
    reduced_motion: bool
    screen_reader_mode: bool
    cognitive_load_reduction_mode: bool
    text_scale: str
    line_width: str
    line_spacing: str
    notification_verbosity: str
    scene_recap_verbosity: str
    runtime_metadata_verbosity: str
    locked_choice_verbosity: str
    input_timing_profile: str
    confirm_high_impact_actions: bool
    keybindings: dict[str, str]
    typewriter: bool
    typewriter_speed: str
    diagnostics_enabled: bool


class SettingsActionPayload(TypedDict, total=False):
    action: Required[SettingsAction]
    draft_settings: SettingsPayload


SettingsResult = SettingsPayload | SettingsActionPayload


__all__ = [
    "SettingsAction",
    "SettingsActionPayload",
    "SettingsDraft",
    "SettingsPayload",
    "SettingsResult",
]
