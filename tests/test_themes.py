import pytest
import tomllib
import os
from pathlib import Path
from cyoa.core.theme_loader import THEMES_DIR, list_themes, load_theme

def test_themes_directory_exists():
    """Verify that the themes directory exists and contains at least one theme."""
    assert os.path.isdir(THEMES_DIR)
    assert len(list_themes()) > 0

@pytest.mark.parametrize("theme_name", list_themes())
def test_valid_theme_structure(theme_name):
    """Ensure every theme file in the directory has all required keys and valid types."""
    theme = load_theme(theme_name)
    
    required_keys = {
        "name": str,
        "description": str,
        "prompt": str,
        "spinner_frames": list,
        "accent_color": str
    }
    
    for key, expected_type in required_keys.items():
        assert key in theme, f"Theme '{theme_name}' is missing required key: '{key}'"
        assert isinstance(theme[key], expected_type), f"Theme '{theme_name}' key '{key}' should be {expected_type.__name__}, but got {type(theme[key]).__name__}"

    # Specifically check spinner_frames contents
    if "spinner_frames" in theme:
        assert len(theme["spinner_frames"]) > 0, f"Theme '{theme_name}' spinner_frames should not be empty."
        for frame in theme["spinner_frames"]:
            assert isinstance(frame, str), f"Theme '{theme_name}' spinner frame '{frame}' should be a string."

def test_load_non_existent_theme():
    """Verify loading a non-existent theme raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_theme("non_existent_theme_9999")
