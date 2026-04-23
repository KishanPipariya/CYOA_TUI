import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cyoa.core.constants import CONFIG_FILE
from cyoa.core.support import open_private_text_file

logger = logging.getLogger(__name__)


USER_CONFIG_VERSION = 1


@dataclass(slots=True)
class UserConfig:
    provider: str | None = None
    model_path: str | None = None
    theme: str = "dark_dungeon"
    dark: bool = True
    typewriter: bool = True
    typewriter_speed: str = "normal"
    diagnostics_enabled: bool = False
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
            "typewriter",
            "typewriter_speed",
            "diagnostics_enabled",
            "preset",
            "runtime_preset",
            "setup_completed",
            "setup_choice",
            "version",
        }
        extras = {
            key: value for key, value in payload.items() if isinstance(key, str) and key not in known_keys
        }

        provider = payload.get("provider")
        model_path = payload.get("model_path")
        theme = payload.get("theme")
        dark = payload.get("dark")
        typewriter = payload.get("typewriter")
        typewriter_speed = payload.get("typewriter_speed")
        diagnostics_enabled = payload.get("diagnostics_enabled")
        preset = payload.get("preset")
        runtime_preset = payload.get("runtime_preset")
        setup_completed = payload.get("setup_completed")
        setup_choice = payload.get("setup_choice")

        return cls(
            provider=provider.strip() if isinstance(provider, str) and provider.strip() else None,
            model_path=model_path.strip() if isinstance(model_path, str) and model_path.strip() else None,
            theme=theme.strip() if isinstance(theme, str) and theme.strip() else "dark_dungeon",
            dark=dark if isinstance(dark, bool) else True,
            typewriter=typewriter if isinstance(typewriter, bool) else True,
            typewriter_speed=(
                typewriter_speed.strip()
                if isinstance(typewriter_speed, str) and typewriter_speed.strip()
                else "normal"
            ),
            diagnostics_enabled=(
                diagnostics_enabled if isinstance(diagnostics_enabled, bool) else False
            ),
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
                "typewriter": self.typewriter,
                "typewriter_speed": self.typewriter_speed,
                "diagnostics_enabled": self.diagnostics_enabled,
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


def save_user_config(config: UserConfig) -> None:
    try:
        Path(CONFIG_FILE).parent.mkdir(parents=True, exist_ok=True)
        with open_private_text_file(CONFIG_FILE, "w") as f:
            json.dump(config.to_dict(), f, indent=2, ensure_ascii=False)
    except OSError as exc:
        logger.warning("Unable to persist user config to %s: %s", CONFIG_FILE, exc)


def update_user_config(**changes: Any) -> UserConfig:
    config = load_user_config()
    for key, value in changes.items():
        if hasattr(config, key):
            setattr(config, key, value)
        else:
            config.extras[key] = value
    save_user_config(config)
    return config


def reset_user_config(*, preserve_setup: bool = True) -> UserConfig:
    current = load_user_config()
    reset = UserConfig()
    if preserve_setup:
        reset.setup_completed = current.setup_completed
        reset.setup_choice = current.setup_choice
    save_user_config(reset)
    return reset
