import json
import tomllib
from pathlib import Path

import pytest

from cyoa.core.theme_loader import (
    THEMES_DIR,
    ThemeValidationError,
    get_moods_config,
    list_themes,
    load_theme,
    validate_all_themes,
    validate_moods_config,
    validate_theme,
)

FIXTURE_THEMES_DIR = Path(__file__).parent / "fixtures" / "themes"


def _load_theme_fixture(*parts: str) -> dict:
    fixture_path = FIXTURE_THEMES_DIR.joinpath(*parts)
    with fixture_path.open("rb") as fixture:
        return tomllib.load(fixture)


def test_themes_directory_exists():
    """Verify that the themes directory exists and contains at least one theme."""
    assert THEMES_DIR.is_dir()
    assert len(list_themes()) > 0


@pytest.mark.parametrize("theme_name", list_themes())
def test_load_theme_returns_validated_theme(theme_name: str):
    """Each shipped theme should satisfy the strict runtime contract."""
    theme = load_theme(theme_name)

    assert theme["name"]
    assert theme["description"]
    assert theme["prompt"]
    assert theme["accent_color"]
    assert theme["spinner_frames"]
    assert all(isinstance(frame, str) and frame for frame in theme["spinner_frames"])
    if "opening_objectives" in theme:
        assert theme["opening_objectives"][0]["id"]
        assert theme["opening_objectives"][0]["text"]


def test_validate_theme_rejects_missing_required_field():
    with pytest.raises(ThemeValidationError, match="prompt"):
        validate_theme(
            {
                "name": "Broken",
                "description": "Missing prompt",
                "accent_color": "blue",
                "spinner_frames": ["-"],
            },
            "broken",
        )


def test_validate_theme_rejects_empty_spinner_frames():
    with pytest.raises(ThemeValidationError, match="spinner_frames"):
        validate_theme(
            {
                "name": "Broken",
                "description": "Empty frames",
                "prompt": "Start",
                "accent_color": "blue",
                "spinner_frames": [],
                "ui": {
                    "main_surface": "#111111",
                    "action_dock_surface": "#111111",
                    "side_panel_surface": "#111111",
                    "status_surface": "#111111",
                    "story_card_surface": "#111111",
                    "story_card_muted_surface": "#111111",
                    "player_choice_surface": "#111111",
                    "choice_surface": "#111111",
                    "choice_locked_surface": "#111111",
                },
            },
            "broken",
        )


def test_validate_moods_config_rejects_non_object_entry():
    with pytest.raises(ThemeValidationError, match="must be an object"):
        validate_moods_config({"default": "blue"})


def test_get_moods_config_returns_empty_on_invalid_json(tmp_path, monkeypatch):
    themes_dir = tmp_path / "themes"
    themes_dir.mkdir()
    (themes_dir / "themes.json").write_text(json.dumps({"default": "broken"}), encoding="utf-8")

    monkeypatch.setattr("cyoa.core.theme_loader.THEMES_DIR", themes_dir)
    monkeypatch.setattr("cyoa.core.theme_loader._themes_cached_config", None)

    assert get_moods_config() == {}


def test_validate_all_themes_rejects_invalid_themes_json(tmp_path, monkeypatch):
    themes_dir = tmp_path / "themes"
    themes_dir.mkdir()
    (themes_dir / "demo.toml").write_text(
        "\n".join(
            [
                'name = "Demo"',
                'description = "Demo theme"',
                'accent_color = "blue"',
                'spinner_frames = ["-", "|"]',
                'prompt = "Start"',
                "[ui]",
                'main_surface = "#111111"',
                'action_dock_surface = "#121212"',
                'side_panel_surface = "#131313"',
                'status_surface = "#141414"',
                'story_card_surface = "#151515"',
                'story_card_muted_surface = "#0e0e0e"',
                'player_choice_surface = "#171717"',
                'choice_surface = "#24384d"',
                'choice_locked_surface = "#1a1a1a"',
            ]
        ),
        encoding="utf-8",
    )
    (themes_dir / "themes.json").write_text(json.dumps({"default": "broken"}), encoding="utf-8")

    monkeypatch.setattr("cyoa.core.theme_loader.THEMES_DIR", themes_dir)
    monkeypatch.setattr("cyoa.core.theme_loader._themes_cached_config", None)

    with pytest.raises(ThemeValidationError, match="must be an object"):
        validate_all_themes()


def test_load_non_existent_theme():
    """Verify loading a non-existent theme raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_theme("non_existent_theme_9999")


def test_load_theme_rejects_path_traversal():
    with pytest.raises(FileNotFoundError, match="must resolve inside"):
        load_theme("../secrets")


def test_validate_theme_accepts_richer_content_bundle():
    theme = validate_theme(_load_theme_fixture("passing", "valid_theme.toml"), "fixture_valid")

    assert theme["opening_stats"] == {"focus": 3}
    assert theme["faction_reputation"] == {"Archivists": 1}
    assert theme["opening_objectives"][0]["id"] == "signal"
    assert theme["ui"]["choice_surface"] == "#20303d"


def test_validate_theme_rejects_invalid_ui_bundle():
    with pytest.raises(ThemeValidationError, match="ui must be an object"):
        validate_theme(
            {
                "name": "Broken",
                "description": "Bad UI bundle",
                "prompt": "Start",
                "accent_color": "blue",
                "spinner_frames": ["-"],
                "ui": "cyan",
            },
            "broken",
        )


def test_validate_theme_rejects_missing_required_ui_field():
    with pytest.raises(ThemeValidationError, match="choice_locked_surface"):
        validate_theme(
            {
                "name": "Broken",
                "description": "Missing ui field",
                "prompt": "Start",
                "accent_color": "blue",
                "spinner_frames": ["-"],
                "ui": {
                    "main_surface": "#101010",
                    "action_dock_surface": "#111111",
                    "side_panel_surface": "#121212",
                    "status_surface": "#131313",
                    "story_card_surface": "#141414",
                    "story_card_muted_surface": "#151515",
                    "player_choice_surface": "#161616",
                    "choice_surface": "#171717",
                },
            },
            "broken",
        )


@pytest.mark.parametrize(
    ("fixture_name", "message"),
    [
        ("muted_surface_too_close.toml", "story_card_muted_surface is too close"),
        ("locked_surface_too_close.toml", "choice_locked_surface is too close"),
    ],
)
def test_validate_theme_rejects_failing_accessibility_fixtures(
    fixture_name: str, message: str
) -> None:
    with pytest.raises(ThemeValidationError, match=message):
        validate_theme(_load_theme_fixture("failing", fixture_name), f"fixture_{fixture_name}")
