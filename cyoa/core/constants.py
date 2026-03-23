"""
Centralised constants and configuration for the CYOA TUI project.
"""

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

STREAM_RENDER_THROTTLE_BASE = 8
STREAM_RENDER_THROTTLE_MAX = 48
MAX_CHOICE_PREVIEW_LEN = 15

CONFIG_FILE = ".config.json"
SAVES_DIR = "saves"
STORY_LOG_FILE = "story.md"

# Error marker used to detect fallback nodes from LLM failures
ERROR_NARRATIVE_PREFIX = "The universe encounters an anomaly"

# --- LLM Defaults ---

# Characters-per-token estimate (conservative for English prose).
CHARS_PER_TOKEN = 4
