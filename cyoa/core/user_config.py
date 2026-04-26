import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cyoa.core.constants import (
    CONFIG_FILE,
    LINE_SPACING_OPTIONS,
    READING_WIDTH_OPTIONS,
    TEXT_SCALE_OPTIONS,
)
from cyoa.core.support import open_private_text_file

logger = logging.getLogger(__name__)


USER_CONFIG_VERSION = 1
ACCESSIBILITY_PRESET_OPTIONS = (
    "default",
    "high_contrast",
    "reduced_motion",
    "screen_reader_friendly",
    "custom",
)
FIRST_RUN_ACCESSIBILITY_PRESET_OPTIONS = ACCESSIBILITY_PRESET_OPTIONS[:-1]


class UserConfigSaveError(RuntimeError):
    """Raised when the durable user config cannot be persisted."""


def _coerce_option(value: object, allowed: tuple[str, ...], default: str) -> str:
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in allowed:
            return cleaned
    return default


def _coerce_accessibility_preset(value: object, default: str = "default") -> str:
    if isinstance(value, str):
        cleaned = value.strip().lower().replace("-", "_").replace(" ", "_")
        if cleaned in ACCESSIBILITY_PRESET_OPTIONS:
            return cleaned
    return default


def accessibility_preset_overrides(preset: str) -> dict[str, bool]:
    normalized = _coerce_accessibility_preset(preset)
    if normalized == "high_contrast":
        return {
            "high_contrast": True,
            "reduced_motion": False,
            "screen_reader_mode": False,
        }
    if normalized == "reduced_motion":
        return {
            "high_contrast": False,
            "reduced_motion": True,
            "screen_reader_mode": False,
        }
    if normalized == "screen_reader_friendly":
        return {
            "high_contrast": False,
            "reduced_motion": True,
            "screen_reader_mode": True,
        }
    return {
        "high_contrast": False,
        "reduced_motion": False,
        "screen_reader_mode": False,
    }


def infer_accessibility_preset(
    *,
    high_contrast: bool,
    reduced_motion: bool,
    screen_reader_mode: bool,
) -> str:
    if screen_reader_mode and reduced_motion and not high_contrast:
        return "screen_reader_friendly"
    if reduced_motion and not screen_reader_mode and not high_contrast:
        return "reduced_motion"
    if high_contrast and not reduced_motion and not screen_reader_mode:
        return "high_contrast"
    if not high_contrast and not reduced_motion and not screen_reader_mode:
        return "default"
    return "custom"


@dataclass(slots=True)
class UserConfig:
    provider: str | None = None
    model_path: str | None = None
    theme: str = "dark_dungeon"
    dark: bool = True
    high_contrast: bool = False
    reduced_motion: bool = False
    screen_reader_mode: bool = False
    cognitive_load_reduction_mode: bool = False
    text_scale: str = "standard"
    line_width: str = "standard"
    line_spacing: str = "standard"
    keybindings: dict[str, str] = field(default_factory=dict)
    typewriter: bool = True
    typewriter_speed: str = "normal"
    diagnostics_enabled: bool = False
    accessibility_preset: str = "default"
    preset: str | None = None
    runtime_preset: str | None = None
    setup_completed: bool = False
    setup_choice: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: object) -> "UserConfig":
        if not isinstance(payload, dict):
            return cls()

        known_keys = {
            "provider",
            "model_path",
            "theme",
            "dark",
            "high_contrast",
            "reduced_motion",
            "screen_reader_mode",
            "cognitive_load_reduction_mode",
            "text_scale",
            "line_width",
            "line_spacing",
            "keybindings",
            "typewriter",
            "typewriter_speed",
            "diagnostics_enabled",
            "accessibility_preset",
            "preset",
            "runtime_preset",
            "setup_completed",
            "setup_choice",
            "version",
        }
        extras = {
            key: value
            for key, value in payload.items()
            if isinstance(key, str) and key not in known_keys
        }

        provider = payload.get("provider")
        model_path = payload.get("model_path")
        theme = payload.get("theme")
        dark = payload.get("dark")
        high_contrast = payload.get("high_contrast")
        reduced_motion = payload.get("reduced_motion")
        screen_reader_mode = payload.get("screen_reader_mode")
        cognitive_load_reduction_mode = payload.get("cognitive_load_reduction_mode")
        text_scale = payload.get("text_scale")
        line_width = payload.get("line_width")
        line_spacing = payload.get("line_spacing")
        keybindings = payload.get("keybindings")
        typewriter = payload.get("typewriter")
        typewriter_speed = payload.get("typewriter_speed")
        diagnostics_enabled = payload.get("diagnostics_enabled")
        accessibility_preset = payload.get("accessibility_preset")
        preset = payload.get("preset")
        runtime_preset = payload.get("runtime_preset")
        setup_completed = payload.get("setup_completed")
        setup_choice = payload.get("setup_choice")

        parsed_keybindings = (
            {
                key.strip(): value.strip()
                for key, value in keybindings.items()
                if isinstance(key, str) and key.strip() and isinstance(value, str) and value.strip()
            }
            if isinstance(keybindings, dict)
            else {}
        )

        return cls(
            provider=provider.strip() if isinstance(provider, str) and provider.strip() else None,
            model_path=model_path.strip()
            if isinstance(model_path, str) and model_path.strip()
            else None,
            theme=theme.strip() if isinstance(theme, str) and theme.strip() else "dark_dungeon",
            dark=dark if isinstance(dark, bool) else True,
            high_contrast=high_contrast if isinstance(high_contrast, bool) else False,
            reduced_motion=reduced_motion if isinstance(reduced_motion, bool) else False,
            screen_reader_mode=screen_reader_mode
            if isinstance(screen_reader_mode, bool)
            else False,
            cognitive_load_reduction_mode=(
                cognitive_load_reduction_mode
                if isinstance(cognitive_load_reduction_mode, bool)
                else False
            ),
            text_scale=_coerce_option(text_scale, TEXT_SCALE_OPTIONS, "standard"),
            line_width=_coerce_option(line_width, READING_WIDTH_OPTIONS, "standard"),
            line_spacing=_coerce_option(line_spacing, LINE_SPACING_OPTIONS, "standard"),
            keybindings=parsed_keybindings,
            typewriter=typewriter if isinstance(typewriter, bool) else True,
            typewriter_speed=(
                typewriter_speed.strip()
                if isinstance(typewriter_speed, str) and typewriter_speed.strip()
                else "normal"
            ),
            diagnostics_enabled=(
                diagnostics_enabled if isinstance(diagnostics_enabled, bool) else False
            ),
            accessibility_preset=_coerce_accessibility_preset(accessibility_preset),
            preset=preset.strip() if isinstance(preset, str) and preset.strip() else None,
            runtime_preset=(
                runtime_preset.strip()
                if isinstance(runtime_preset, str) and runtime_preset.strip()
                else None
            ),
            setup_completed=setup_completed if isinstance(setup_completed, bool) else False,
            setup_choice=(
                setup_choice.strip()
                if isinstance(setup_choice, str) and setup_choice.strip()
                else None
            ),
            extras=extras,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.extras)
        payload.update(
            {
                "version": USER_CONFIG_VERSION,
                "provider": self.provider,
                "model_path": self.model_path,
                "theme": self.theme,
                "dark": self.dark,
                "high_contrast": self.high_contrast,
                "reduced_motion": self.reduced_motion,
                "screen_reader_mode": self.screen_reader_mode,
                "cognitive_load_reduction_mode": self.cognitive_load_reduction_mode,
                "text_scale": self.text_scale,
                "line_width": self.line_width,
                "line_spacing": self.line_spacing,
                "keybindings": self.keybindings,
                "typewriter": self.typewriter,
                "typewriter_speed": self.typewriter_speed,
                "diagnostics_enabled": self.diagnostics_enabled,
                "accessibility_preset": self.accessibility_preset,
                "preset": self.preset,
                "runtime_preset": self.runtime_preset,
                "setup_completed": self.setup_completed,
                "setup_choice": self.setup_choice,
            }
        )
        return payload

    def to_ui_preferences(self) -> dict[str, Any]:
        return {
            "dark": self.dark,
            "high_contrast": self.high_contrast,
            "reduced_motion": self.reduced_motion,
            "screen_reader_mode": self.screen_reader_mode,
            "cognitive_load_reduction_mode": self.cognitive_load_reduction_mode,
            "text_scale": self.text_scale,
            "line_width": self.line_width,
            "line_spacing": self.line_spacing,
            "typewriter": self.typewriter,
            "typewriter_speed": self.typewriter_speed,
        }


def load_user_config() -> UserConfig:
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return UserConfig.from_dict(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        logger.debug("Falling back to default user config from %s: %s", CONFIG_FILE, exc)
        return UserConfig()


def save_user_config(config: UserConfig, *, raise_on_error: bool = False) -> None:
    try:
        Path(CONFIG_FILE).parent.mkdir(parents=True, exist_ok=True)
        with open_private_text_file(CONFIG_FILE, "w") as f:
            json.dump(config.to_dict(), f, indent=2, ensure_ascii=False)
    except OSError as exc:
        logger.warning("Unable to persist user config to %s: %s", CONFIG_FILE, exc)
        if raise_on_error:
            raise UserConfigSaveError(f"Unable to save settings to {CONFIG_FILE}: {exc}") from exc


def update_user_config(*, raise_on_error: bool = False, **changes: Any) -> UserConfig:
    config = load_user_config()
    for key, value in changes.items():
        if hasattr(config, key):
            setattr(config, key, value)
        else:
            config.extras[key] = value
    save_user_config(config, raise_on_error=raise_on_error)
    return config


def reset_user_config(*, preserve_setup: bool = True) -> UserConfig:
    current = load_user_config()
    reset = UserConfig()
    if preserve_setup:
        reset.setup_completed = current.setup_completed
        reset.setup_choice = current.setup_choice
    save_user_config(reset)
    return reset
