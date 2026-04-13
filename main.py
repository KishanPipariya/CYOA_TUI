import argparse
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from dotenv import load_dotenv

# Load .env before anything that reads os.getenv (graph_db)
load_dotenv()

VALID_PROVIDERS = {"llama_cpp", "ollama", "mock"}


class StartupConfigError(ValueError):
    """Raised when startup configuration is invalid."""


@dataclass(frozen=True)
class StartupConfig:
    model: str | None
    provider: str
    theme: str
    prompt: str | None


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
        default=os.getenv("LLM_MODEL_PATH"),
        help="Path to the .gguf model file (defaults to LLM_MODEL_PATH in .env)",
    )
    parser.add_argument(
        "--theme",
        type=str,
        default="dark_dungeon",
        help=themes_help,
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default=None,
        help="Override the starting prompt directly (takes precedence over --theme).",
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
    provider = os.getenv("LLM_PROVIDER", "llama_cpp").strip().lower()
    if provider not in VALID_PROVIDERS:
        valid = ", ".join(sorted(VALID_PROVIDERS))
        raise StartupConfigError(
            f"Unsupported LLM_PROVIDER {provider!r}. Expected one of: {valid}."
        )

    _parse_positive_int("LLM_N_CTX")
    _parse_positive_int("LLM_MAX_TOKENS")
    _parse_positive_int("LLM_TOKEN_BUDGET")
    _parse_non_negative_float("LLM_TEMPERATURE")

    model = args.model.strip() if isinstance(args.model, str) and args.model.strip() else None
    if provider == "llama_cpp" and not model:
        raise StartupConfigError(
            "No local model configured for llama_cpp. Use --model or set LLM_MODEL_PATH in .env."
        )
    if provider == "llama_cpp" and model and not os.path.exists(model):
        raise StartupConfigError(
            f"Configured llama_cpp model file does not exist: {model!r}."
        )

    return StartupConfig(
        model=model,
        provider=provider,
        theme=args.theme,
        prompt=args.prompt,
    )


def main(argv: Sequence[str] | None = None) -> int:
    # Import after .env loading because graph_db reads env at import time.
    from cyoa.core.constants import DEFAULT_STARTING_PROMPT, STORY_LOG_FILE
    from cyoa.core.observability import setup_observability
    from cyoa.core.theme_loader import list_themes, load_theme
    from cyoa.db.story_logger import StoryLogger
    from cyoa.ui.app import CYOAApp

    # Initialize OpenTelemetry
    setup_observability()
    parser = _build_parser(list_themes())
    args = parser.parse_args(argv)

    try:
        config = validate_startup_config(args)
    except StartupConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    # --prompt overrides --theme
    if config.prompt:
        starting_prompt = config.prompt
        spinner_frames = ["[-]", "[\\]", "[|]", "[/]"]
        accent_color = None
    else:
        try:
            theme = load_theme(config.theme)
            starting_prompt = theme.get("prompt", DEFAULT_STARTING_PROMPT)
            spinner_frames = theme.get("spinner_frames", ["[-]", "[\\]", "[|]", "[/]"])
            accent_color = theme.get("accent_color")
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2

    # Initialize a global log listener
    logger_service = StoryLogger(filepath=STORY_LOG_FILE)

    app = CYOAApp(
        model_path=config.model or "",
        starting_prompt=starting_prompt,
        spinner_frames=spinner_frames,
        accent_color=accent_color,
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
