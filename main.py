import argparse
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from dotenv import load_dotenv

# Load .env before anything that reads os.getenv (graph_db)
load_dotenv()

VALID_PROVIDERS = {"llama_cpp", "ollama", "mock"}
RUNTIME_PRESETS = {
    "local-quality": {"provider": "llama_cpp", "generation_preset": "precise"},
    "local-fast": {"provider": "llama_cpp", "generation_preset": "balanced"},
    "ollama-dev": {"provider": "ollama", "generation_preset": "balanced"},
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


def _build_parser(available_themes: Sequence[str] | None = None) -> argparse.ArgumentParser:
    themes_help = "Story theme to use (default: dark_dungeon)."
    if available_themes:
        themes_help = (
            "Story theme to use. "
            f"Available: {', '.join(available_themes)} (default: dark_dungeon)"
        )

    parser = argparse.ArgumentParser(description="CYOA Terminal Game with Local LLM")
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Path to the .gguf model file (saved config first, env vars override).",
    )
    parser.add_argument(
        "--theme",
        type=str,
        default=None,
        help=themes_help,
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default=None,
        help="Override the starting prompt directly (takes precedence over --theme).",
    )
    parser.add_argument(
        "--preset",
        type=str,
        default=None,
        help="Generation preset to use at startup (balanced, precise, cinematic).",
    )
    parser.add_argument(
        "--runtime-preset",
        type=str,
        default=None,
        help="Runtime profile to apply (local-quality, local-fast, ollama-dev, mock-smoke).",
    )
    return parser


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


def validate_startup_config(args: argparse.Namespace) -> StartupConfig:
    from cyoa.llm.broker import PRESETS
    from cyoa.core.user_config import load_user_config

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
    raw_provider = os.getenv("LLM_PROVIDER") or user_config.provider or str(runtime_defaults.get("provider", "llama_cpp"))
    provider = raw_provider.strip().lower()
    if provider not in VALID_PROVIDERS:
        valid = ", ".join(sorted(VALID_PROVIDERS))
        raise StartupConfigError(
            f"Unsupported LLM_PROVIDER {provider!r}. Expected one of: {valid}."
        )

    _parse_positive_int("LLM_N_CTX")
    _parse_positive_int("LLM_MAX_TOKENS")
    _parse_positive_int("LLM_TOKEN_BUDGET")
    _parse_non_negative_float("LLM_TEMPERATURE")

    default_preset = str(runtime_defaults.get("generation_preset", "")).strip().lower() or None
    preset = (
        (args.preset.strip().lower() if isinstance(args.preset, str) and args.preset.strip() else None)
        or os.getenv("LLM_PRESET")
        or user_config.preset
        or default_preset
    )
    preset = preset.strip().lower() if isinstance(preset, str) and preset.strip() else None
    if preset and preset not in PRESETS:
        raise StartupConfigError(
            f"Unsupported preset {preset!r}. Expected one of: {', '.join(sorted(PRESETS))}."
        )

    model = (
        (args.model.strip() if isinstance(args.model, str) and args.model.strip() else None)
        or os.getenv("LLM_MODEL_PATH")
        or user_config.model_path
    )
    if provider == "llama_cpp" and not model:
        raise StartupConfigError(
            "No local model configured for llama_cpp. Use --model or set LLM_MODEL_PATH in .env."
        )
    if provider == "llama_cpp" and model and not os.path.exists(model):
        raise StartupConfigError(
            f"Configured llama_cpp model file does not exist: {model!r}."
        )

    theme = (
        args.theme.strip()
        if isinstance(args.theme, str) and args.theme.strip()
        else user_config.theme or "dark_dungeon"
    )

    return StartupConfig(
        model=model,
        provider=provider,
        theme=theme,
        prompt=args.prompt,
        preset=preset,
        runtime_preset=runtime_preset,
    )


def main(argv: Sequence[str] | None = None) -> int:
    # Import after .env loading because graph_db reads env at import time.
    from cyoa.core.constants import DEFAULT_STARTING_PROMPT, STORY_LOG_FILE, ensure_user_directories
    from cyoa.core.observability import setup_observability
    from cyoa.core.theme_loader import ThemeValidationError, list_themes, load_theme
    from cyoa.core.user_config import update_user_config
    from cyoa.db.story_logger import StoryLogger
    from cyoa.ui.app import CYOAApp

    # Initialize OpenTelemetry
    setup_observability()
    ensure_user_directories()
    parser = _build_parser(list_themes())
    args = parser.parse_args(argv)

    try:
        config = validate_startup_config(args)
    except StartupConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if "LLM_PROVIDER" not in os.environ:
        os.environ["LLM_PROVIDER"] = config.provider
    if config.model and "LLM_MODEL_PATH" not in os.environ:
        os.environ["LLM_MODEL_PATH"] = config.model
    if config.preset and "LLM_PRESET" not in os.environ:
        os.environ["LLM_PRESET"] = config.preset

    update_user_config(
        provider=config.provider,
        model_path=config.model,
        theme=config.theme,
        preset=config.preset,
        runtime_preset=config.runtime_preset,
    )

    # --prompt overrides --theme
    if config.prompt:
        starting_prompt = config.prompt
        spinner_frames = ["[-]", "[\\]", "[|]", "[/]"]
        accent_color = None
        initial_world_state = {}
        initial_prompt_config = {}
    else:
        try:
            theme = load_theme(config.theme)
            starting_prompt = theme.get("prompt", DEFAULT_STARTING_PROMPT)
            spinner_frames = theme.get("spinner_frames", ["[-]", "[\\]", "[|]", "[/]"])
            accent_color = theme.get("accent_color")
            initial_world_state = {
                "inventory": theme.get("opening_inventory", []),
                "player_stats": theme.get("opening_stats", {}),
                "objectives": theme.get("opening_objectives", []),
                "faction_reputation": theme.get("faction_reputation", {}),
                "npc_affinity": theme.get("npc_affinity", {}),
                "story_flags": theme.get("story_flags", []),
            }
            initial_prompt_config = {
                "goals": theme.get("goals", []),
                "directives": theme.get("directives", []),
                "persona": theme.get("persona"),
            }
        except (FileNotFoundError, ThemeValidationError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2

    # Initialize a global log listener
    logger_service = StoryLogger(filepath=STORY_LOG_FILE)

    app = CYOAApp(
        model_path=config.model or "",
        starting_prompt=starting_prompt,
        spinner_frames=spinner_frames,
        accent_color=accent_color,
        initial_world_state=initial_world_state,
        initial_prompt_config=initial_prompt_config,
        runtime_diagnostics={
            "runtime_preset": config.runtime_preset or "custom",
            "provider": config.provider,
            "model": (config.model or "(provider default)") if config.provider != "mock" else "mock",
        },
    )

    try:
        app.run()
    except KeyboardInterrupt:
        return 130
    finally:
        logger_service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
