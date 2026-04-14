import json
import logging
import tomllib
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)

THEMES_DIR = Path(__file__).parent.parent.parent / "themes"

_themes_cached_config: dict[str, Any] | None = None


class ThemeValidationError(ValueError):
    """Raised when a theme file or theme config is structurally invalid."""


def _require_non_empty_string(value: Any, field: str, source: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ThemeValidationError(f"{source}: {field} must be a non-empty string.")
    return value


def _require_non_empty_string_list(value: Any, field: str, source: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ThemeValidationError(f"{source}: {field} must be a non-empty list of strings.")
    validated: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ThemeValidationError(f"{source}: {field} entries must be non-empty strings.")
        validated.append(item)
    return validated


def validate_theme(theme: dict[str, Any], theme_name: str) -> dict[str, Any]:
    """Validate and normalize a single theme payload."""
    source = f"Theme '{theme_name}'"
    return {
        "name": _require_non_empty_string(theme.get("name"), "name", source),
        "description": _require_non_empty_string(theme.get("description"), "description", source),
        "prompt": _require_non_empty_string(theme.get("prompt"), "prompt", source),
        "accent_color": _require_non_empty_string(theme.get("accent_color"), "accent_color", source),
        "spinner_frames": _require_non_empty_string_list(
            theme.get("spinner_frames"), "spinner_frames", source
        ),
    }


def validate_moods_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate the optional mood-to-theme config loaded from themes.json."""
    validated: dict[str, Any] = {}
    for mood, value in config.items():
        source = f"themes.json mood '{mood}'"
        if not isinstance(value, dict):
            raise ThemeValidationError(f"{source} must be an object.")
        normalized: dict[str, Any] = {}
        if "accent_color" in value:
            normalized["accent_color"] = _require_non_empty_string(
                value["accent_color"], "accent_color", source
            )
        if "description" in value:
            normalized["description"] = _require_non_empty_string(
                value["description"], "description", source
            )
        if "spinner_frames" in value:
            normalized["spinner_frames"] = _require_non_empty_string_list(
                value["spinner_frames"], "spinner_frames", source
            )
        validated[mood] = normalized
    return validated


def validate_all_themes() -> list[str]:
    """Validate every theme file and themes.json, returning validated theme names."""
    theme_names = list_themes()
    for theme_name in theme_names:
        load_theme(theme_name)
    themes_path = THEMES_DIR / "themes.json"
    if themes_path.exists():
        with open(themes_path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ThemeValidationError("themes.json must contain an object at the top level.")
        validate_moods_config(cast(dict[str, Any], data))
    return theme_names


def load_theme(name: str) -> dict[str, Any]:
    """
    Load a theme by name from the themes/ directory.
    Returns a dict with keys: name, description, accent_color, prompt.
    Raises SystemExit with a helpful message if the theme is not found.
    """
    theme_path = THEMES_DIR / f"{name}.toml"

    if not theme_path.exists():
        available = [p.stem for p in THEMES_DIR.glob("*.toml")]
        raise FileNotFoundError(
            f"Theme '{name}' not found. "
            f"Available themes: {', '.join(sorted(available)) or '(none)'}\n"
            f"Theme files live in: {THEMES_DIR}"
        )

    with open(theme_path, "rb") as f:
        return validate_theme(tomllib.load(f), name)


def list_themes() -> list[str]:
    """Return a sorted list of all available theme names."""
    return sorted(p.stem for p in THEMES_DIR.glob("*.toml"))


def get_moods_config() -> dict[str, Any]:
    """Load the mood-to-theme mapping from themes.json with rudimentary caching."""
    global _themes_cached_config
    if _themes_cached_config is not None:
        return _themes_cached_config

    themes_path = THEMES_DIR / "themes.json"
    if themes_path.exists():
        try:
            with open(themes_path, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    typed_data = validate_moods_config(cast(dict[str, Any], data))
                    _themes_cached_config = typed_data
                    return typed_data
                logger.debug("themes.json top-level payload is not an object.")
        except Exception as e:
            logger.debug("Failed to load themes.json: %s", e)
    return {}


def get_config_for_mood(mood: str) -> dict[str, Any]:
    """Get the configuration for a specific mood, falling back to 'default'."""
    themes_config = get_moods_config()
    config = themes_config.get(mood, themes_config.get("default", {}))
    if isinstance(config, dict):
        return cast(dict[str, Any], config)
    return {}
