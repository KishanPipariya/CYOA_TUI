from typing import Any

from cyoa.core.constants import (
    INPUT_TIMING_PROFILE_OPTIONS,
    LINE_SPACING_OPTIONS,
    READING_WIDTH_OPTIONS,
    TEXT_SCALE_OPTIONS,
)
from cyoa.core.user_config import load_user_config, save_user_config


def load_config() -> dict[str, Any]:
    """Load UI preferences from the durable user config."""
    return load_user_config().to_ui_preferences()


def save_config(data: dict[str, Any]) -> None:  # noqa: C901
    """Persist UI preferences while preserving non-UI user settings."""
    config = load_user_config()
    dark = data.get("dark")
    if isinstance(dark, bool):
        config.dark = dark
    reduced_motion = data.get("reduced_motion")
    if isinstance(reduced_motion, bool):
        config.reduced_motion = reduced_motion
    screen_reader_mode = data.get("screen_reader_mode")
    if isinstance(screen_reader_mode, bool):
        config.screen_reader_mode = screen_reader_mode
    text_scale = data.get("text_scale")
    if isinstance(text_scale, str) and text_scale in TEXT_SCALE_OPTIONS:
        config.text_scale = text_scale
    line_width = data.get("line_width")
    if isinstance(line_width, str) and line_width in READING_WIDTH_OPTIONS:
        config.line_width = line_width
    line_spacing = data.get("line_spacing")
    if isinstance(line_spacing, str) and line_spacing in LINE_SPACING_OPTIONS:
        config.line_spacing = line_spacing
    input_timing_profile = data.get("input_timing_profile")
    if (
        isinstance(input_timing_profile, str)
        and input_timing_profile in INPUT_TIMING_PROFILE_OPTIONS
    ):
        config.input_timing_profile = input_timing_profile
    confirm_high_impact_actions = data.get("confirm_high_impact_actions")
    if isinstance(confirm_high_impact_actions, bool):
        config.confirm_high_impact_actions = confirm_high_impact_actions
    typewriter = data.get("typewriter")
    if isinstance(typewriter, bool):
        config.typewriter = typewriter
    typewriter_speed = data.get("typewriter_speed")
    if isinstance(typewriter_speed, str) and typewriter_speed:
        config.typewriter_speed = typewriter_speed
    save_user_config(config)
