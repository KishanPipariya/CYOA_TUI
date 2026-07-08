import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from cyoa.core import constants
from cyoa.core.theme_loader import list_themes, load_theme
from cyoa.core.user_config import (
    TerminalAccessibilityFallback,
    UserConfig,
    infer_accessibility_preset,
)
from cyoa.ui.keybindings import resolve_keybinding_overrides
from cyoa.ui.settings_types import SettingsDraft, SettingsPayload


@dataclass(frozen=True, slots=True)
class SettingsRuntimeState:
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
    typewriter: bool
    typewriter_speed: str
    runtime_provider: str | None
    terminal_accessibility_fallback: TerminalAccessibilityFallback | None


@dataclass(frozen=True, slots=True)
class SettingsScreenState:
    provider: str | None
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
    available_themes: list[dict[str, str]]
    terminal_accessibility_fallback: TerminalAccessibilityFallback | None
    initial_feedback: str = ""


@dataclass(frozen=True, slots=True)
class ResolvedSettings:
    user_config_changes: dict[str, Any]
    keybinding_overrides: dict[str, str]
    accessibility_preset: str
    pending_restart_changes: list[str]


@dataclass(frozen=True, slots=True)
class BackendTestRequest:
    provider: str
    model_path: str | None


def build_story_pack_options(
    *,
    list_theme_names: Callable[[], list[str]] = list_themes,
    load_theme_data: Callable[[str], dict[str, Any]] = load_theme,
) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for theme_name in list_theme_names():
        loaded_theme = load_theme_data(theme_name)
        campaign = (
            loaded_theme.get("campaign") if isinstance(loaded_theme.get("campaign"), dict) else None
        )
        options.append(
            {
                "id": theme_name,
                "name": str(loaded_theme.get("name") or theme_name),
                "description": str(loaded_theme.get("description") or "").strip(),
                "campaign_name": (
                    str(campaign.get("name")).strip()
                    if isinstance(campaign, dict) and campaign.get("name")
                    else ""
                ),
                "campaign_description": (
                    str(campaign.get("description")).strip()
                    if isinstance(campaign, dict) and campaign.get("description")
                    else ""
                ),
            }
        )
    return options


def build_settings_screen_state(
    config: UserConfig | Any,
    runtime: SettingsRuntimeState,
    draft_settings: SettingsDraft | Mapping[str, Any] | None = None,
    *,
    available_themes: list[dict[str, str]] | None = None,
    feedback_message: str = "",
) -> SettingsScreenState:
    draft = draft_settings or {}

    def pick(key: str, fallback: Any) -> Any:
        return draft.get(key, fallback)

    raw_keybindings = pick("keybindings", getattr(config, "keybindings", {}))
    keybindings = dict(raw_keybindings) if isinstance(raw_keybindings, Mapping) else {}

    return SettingsScreenState(
        provider=pick("provider", config.provider),
        model_path=pick("model_path", config.model_path),
        theme=pick("theme", config.theme),
        dark=bool(pick("dark", runtime.dark)),
        high_contrast=bool(pick("high_contrast", runtime.high_contrast)),
        reduced_motion=bool(pick("reduced_motion", runtime.reduced_motion)),
        screen_reader_mode=bool(pick("screen_reader_mode", runtime.screen_reader_mode)),
        cognitive_load_reduction_mode=bool(
            pick(
                "cognitive_load_reduction_mode",
                runtime.cognitive_load_reduction_mode,
            )
        ),
        text_scale=str(pick("text_scale", runtime.text_scale)),
        line_width=str(pick("line_width", runtime.line_width)),
        line_spacing=str(pick("line_spacing", runtime.line_spacing)),
        notification_verbosity=str(pick("notification_verbosity", runtime.notification_verbosity)),
        scene_recap_verbosity=str(pick("scene_recap_verbosity", runtime.scene_recap_verbosity)),
        runtime_metadata_verbosity=str(
            pick("runtime_metadata_verbosity", runtime.runtime_metadata_verbosity)
        ),
        locked_choice_verbosity=str(
            pick("locked_choice_verbosity", runtime.locked_choice_verbosity)
        ),
        input_timing_profile=str(
            pick("input_timing_profile", getattr(config, "input_timing_profile", "default"))
        ),
        confirm_high_impact_actions=bool(
            pick(
                "confirm_high_impact_actions",
                getattr(config, "confirm_high_impact_actions", False),
            )
        ),
        keybindings=keybindings,
        typewriter=bool(pick("typewriter", runtime.typewriter)),
        typewriter_speed=str(pick("typewriter_speed", runtime.typewriter_speed)),
        diagnostics_enabled=bool(
            pick("diagnostics_enabled", getattr(config, "diagnostics_enabled", False))
        ),
        available_themes=available_themes
        if available_themes is not None
        else build_story_pack_options(),
        terminal_accessibility_fallback=runtime.terminal_accessibility_fallback,
        initial_feedback=feedback_message,
    )


def resolve_provider_setting(payload: Mapping[str, Any]) -> str:
    provider = str(payload.get("provider") or "mock").strip().lower()
    return provider if provider in {"mock", "llama_cpp"} else "mock"


def resolve_option_setting(
    payload: Mapping[str, Any],
    key: str,
    current_value: str,
    allowed: tuple[str, ...],
) -> str:
    candidate = str(payload.get(key) or current_value).strip()
    return candidate if candidate in allowed else current_value


def resolve_settings_payload(
    payload: SettingsPayload | Mapping[str, Any],
    current_config: UserConfig | Any,
    *,
    runtime_provider: str | None,
) -> ResolvedSettings:
    provider = resolve_provider_setting(payload)
    keybinding_overrides = resolve_keybinding_overrides(
        payload.get("keybindings", getattr(current_config, "keybindings", {}))
    )
    raw_model_path = payload.get("model_path")
    model_path = (
        raw_model_path.strip()
        if isinstance(raw_model_path, str) and raw_model_path.strip()
        else None
    )
    theme_name = str(payload.get("theme") or current_config.theme).strip() or current_config.theme
    dark = bool(payload.get("dark", current_config.dark))
    high_contrast = bool(
        payload.get("high_contrast", getattr(current_config, "high_contrast", False))
    )
    reduced_motion = bool(
        payload.get("reduced_motion", getattr(current_config, "reduced_motion", False))
    )
    screen_reader_mode = bool(
        payload.get("screen_reader_mode", getattr(current_config, "screen_reader_mode", False))
    )
    cognitive_load_reduction_mode = bool(
        payload.get(
            "cognitive_load_reduction_mode",
            getattr(current_config, "cognitive_load_reduction_mode", False),
        )
    )
    text_scale = resolve_option_setting(
        payload,
        "text_scale",
        getattr(current_config, "text_scale", "standard"),
        constants.TEXT_SCALE_OPTIONS,
    )
    line_width = resolve_option_setting(
        payload,
        "line_width",
        getattr(current_config, "line_width", "standard"),
        constants.READING_WIDTH_OPTIONS,
    )
    line_spacing = resolve_option_setting(
        payload,
        "line_spacing",
        getattr(current_config, "line_spacing", "standard"),
        constants.LINE_SPACING_OPTIONS,
    )
    notification_verbosity = resolve_option_setting(
        payload,
        "notification_verbosity",
        getattr(current_config, "notification_verbosity", "standard"),
        constants.VERBOSITY_OPTIONS,
    )
    scene_recap_verbosity = resolve_option_setting(
        payload,
        "scene_recap_verbosity",
        getattr(current_config, "scene_recap_verbosity", "standard"),
        constants.VERBOSITY_OPTIONS,
    )
    runtime_metadata_verbosity = resolve_option_setting(
        payload,
        "runtime_metadata_verbosity",
        getattr(current_config, "runtime_metadata_verbosity", "standard"),
        constants.VERBOSITY_OPTIONS,
    )
    locked_choice_verbosity = resolve_option_setting(
        payload,
        "locked_choice_verbosity",
        getattr(current_config, "locked_choice_verbosity", "standard"),
        constants.VERBOSITY_OPTIONS,
    )
    input_timing_profile = resolve_option_setting(
        payload,
        "input_timing_profile",
        getattr(current_config, "input_timing_profile", "default"),
        constants.INPUT_TIMING_PROFILE_OPTIONS,
    )
    confirm_high_impact_actions = bool(
        payload.get(
            "confirm_high_impact_actions",
            getattr(current_config, "confirm_high_impact_actions", False),
        )
    )
    typewriter = bool(payload.get("typewriter", current_config.typewriter))
    typewriter_speed = resolve_option_setting(
        payload,
        "typewriter_speed",
        current_config.typewriter_speed,
        tuple(constants.TYPEWRITER_SPEEDS),
    )
    diagnostics_enabled = bool(
        payload.get("diagnostics_enabled", current_config.diagnostics_enabled)
    )
    accessibility_preset = infer_accessibility_preset(
        high_contrast=high_contrast,
        reduced_motion=reduced_motion,
        screen_reader_mode=screen_reader_mode,
    )
    pending_changes: list[str] = []
    if theme_name != current_config.theme:
        pending_changes.append("theme")
    if provider != runtime_provider:
        pending_changes.append("provider")
    if provider == "llama_cpp" and model_path != current_config.model_path:
        pending_changes.append("model path")

    return ResolvedSettings(
        user_config_changes={
            "provider": provider,
            "model_path": model_path,
            "theme": theme_name,
            "dark": dark,
            "high_contrast": high_contrast,
            "reduced_motion": reduced_motion,
            "screen_reader_mode": screen_reader_mode,
            "cognitive_load_reduction_mode": cognitive_load_reduction_mode,
            "text_scale": text_scale,
            "line_width": line_width,
            "line_spacing": line_spacing,
            "notification_verbosity": notification_verbosity,
            "scene_recap_verbosity": scene_recap_verbosity,
            "runtime_metadata_verbosity": runtime_metadata_verbosity,
            "locked_choice_verbosity": locked_choice_verbosity,
            "input_timing_profile": input_timing_profile,
            "confirm_high_impact_actions": confirm_high_impact_actions,
            "keybindings": keybinding_overrides,
            "typewriter": typewriter,
            "typewriter_speed": typewriter_speed,
            "diagnostics_enabled": diagnostics_enabled,
            "accessibility_preset": accessibility_preset,
        },
        keybinding_overrides=keybinding_overrides,
        accessibility_preset=accessibility_preset,
        pending_restart_changes=pending_changes,
    )


def set_diagnostics_env(enabled: bool) -> None:
    if enabled:
        os.environ["CYOA_ENABLE_RAG"] = "1"
        return
    os.environ.pop("CYOA_ENABLE_RAG", None)


def clear_settings_runtime_env() -> None:
    os.environ.pop("CYOA_ENABLE_RAG", None)
    os.environ.pop("LLM_MODEL_PATH", None)


def resolve_backend_test_request(
    config: UserConfig | Any,
    draft_settings: SettingsDraft | Mapping[str, Any] | None = None,
) -> BackendTestRequest:
    draft = draft_settings or {}
    provider = (
        resolve_provider_setting(draft_settings)
        if draft_settings is not None
        else (config.provider or "mock").strip().lower()
    )
    raw_model_path = draft.get("model_path", config.model_path)
    model_path = (
        raw_model_path.strip()
        if isinstance(raw_model_path, str) and raw_model_path.strip()
        else None
    )
    return BackendTestRequest(provider=provider, model_path=model_path)


__all__ = [
    "BackendTestRequest",
    "ResolvedSettings",
    "SettingsRuntimeState",
    "SettingsScreenState",
    "build_settings_screen_state",
    "build_story_pack_options",
    "clear_settings_runtime_env",
    "resolve_backend_test_request",
    "resolve_option_setting",
    "resolve_provider_setting",
    "resolve_settings_payload",
    "set_diagnostics_env",
]
