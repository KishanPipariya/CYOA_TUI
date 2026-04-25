"""
Centralised constants and configuration for the CYOA TUI project.
"""

import os
import sys
from pathlib import Path

APP_NAME = "cyoa-tui"
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _platform_home() -> Path:
    home = os.environ.get("HOME")
    if home:
        return Path(home).expanduser()
    return Path.home()


def _current_platform() -> str:
    """Return the active platform name without letting type checkers constant-fold it."""
    return os.environ.get("CYOA_PLATFORM", sys.platform)


def get_user_config_dir() -> Path:
    """Return the platform-appropriate directory for durable user config."""
    override = os.environ.get("CYOA_CONFIG_DIR")
    if override:
        return Path(override).expanduser()

    home = _platform_home()
    platform_name = _current_platform()
    if platform_name == "darwin":
        return home / "Library" / "Application Support" / APP_NAME
    if platform_name == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / APP_NAME
        return home / "AppData" / "Roaming" / APP_NAME

    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home).expanduser() / APP_NAME
    return home / ".config" / APP_NAME


def get_user_data_dir() -> Path:
    """Return the platform-appropriate directory for saves and exports."""
    override = os.environ.get("CYOA_DATA_DIR")
    if override:
        return Path(override).expanduser()

    home = _platform_home()
    platform_name = _current_platform()
    if platform_name == "darwin":
        return home / "Library" / "Application Support" / APP_NAME
    if platform_name == "win32":
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            return Path(local_appdata) / APP_NAME
        return home / "AppData" / "Local" / APP_NAME

    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return Path(xdg_data_home).expanduser() / APP_NAME
    return home / ".local" / "share" / APP_NAME


def get_user_state_dir() -> Path:
    """Return the platform-appropriate directory for logs and transient state."""
    override = os.environ.get("CYOA_STATE_DIR")
    if override:
        return Path(override).expanduser()

    home = _platform_home()
    platform_name = _current_platform()
    if platform_name == "darwin":
        return home / "Library" / "Logs" / APP_NAME
    if platform_name == "win32":
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            return Path(local_appdata) / APP_NAME / "Logs"
        return home / "AppData" / "Local" / APP_NAME / "Logs"

    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        return Path(xdg_state_home).expanduser() / APP_NAME
    return home / ".local" / "state" / APP_NAME


def ensure_user_directories() -> None:
    """Create the app's user-facing storage directories if they do not yet exist."""
    get_user_config_dir().mkdir(parents=True, exist_ok=True)
    get_user_data_dir().mkdir(parents=True, exist_ok=True)
    get_user_state_dir().mkdir(parents=True, exist_ok=True)

# --- Narrative & Gameplay ---

DEFAULT_STARTING_PROMPT = """You are a dark fantasy interactive fiction engine.
Describe the starting scenario where the player wakes up in a cold, unfamiliar dungeon cell.
Provide 2-3 choices for what they can do next.
You MUST provide a creative 'title' for this new adventure in the JSON response.
Manage the player's stats (health, gold, reputation) using 'stat_updates'. Provide stat changes (e.g. {"health": -10, "gold": 50}) when the narrative dictates it. Low health disables risky choices, high reputation unlocks dialogue.
When the story reaches a definitive conclusion (victory, death, escape, etc), set 'is_ending' to true and provide an empty choices list.
Ensure your output is strictly valid JSON matching the requested schema.
"""

# Keywords used to match ASCII art scenes to narrative content
SCENE_KEYWORDS: dict[str, list[str]] = {
    "dungeon": ["dungeon", "cell", "prison", "chains", "shackles"],
    "forest": ["forest", "woods", "trees", "grove", "woodland"],
    "castle": ["castle", "throne", "tower", "battlements", "keep"],
    "town": ["town", "village", "market", "tavern", "inn", "shop"],
    "cave": ["cave", "cavern", "grotto", "underground", "stalactite"],
    "mountain": ["mountain", "peak", "cliff", "ridge", "summit"],
    "ruins": ["ruins", "ruin", "ancient", "crumbling", "temple"],
}

# --- UI & Rendering ---

# Load the ASCII art for the initial screen
try:
    with (_REPO_ROOT / "loading_art.md").open(encoding="utf-8") as f:
        LOADING_ART = f.read()
except FileNotFoundError:
    LOADING_ART = "# Welcome to the Adventure\n\n*Loading the AI model... Please wait.*"

STREAM_RENDER_THROTTLE_BASE = 8
STREAM_RENDER_THROTTLE_MAX = 48
MAX_CHOICE_PREVIEW_LEN = 15

# --- Typewriter Narrator ---
TYPEWRITER_SPEEDS: dict[str, float] = {
    "slow": 0.05,
    "normal": 0.02,
    "fast": 0.005,
    "instant": 0.0,
}
TEXT_SCALE_OPTIONS: tuple[str, ...] = ("standard", "large", "xlarge")
READING_WIDTH_OPTIONS: tuple[str, ...] = ("focused", "standard", "full")
LINE_SPACING_OPTIONS: tuple[str, ...] = ("compact", "standard", "relaxed")
TYPEWRITER_CHAR_DELAY = 0.02  # seconds per character (legacy, kept for compat)
TYPEWRITER_CATCHUP_THRESHOLD = 50  # if queue > 50, speed up reveal
TYPEWRITER_MAX_BATCH = 5  # max characters to reveal per tick during catchup

CONFIG_FILE = str(get_user_config_dir() / "config.json")
SAVES_DIR = str(get_user_data_dir() / "saves")
MODELS_DIR = str(get_user_data_dir() / "models")
STORY_LOG_FILE = str(get_user_state_dir() / "story.md")
CRASH_LOG_FILE = str(get_user_state_dir() / "last_crash.log")

# Error marker used to detect fallback nodes from LLM failures
ERROR_NARRATIVE_PREFIX = "The universe encounters an anomaly"

# --- LLM Defaults ---

# Characters-per-token estimate (conservative for English prose).
CHARS_PER_TOKEN = 4

# LLM Hyperparameters
DEFAULT_LLM_N_CTX = 4096
DEFAULT_LLM_TEMPERATURE = 0.6
DEFAULT_LLM_MAX_TOKENS = 512
DEFAULT_LLM_SUMMARY_MAX_TOKENS = 200
DEFAULT_LLM_REPAIR_ATTEMPTS = 2
DEFAULT_LLM_SUMMARY_THRESHOLD = 0.8

# --- Database & Persistence ---

DEFAULT_NEO4J_URI = "bolt://localhost:7687"
