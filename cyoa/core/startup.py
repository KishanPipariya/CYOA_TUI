import argparse
import os
from dataclasses import dataclass

VALID_PROVIDERS = {"llama_cpp", "mock"}
RUNTIME_PRESETS = {
    "local-quality": {"provider": "llama_cpp", "generation_preset": "precise"},
    "local-fast": {"provider": "llama_cpp", "generation_preset": "balanced"},
    "mock-smoke": {"provider": "mock", "generation_preset": "precise"},
}


class StartupConfigError(ValueError):
    """Raised when startup configuration is invalid."""


@dataclass(frozen=True)
class StartupConfig:
    model: str | None
    provider: str
    theme: str
    prompt: str | None
    preset: str | None
    runtime_preset: str | None
    startup_accessibility_overrides: dict[str, bool]
    startup_note: str | None = None


def _parse_positive_int(name: str) -> None:
    raw_value = os.getenv(name)
    if raw_value is None:
        return

    try:
        parsed = int(raw_value)
    except ValueError as exc:
        raise StartupConfigError(f"{name} must be an integer; got {raw_value!r}.") from exc

    if parsed <= 0:
        raise StartupConfigError(f"{name} must be greater than 0; got {parsed}.")


def _parse_non_negative_float(name: str) -> None:
    raw_value = os.getenv(name)
    if raw_value is None:
        return

    try:
        parsed = float(raw_value)
    except ValueError as exc:
        raise StartupConfigError(f"{name} must be a number; got {raw_value!r}.") from exc

    if parsed < 0:
        raise StartupConfigError(f"{name} must be non-negative; got {parsed}.")


def _select_safe_default_provider(model: str | None) -> str:
    if model and os.path.exists(model):
        return "llama_cpp"
    return "mock"


def validate_startup_config(args: argparse.Namespace) -> StartupConfig:  # noqa: C901
    from cyoa.core.user_config import load_user_config
    from cyoa.llm.broker import PRESETS

    user_config = load_user_config()
    runtime_preset = (
        (
            args.runtime_preset.strip().lower()
            if isinstance(args.runtime_preset, str) and args.runtime_preset.strip()
            else None
        )
        or os.getenv("APP_RUNTIME_PRESET")
        or user_config.runtime_preset
    )
    runtime_preset = (
        runtime_preset.strip().lower()
        if isinstance(runtime_preset, str) and runtime_preset.strip()
        else None
    )
    if runtime_preset and runtime_preset not in RUNTIME_PRESETS:
        raise StartupConfigError(
            f"Unsupported runtime preset {runtime_preset!r}. Expected one of: {', '.join(sorted(RUNTIME_PRESETS))}."
        )

    runtime_defaults = RUNTIME_PRESETS[runtime_preset] if runtime_preset else {}

    _parse_positive_int("LLM_N_CTX")
    _parse_positive_int("LLM_MAX_TOKENS")
    _parse_positive_int("LLM_TOKEN_BUDGET")
    _parse_non_negative_float("LLM_TEMPERATURE")

    default_preset = str(runtime_defaults.get("generation_preset", "")).strip().lower() or None
    preset = (
        (
            args.preset.strip().lower()
            if isinstance(args.preset, str) and args.preset.strip()
            else None
        )
        or os.getenv("LLM_PRESET")
        or user_config.preset
        or default_preset
    )
    preset = preset.strip().lower() if isinstance(preset, str) and preset.strip() else None
    if preset and preset not in PRESETS:
        raise StartupConfigError(
            f"Unsupported preset {preset!r}. Expected one of: {', '.join(sorted(PRESETS))}."
        )

    cli_model = args.model.strip() if isinstance(args.model, str) and args.model.strip() else None
    env_model = os.getenv("LLM_MODEL_PATH")
    saved_model = user_config.model_path

    provider_source = "default"
    raw_provider: str | None = None
    if cli_model:
        # An explicit CLI model path should boot the local provider even if a prior
        # session or shell exported mock mode.
        raw_provider = "llama_cpp"
        provider_source = "cli_model"
    elif "LLM_PROVIDER" in os.environ:
        raw_provider = os.environ["LLM_PROVIDER"]
        provider_source = "env"
    elif user_config.provider:
        raw_provider = user_config.provider
        provider_source = "user_config"
    elif runtime_defaults.get("provider"):
        raw_provider = str(runtime_defaults["provider"])
        provider_source = "runtime_preset"

    model: str | None
    if cli_model:
        model = cli_model
    elif env_model:
        model = env_model
    elif raw_provider is None:
        model = saved_model
    elif raw_provider.strip().lower() == "llama_cpp" and provider_source in {
        "user_config",
        "runtime_preset",
    }:
        model = saved_model
    else:
        model = None

    startup_note: str | None = None
    provider: str
    if raw_provider is None:
        provider = _select_safe_default_provider(model)
    else:
        provider = raw_provider.strip().lower()
        if provider not in VALID_PROVIDERS:
            valid = ", ".join(sorted(VALID_PROVIDERS))
            raise StartupConfigError(
                f"Unsupported LLM_PROVIDER {provider!r}. Expected one of: {valid}."
            )

        if provider == "llama_cpp":
            if not model:
                if provider_source in {"env", "cli_model"}:
                    raise StartupConfigError(
                        "No local model configured for llama_cpp. Use --model or set LLM_MODEL_PATH in .env."
                    )
                provider = _select_safe_default_provider(model=None)
                startup_note = (
                    f"Local model was not configured. Starting in {provider} mode instead."
                )
            elif not os.path.exists(model):
                if provider_source in {"env", "cli_model"}:
                    raise StartupConfigError(
                        f"Configured llama_cpp model file does not exist: {model!r}."
                    )
                provider = _select_safe_default_provider(model=None)
                startup_note = (
                    f"Configured local model was unavailable. Starting in {provider} mode instead."
                )

    theme = (
        args.theme.strip()
        if isinstance(args.theme, str) and args.theme.strip()
        else user_config.theme or "dark_dungeon"
    )
    startup_accessibility_overrides = {
        key: True
        for key, enabled in (
            ("screen_reader_mode", args.screen_reader),
            ("high_contrast", args.high_contrast),
            ("reduced_motion", args.reduced_motion),
        )
        if enabled
    }

    return StartupConfig(
        model=model,
        provider=provider,
        theme=theme,
        prompt=args.prompt,
        preset=preset,
        runtime_preset=runtime_preset,
        startup_accessibility_overrides=startup_accessibility_overrides,
        startup_note=startup_note,
    )
