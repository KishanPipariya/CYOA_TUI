import json
import logging
import tomllib
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

THEMES_DIR = Path(__file__).parent.parent.parent / "themes"

_themes_cached_config: dict[str, Any] | None = None


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
        return tomllib.load(f)


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
                    _themes_cached_config = data
                    return data
        except Exception as e:
            logger.debug("Failed to load themes.json: %s", e)
    return {}


def get_config_for_mood(mood: str) -> dict[str, Any]:
    """Get the configuration for a specific mood, falling back to 'default'."""
    themes_config = get_moods_config()
    return themes_config.get(mood, themes_config.get("default", {}))
