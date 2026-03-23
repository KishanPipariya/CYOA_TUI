import json
from typing import Any
from cyoa.core.constants import CONFIG_FILE

def load_config() -> dict[str, Any]:
    """Load UI preferences from the local config file."""
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_config(data: dict[str, Any]) -> None:
    """Save UI preferences to the local config file."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f)
