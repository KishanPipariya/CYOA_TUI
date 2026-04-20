from typing import Any, cast

from cyoa.core.user_config import load_user_config, save_user_config


def load_config() -> dict[str, Any]:
    """Load UI preferences from the durable user config."""
    return cast(dict[str, Any], load_user_config().to_ui_preferences())


def save_config(data: dict[str, Any]) -> None:
    """Persist UI preferences while preserving non-UI user settings."""
    config = load_user_config()
    dark = data.get("dark")
    if isinstance(dark, bool):
        config.dark = dark
    typewriter = data.get("typewriter")
    if isinstance(typewriter, bool):
        config.typewriter = typewriter
    typewriter_speed = data.get("typewriter_speed")
    if isinstance(typewriter_speed, str) and typewriter_speed:
        config.typewriter_speed = typewriter_speed
    save_user_config(config)
