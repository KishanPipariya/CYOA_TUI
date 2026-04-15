import json

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
        '\n'.join(
            [
                'name = "Demo"',
                'description = "Demo theme"',
                'accent_color = "blue"',
                'spinner_frames = ["-", "|"]',
                'prompt = "Start"',
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


def test_validate_theme_accepts_richer_content_bundle():
    theme = validate_theme(
        {
            "name": "Bundle",
            "description": "Richer theme",
            "prompt": "Start",
            "accent_color": "cyan",
            "spinner_frames": ["-", "|"],
            "goals": ["Investigate"],
            "directives": ["Respect locks"],
            "opening_inventory": ["Keycard"],
            "opening_stats": {"health": 90},
            "opening_objectives": [{"id": "obj", "text": "Investigate", "status": "active"}],
            "faction_reputation": {"Guild": 2},
            "npc_affinity": {"Ada": 1},
            "story_flags": ["met_ada"],
            "content_tags": ["sci_fi"],
            "persona": "Be precise.",
        },
        "bundle",
    )

    assert theme["opening_stats"] == {"health": 90}
    assert theme["faction_reputation"] == {"Guild": 2}
    assert theme["opening_objectives"][0]["id"] == "obj"
