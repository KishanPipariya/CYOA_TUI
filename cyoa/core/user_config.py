import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from cyoa.core.constants import (
    CONFIG_FILE,
    INPUT_TIMING_PROFILE_OPTIONS,
    LINE_SPACING_OPTIONS,
    READING_WIDTH_OPTIONS,
    TEXT_SCALE_OPTIONS,
    VERBOSITY_OPTIONS,
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
ACCESSIBILITY_SETTING_KEYS = (
    "high_contrast",
    "reduced_motion",
    "screen_reader_mode",
)
STARTUP_RECOMMENDATION_COMPACT_WIDTH = 140


@dataclass(slots=True, frozen=True)
class StartupAccessibilityRecommendation:
    key: str
    accessibility_preset: str
    title: str
    message: str
    reasons: tuple[str, ...] = ()
    rescue_mode_active: bool = False


@dataclass(slots=True, frozen=True)
class TerminalAccessibilityFallback:
    key: str
    accessibility_preset: str
    title: str
    message: str
    reasons: tuple[str, ...] = ()

    @property
    def overrides(self) -> dict[str, bool]:
        return accessibility_preset_overrides(self.accessibility_preset)


@dataclass(slots=True, frozen=True)
class AccessibilityProfileReport:
    summary: str
    advisory_lines: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class SafetyProfileReport:
    summary: str
    advisory_lines: tuple[str, ...] = ()


class UserConfigSaveError(RuntimeError):
    """Raised when the durable user config cannot be persisted."""


class UserConfigLoadError(ValueError):
    """Raised when an existing configuration file is not the supported schema."""


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


def _coerce_input_timing_profile(value: object, default: str = "default") -> str:
    if isinstance(value, str):
        cleaned = value.strip().lower().replace("-", "_").replace(" ", "_")
        if cleaned in INPUT_TIMING_PROFILE_OPTIONS:
            return cleaned
    return default


def _coerce_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        cleaned = item.strip().lower().replace(" ", "_")
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return normalized


def _trimmed_optional_str(value: object) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return None


def _coerce_keybindings(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        key.strip(): binding.strip()
        for key, binding in value.items()
        if isinstance(key, str) and key.strip() and isinstance(binding, str) and binding.strip()
    }


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
    _KNOWN_PAYLOAD_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
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
            "notification_verbosity",
            "scene_recap_verbosity",
            "runtime_metadata_verbosity",
            "locked_choice_verbosity",
            "input_timing_profile",
            "confirm_high_impact_actions",
            "keybindings",
            "typewriter",
            "typewriter_speed",
            "diagnostics_enabled",
            "accessibility_preset",
            "preset",
            "runtime_preset",
            "setup_completed",
            "setup_choice",
            "dismissed_startup_recommendations",
            "version",
        }
    )

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
    notification_verbosity: str = "standard"
    scene_recap_verbosity: str = "standard"
    runtime_metadata_verbosity: str = "standard"
    locked_choice_verbosity: str = "standard"
    input_timing_profile: str = "default"
    confirm_high_impact_actions: bool = False
    keybindings: dict[str, str] = field(default_factory=dict)
    typewriter: bool = True
    typewriter_speed: str = "normal"
    diagnostics_enabled: bool = False
    accessibility_preset: str = "default"
    preset: str | None = None
    runtime_preset: str | None = None
    setup_completed: bool = False
    setup_choice: str | None = None
    dismissed_startup_recommendations: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: object) -> "UserConfig":  # noqa: C901
        if not isinstance(payload, dict):
            raise UserConfigLoadError("configuration must be a JSON object")
        keys = set(payload)
        missing = sorted(cls._KNOWN_PAYLOAD_KEYS - keys)
        unknown = sorted(keys - cls._KNOWN_PAYLOAD_KEYS)
        if missing:
            raise UserConfigLoadError(
                "configuration is missing required keys: " + ", ".join(missing)
            )
        if unknown:
            raise UserConfigLoadError("configuration has unknown keys: " + ", ".join(unknown))
        if payload["version"] != USER_CONFIG_VERSION or isinstance(payload["version"], bool):
            raise UserConfigLoadError(
                f"configuration has unsupported version; expected {USER_CONFIG_VERSION}"
            )

        def optional_string(name: str) -> str | None:
            value = payload[name]
            if value is None:
                return None
            if not isinstance(value, str) or not value:
                raise UserConfigLoadError(f"configuration has invalid {name}")
            return value

        def required_string(name: str, allowed: tuple[str, ...] | None = None) -> str:
            value = payload[name]
            if (
                not isinstance(value, str)
                or not value
                or (allowed is not None and value not in allowed)
            ):
                raise UserConfigLoadError(f"configuration has invalid {name}")
            return value

        def boolean(name: str) -> bool:
            value = payload[name]
            if not isinstance(value, bool):
                raise UserConfigLoadError(f"configuration has invalid {name}")
            return value

        keybindings = payload["keybindings"]
        if not isinstance(keybindings, dict) or any(
            not isinstance(key, str) or not key or not isinstance(value, str) or not value
            for key, value in keybindings.items()
        ):
            raise UserConfigLoadError("configuration has invalid keybindings")
        dismissed = payload["dismissed_startup_recommendations"]
        if not isinstance(dismissed, list) or any(
            not isinstance(item, str) or not item for item in dismissed
        ):
            raise UserConfigLoadError("configuration has invalid dismissed_startup_recommendations")
        if len(set(dismissed)) != len(dismissed):
            raise UserConfigLoadError(
                "configuration has duplicate dismissed_startup_recommendations"
            )

        return cls(
            provider=optional_string("provider"),
            model_path=optional_string("model_path"),
            theme=required_string("theme"),
            dark=boolean("dark"),
            high_contrast=boolean("high_contrast"),
            reduced_motion=boolean("reduced_motion"),
            screen_reader_mode=boolean("screen_reader_mode"),
            cognitive_load_reduction_mode=boolean("cognitive_load_reduction_mode"),
            text_scale=required_string("text_scale", TEXT_SCALE_OPTIONS),
            line_width=required_string("line_width", READING_WIDTH_OPTIONS),
            line_spacing=required_string("line_spacing", LINE_SPACING_OPTIONS),
            notification_verbosity=required_string("notification_verbosity", VERBOSITY_OPTIONS),
            scene_recap_verbosity=required_string("scene_recap_verbosity", VERBOSITY_OPTIONS),
            runtime_metadata_verbosity=required_string(
                "runtime_metadata_verbosity", VERBOSITY_OPTIONS
            ),
            locked_choice_verbosity=required_string("locked_choice_verbosity", VERBOSITY_OPTIONS),
            input_timing_profile=required_string(
                "input_timing_profile", INPUT_TIMING_PROFILE_OPTIONS
            ),
            confirm_high_impact_actions=boolean("confirm_high_impact_actions"),
            keybindings=dict(keybindings),
            typewriter=boolean("typewriter"),
            typewriter_speed=required_string("typewriter_speed"),
            diagnostics_enabled=boolean("diagnostics_enabled"),
            accessibility_preset=required_string(
                "accessibility_preset", ACCESSIBILITY_PRESET_OPTIONS
            ),
            preset=optional_string("preset"),
            runtime_preset=optional_string("runtime_preset"),
            setup_completed=boolean("setup_completed"),
            setup_choice=optional_string("setup_choice"),
            dismissed_startup_recommendations=list(dismissed),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
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
            "notification_verbosity": self.notification_verbosity,
            "scene_recap_verbosity": self.scene_recap_verbosity,
            "runtime_metadata_verbosity": self.runtime_metadata_verbosity,
            "locked_choice_verbosity": self.locked_choice_verbosity,
            "input_timing_profile": self.input_timing_profile,
            "confirm_high_impact_actions": self.confirm_high_impact_actions,
            "keybindings": self.keybindings,
            "typewriter": self.typewriter,
            "typewriter_speed": self.typewriter_speed,
            "diagnostics_enabled": self.diagnostics_enabled,
            "accessibility_preset": self.accessibility_preset,
            "preset": self.preset,
            "runtime_preset": self.runtime_preset,
            "setup_completed": self.setup_completed,
            "setup_choice": self.setup_choice,
            "dismissed_startup_recommendations": self.dismissed_startup_recommendations,
        }
        # Keep direct construction convenient for runtime code, but never emit
        # an invalid config file.
        UserConfig.from_dict(payload)
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
            "notification_verbosity": self.notification_verbosity,
            "scene_recap_verbosity": self.scene_recap_verbosity,
            "runtime_metadata_verbosity": self.runtime_metadata_verbosity,
            "locked_choice_verbosity": self.locked_choice_verbosity,
            "input_timing_profile": self.input_timing_profile,
            "confirm_high_impact_actions": self.confirm_high_impact_actions,
            "typewriter": self.typewriter,
            "typewriter_speed": self.typewriter_speed,
        }


def resolve_accessibility_preferences(
    config: UserConfig,
    overrides: Mapping[str, object] | None = None,
) -> dict[str, bool]:
    resolved = {
        "high_contrast": bool(getattr(config, "high_contrast", False)),
        "reduced_motion": bool(getattr(config, "reduced_motion", False)),
        "screen_reader_mode": bool(getattr(config, "screen_reader_mode", False)),
    }
    if overrides is None:
        return resolved

    for key in ACCESSIBILITY_SETTING_KEYS:
        value = overrides.get(key)
        if isinstance(value, bool):
            resolved[key] = value
    return resolved


def _has_limited_color_capability(
    *,
    term: str | None,
    colorterm: str | None,
    no_color: bool,
) -> bool:
    if no_color:
        return True

    active_term = (term or "").strip().lower()
    active_color_term = (colorterm or "").strip().lower()
    if "mono" in active_term:
        return True
    if active_term in {"ansi", "vt100", "vt220"} and not active_color_term:
        return True
    return False


def _recommendation_is_already_satisfied(
    recommendation: StartupAccessibilityRecommendation,
    preferences: Mapping[str, bool],
) -> bool:
    overrides = accessibility_preset_overrides(recommendation.accessibility_preset)
    return all(not required or preferences.get(key, False) for key, required in overrides.items())


def infer_startup_accessibility_recommendation(
    *,
    config: UserConfig,
    width: int,
    height: int,
    term: str | None = None,
    colorterm: str | None = None,
    no_color: bool = False,
    overrides: Mapping[str, object] | None = None,
) -> StartupAccessibilityRecommendation | None:
    if overrides and any(bool(overrides.get(key)) for key in ACCESSIBILITY_SETTING_KEYS):
        return None

    preferences = resolve_accessibility_preferences(config, overrides)
    limited_color = _has_limited_color_capability(
        term=term,
        colorterm=colorterm,
        no_color=no_color,
    )
    narrow_terminal = width < 100 or height < 28

    recommendation: StartupAccessibilityRecommendation | None = None
    if narrow_terminal:
        reasons = [f"Current terminal size: {width}x{height}."]
        if width < STARTUP_RECOMMENDATION_COMPACT_WIDTH:
            reasons.append("Rescue mode will simplify the layout automatically at this size.")
        if limited_color:
            reasons.append(
                "Color support looks limited, so plain text rendering will be more reliable."
            )
        recommendation = StartupAccessibilityRecommendation(
            key="narrow_terminal_screen_reader",
            accessibility_preset="screen_reader_friendly",
            title="Screen Reader Friendly Startup Recommended",
            message=(
                "This terminal is tight enough that decorative output and motion can make the "
                "opening UI harder to follow."
            ),
            reasons=tuple(reasons),
            rescue_mode_active=width < STARTUP_RECOMMENDATION_COMPACT_WIDTH,
        )

    if recommendation is None:
        return None
    if recommendation.key in getattr(config, "dismissed_startup_recommendations", []):
        return None
    if _recommendation_is_already_satisfied(recommendation, preferences):
        return None
    return recommendation


def infer_terminal_accessibility_fallback(
    *,
    term: str | None = None,
    colorterm: str | None = None,
    no_color: bool = False,
) -> TerminalAccessibilityFallback | None:
    if not _has_limited_color_capability(term=term, colorterm=colorterm, no_color=no_color):
        return None

    reasons: list[str] = []
    active_term = (term or "").strip().lower()
    active_colorterm = (colorterm or "").strip().lower()
    if no_color:
        reasons.append("NO_COLOR is set for this terminal session.")
    if "mono" in active_term:
        reasons.append(f"Terminal reports monochrome capabilities via TERM={active_term!r}.")
    elif active_term in {"ansi", "vt100", "vt220"} and not active_colorterm:
        reasons.append(f"Terminal reports limited color support via TERM={active_term!r}.")
    else:
        reasons.append("Terminal color capability looks limited for this launch.")

    return TerminalAccessibilityFallback(
        key="limited_terminal_capability_plaintext",
        accessibility_preset="screen_reader_friendly",
        title="Terminal Capability Fallback Active",
        message=(
            "This terminal session looks color-limited, so the app is forcing plain-text "
            "rendering and reduced motion for reliability."
        ),
        reasons=tuple(reasons),
    )


def _active_accessibility_modes(
    *,
    high_contrast: bool,
    reduced_motion: bool,
    screen_reader_mode: bool,
    cognitive_load_reduction_mode: bool,
) -> list[str]:
    active_modes: list[str] = []
    if screen_reader_mode:
        active_modes.append("Screen Reader Friendly")
    elif high_contrast:
        active_modes.append("High Contrast")
    if reduced_motion and not screen_reader_mode:
        active_modes.append("Reduced Motion")
    if cognitive_load_reduction_mode:
        active_modes.append("Reduced Cognitive Load")
    if not active_modes:
        active_modes.append("Default")
    return active_modes


def _terminal_fallback_forced_modes(
    fallback: TerminalAccessibilityFallback | None,
) -> list[str]:
    if fallback is None:
        return []

    return [
        label
        for key, label in (
            ("high_contrast", "High Contrast"),
            ("reduced_motion", "Reduced Motion"),
            ("screen_reader_mode", "Screen Reader Friendly"),
        )
        if fallback.overrides.get(key, False)
    ]


def _accessibility_profile_advisories(
    *,
    reduced_motion: bool,
    screen_reader_mode: bool,
    cognitive_load_reduction_mode: bool,
    runtime_metadata_verbosity: str,
    typewriter: bool,
    terminal_fallback: TerminalAccessibilityFallback | None,
) -> list[str]:
    advisory_lines: list[str] = []
    if terminal_fallback is not None:
        advisory_lines.append(terminal_fallback.message)
    if screen_reader_mode and not reduced_motion:
        advisory_lines.append(
            "Screen Reader Friendly is enabled without Reduced Motion, so this is a custom mix instead of the standard preset."
        )
    if cognitive_load_reduction_mode and runtime_metadata_verbosity != "minimal":
        advisory_lines.append(
            "Reduced Cognitive Load can still hide lower-priority runtime detail even when Runtime Metadata is set above Minimal."
        )
    if reduced_motion and typewriter:
        advisory_lines.append(
            "Reduced Motion keeps narrated text instant even while Typewriter remains enabled."
        )
    return advisory_lines


def build_accessibility_profile_report(
    *,
    high_contrast: bool,
    reduced_motion: bool,
    screen_reader_mode: bool,
    cognitive_load_reduction_mode: bool,
    text_scale: str,
    line_width: str,
    line_spacing: str,
    runtime_metadata_verbosity: str,
    typewriter: bool,
    terminal_fallback: TerminalAccessibilityFallback | None = None,
) -> AccessibilityProfileReport:
    active_modes = _active_accessibility_modes(
        high_contrast=high_contrast,
        reduced_motion=reduced_motion,
        screen_reader_mode=screen_reader_mode,
        cognitive_load_reduction_mode=cognitive_load_reduction_mode,
    )

    summary_parts = [
        f"Active profile: {', '.join(active_modes)}.",
        f"Reading: {text_scale} text, {line_width} width, {line_spacing} spacing.",
    ]

    forced_modes = _terminal_fallback_forced_modes(terminal_fallback)
    if forced_modes:
        summary_parts.append(
            f"Terminal fallback for this launch forces: {', '.join(forced_modes)}."
        )

    advisory_lines = _accessibility_profile_advisories(
        reduced_motion=reduced_motion,
        screen_reader_mode=screen_reader_mode,
        cognitive_load_reduction_mode=cognitive_load_reduction_mode,
        runtime_metadata_verbosity=runtime_metadata_verbosity,
        typewriter=typewriter,
        terminal_fallback=terminal_fallback,
    )

    return AccessibilityProfileReport(
        summary=" ".join(summary_parts),
        advisory_lines=tuple(advisory_lines),
    )


def input_timing_profile_details(profile: str) -> dict[str, float | str]:
    normalized = _coerce_input_timing_profile(profile)
    if normalized == "gentle":
        return {
            "profile": "gentle",
            "label": "Gentle",
            "navigation_debounce_seconds": 0.12,
            "repeat_pacing_seconds": 0.2,
        }
    if normalized == "steady":
        return {
            "profile": "steady",
            "label": "Steady",
            "navigation_debounce_seconds": 0.25,
            "repeat_pacing_seconds": 0.35,
        }
    return {
        "profile": "default",
        "label": "Default",
        "navigation_debounce_seconds": 0.0,
        "repeat_pacing_seconds": 0.0,
    }


def build_safety_profile_report(
    *,
    input_timing_profile: str,
    confirm_high_impact_actions: bool,
) -> SafetyProfileReport:
    details = input_timing_profile_details(input_timing_profile)
    navigation_ms = int(float(details["navigation_debounce_seconds"]) * 1000)
    repeat_ms = int(float(details["repeat_pacing_seconds"]) * 1000)
    confirmation_label = "Expanded" if confirm_high_impact_actions else "Standard"
    summary_parts = [
        (
            f"Safety profile: {details['label']} timing with {confirmation_label.lower()} action confirmations."
        ),
    ]
    if navigation_ms or repeat_ms:
        summary_parts.append(
            f"Navigation debounce {navigation_ms} ms and repeat pacing {repeat_ms} ms are active."
        )
    else:
        summary_parts.append("Direct keyboard timing stays at the fast default.")

    advisory_lines: list[str] = []
    if confirm_high_impact_actions:
        advisory_lines.append(
            "Expanded confirmations also protect loading saves, restoring checkpoints, branching from past scenes, and the end-of-run new-adventure button."
        )
    if navigation_ms or repeat_ms:
        advisory_lines.append(
            "Timed input gating can ignore very fast repeated key presses so sticky keys, hold-repeat, and switch-like inputs do not trigger extra moves."
        )

    return SafetyProfileReport(
        summary=" ".join(summary_parts),
        advisory_lines=tuple(advisory_lines),
    )


def load_user_config() -> UserConfig:
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return UserConfig.from_dict(json.load(f))
    except FileNotFoundError:
        return UserConfig()
    except (json.JSONDecodeError, OSError, UserConfigLoadError) as exc:
        raise UserConfigLoadError(f"Unable to load configuration at {CONFIG_FILE}: {exc}") from exc


def save_user_config(config: UserConfig, *, raise_on_error: bool = False) -> None:
    try:
        payload = config.to_dict()
        Path(CONFIG_FILE).parent.mkdir(parents=True, exist_ok=True)
        with open_private_text_file(CONFIG_FILE, "w") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    except (OSError, UserConfigLoadError) as exc:
        logger.warning("Unable to persist user config to %s: %s", CONFIG_FILE, exc)
        if raise_on_error:
            raise UserConfigSaveError(f"Unable to save settings to {CONFIG_FILE}: {exc}") from exc


def update_user_config(*, raise_on_error: bool = False, **changes: Any) -> UserConfig:
    config = load_user_config()
    for key, value in changes.items():
        if key not in UserConfig.__dataclass_fields__:
            raise ValueError(f"Unknown user configuration field: {key}")
        setattr(config, key, value)
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
