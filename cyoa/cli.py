import argparse
import os
import sys
from collections.abc import Sequence

from dotenv import load_dotenv

from cyoa.core.startup import StartupConfig, StartupConfigError, validate_startup_config

# Load .env before anything that reads os.getenv (graph_db)
load_dotenv()

__all__ = ["StartupConfig", "StartupConfigError", "main", "validate_startup_config"]


def _is_terminal_attach_failure(exc: BaseException) -> bool:
    message = str(exc).strip().lower()
    if "attach failed" in message:
        return True
    return type(exc).__name__.lower() == "attacherror"


def _build_parser(available_themes: Sequence[str] | None = None) -> argparse.ArgumentParser:
    themes_help = "Story theme to use (default: dark_dungeon)."
    if available_themes:
        themes_help = (
            f"Story theme to use. Available: {', '.join(available_themes)} (default: dark_dungeon)"
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
        help="Runtime profile to apply (local-quality, local-fast, mock-smoke).",
    )
    parser.add_argument(
        "--screen-reader",
        action="store_true",
        help="Start this session in screen reader mode without changing saved settings.",
    )
    parser.add_argument(
        "--high-contrast",
        action="store_true",
        help="Start this session in high contrast mode without changing saved settings.",
    )
    parser.add_argument(
        "--reduced-motion",
        action="store_true",
        help="Start this session with reduced motion without changing saved settings.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # Import after .env loading because graph_db reads env at import time.
    from cyoa.core.constants import DEFAULT_STARTING_PROMPT, STORY_LOG_FILE, ensure_user_directories
    from cyoa.core.observability import setup_observability
    from cyoa.core.support import write_crash_log
    from cyoa.core.theme_loader import ThemeValidationError, list_themes, load_theme
    from cyoa.core.user_config import update_user_config
    from cyoa.db.story_logger import StoryLogger
    from cyoa.ui.app import CYOAApp, CYOAAppConfig

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

    if config.prompt:
        starting_prompt = config.prompt
        spinner_frames = ["[-]", "[\\]", "[|]", "[/]"]
        accent_color = None
        ui_theme = {}
        initial_world_state = {}
        initial_prompt_config = {}
    else:
        try:
            theme = load_theme(config.theme)
            starting_prompt = theme.get("prompt", DEFAULT_STARTING_PROMPT)
            spinner_frames = theme.get("spinner_frames", ["[-]", "[\\]", "[|]", "[/]"])
            accent_color = theme.get("accent_color")
            ui_theme = theme.get("ui", {})
            initial_world_state = {
                "inventory": theme.get("opening_inventory", []),
                "player_stats": theme.get("opening_stats", {}),
                "objectives": theme.get("opening_objectives", []),
                "companions": theme.get("opening_companions", []),
                "faction_reputation": theme.get("faction_reputation", {}),
                "npc_affinity": theme.get("npc_affinity", {}),
                "story_flags": theme.get("story_flags", []),
                "campaign": theme.get("campaign"),
            }
            initial_prompt_config = {
                "goals": theme.get("goals", []),
                "directives": theme.get("directives", []),
                "persona": theme.get("persona"),
            }
        except (FileNotFoundError, ThemeValidationError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

    logger_service = StoryLogger(filepath=STORY_LOG_FILE)

    app = CYOAApp(
        CYOAAppConfig(
            model_path=config.model or "",
            starting_prompt=starting_prompt,
            spinner_frames=spinner_frames,
            accent_color=accent_color,
            ui_theme=ui_theme,
            initial_world_state=initial_world_state,
            initial_prompt_config=initial_prompt_config,
            runtime_diagnostics={
                "runtime_preset": config.runtime_preset or "custom",
                "provider": config.provider,
                "model": (config.model or "(provider default)")
                if config.provider != "mock"
                else "mock",
                "startup_note": config.startup_note or "",
            },
            startup_accessibility_overrides=config.startup_accessibility_overrides,
        )
    )

    try:
        app.run()
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        if _is_terminal_attach_failure(exc):
            print(
                (
                    "Error: the Textual UI could not attach to this terminal session. "
                    "Run `cyoa-tui` in a real interactive terminal such as Terminal, iTerm2, "
                    "Windows Terminal, Kitty, or Alacritty."
                ),
                file=sys.stderr,
            )
            return 2
        crash_log_path = write_crash_log(
            exc,
            resolved_config={
                "provider": config.provider,
                "model": config.model,
                "theme": config.theme,
                "preset": config.preset,
                "runtime_preset": config.runtime_preset,
            },
            runtime_diagnostics=dict(app._runtime_diagnostics),
        )
        print(
            f"Unexpected startup failure. Details were written to {crash_log_path}",
            file=sys.stderr,
        )
        return 1
    finally:
        logger_service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
