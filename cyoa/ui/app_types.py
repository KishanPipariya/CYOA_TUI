from dataclasses import dataclass
from typing import Literal

from cyoa.core import constants


@dataclass(slots=True)
class CYOAAppConfig:
    model_path: str
    starting_prompt: str = constants.DEFAULT_STARTING_PROMPT
    spinner_frames: list[str] | None = None
    accent_color: str | None = None
    ui_theme: dict[str, str] | None = None
    initial_world_state: dict[str, object] | None = None
    initial_prompt_config: dict[str, object] | None = None
    runtime_diagnostics: dict[str, str] | None = None
    startup_accessibility_overrides: dict[str, bool] | None = None
    allow_headless_startup_recovery: bool = False


@dataclass(slots=True)
class BufferedNotification:
    message: str
    severity: Literal["information", "warning", "error"]
    timeout: float


@dataclass(slots=True)
class NotificationHistoryEntry:
    message: str
    severity: Literal["information", "warning", "error"]


@dataclass(slots=True)
class FocusTarget:
    kind: Literal["widget_id", "choice_index"]
    value: str | int
