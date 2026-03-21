import tomllib
from pathlib import Path
from typing import Any

THEMES_DIR = Path(__file__).parent.parent.parent / "themes"


def load_theme(name: str) -> dict[str, Any]:
    """
    Load a theme by name from the themes/ directory.
    Returns a dict with keys: name, description, accent_color, prompt.
    Raises SystemExit with a helpful message if the theme is not found.
    """
    theme_path = THEMES_DIR / f"{name}.toml"

    if not theme_path.exists():
        available = [p.stem for p in THEMES_DIR.glob("*.toml")]
        raise FileNotFoundError(
            f"Theme '{name}' not found. "
            f"Available themes: {', '.join(sorted(available)) or '(none)'}\n"
            f"Theme files live in: {THEMES_DIR}"
        )

    with open(theme_path, "rb") as f:
        return tomllib.load(f)


def list_themes() -> list[str]:
    """Return a sorted list of all available theme names."""
    return sorted(p.stem for p in THEMES_DIR.glob("*.toml"))
