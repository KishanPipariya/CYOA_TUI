import argparse
import os
import sys

from dotenv import load_dotenv

# Load .env before anything that reads os.getenv (graph_db)
load_dotenv()

# Import core constants to keep things consistent
from cyoa.core.constants import DEFAULT_STARTING_PROMPT, STORY_LOG_FILE  # noqa: E402
from cyoa.core.theme_loader import list_themes, load_theme  # noqa: E402
from cyoa.db.story_logger import StoryLogger  # noqa: E402
from cyoa.ui.app import CYOAApp  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="CYOA Terminal Game with Local LLM")
    parser.add_argument("--model", type=str, required=True, help="Path to the .gguf model file")
    parser.add_argument(
        "--theme",
        type=str,
        default="dark_dungeon",
        help=f"Story theme to use. Available: {', '.join(list_themes())} (default: dark_dungeon)",
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default=None,
        help="Override the starting prompt directly (takes precedence over --theme).",
    )

    args = parser.parse_args()

    # --prompt overrides --theme
    if args.prompt:
        starting_prompt = args.prompt
        spinner_frames = ["[-]", "[\\]", "[|]", "[/]"]
        accent_color = None
    else:
        try:
            theme = load_theme(args.theme)
            starting_prompt = theme.get("prompt", DEFAULT_STARTING_PROMPT)
            spinner_frames = theme.get("spinner_frames", ["[-]", "[\\]", "[|]", "[/]"])
            accent_color = theme.get("accent_color")
        except FileNotFoundError as e:
            sys.exit(f"Error: {e}")

    # Initialize a global log listener
    logger_service = StoryLogger(filepath=STORY_LOG_FILE)

    app = CYOAApp(
        model_path=args.model,
        starting_prompt=starting_prompt,
        spinner_frames=spinner_frames,
        accent_color=accent_color,
    )

    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        logger_service.close()
        os._exit(0)


if __name__ == "__main__":
    main()
