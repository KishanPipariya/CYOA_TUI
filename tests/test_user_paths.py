import importlib
import json
import sys
from pathlib import Path

from cyoa.core import constants as constants_module
from cyoa.core import utils
from cyoa.core.user_config import UserConfig, load_user_config, save_user_config


def test_linux_user_paths_follow_xdg(monkeypatch) -> None:
    with monkeypatch.context() as local:
        local.setenv("HOME", "/tmp/test-home")
        local.setenv("XDG_CONFIG_HOME", "/tmp/test-config")
        local.setenv("XDG_DATA_HOME", "/tmp/test-data")
        local.setenv("XDG_STATE_HOME", "/tmp/test-state")
        local.setattr(sys, "platform", "linux")

        importlib.reload(constants_module)

        assert Path(constants_module.CONFIG_FILE) == Path("/tmp/test-config/cyoa-tui/config.json")
        assert Path(constants_module.SAVES_DIR) == Path("/tmp/test-data/cyoa-tui/saves")
        assert Path(constants_module.STORY_LOG_FILE) == Path("/tmp/test-state/cyoa-tui/story.md")

    importlib.reload(constants_module)


def test_save_config_creates_parent_directory(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "nested" / "config.json"
    monkeypatch.setattr("cyoa.core.user_config.CONFIG_FILE", str(config_path))

    utils.save_config({"dark": True, "typewriter": False})

    assert config_path.exists()
    assert utils.load_config() == {"dark": True, "typewriter": False, "typewriter_speed": "normal"}


def test_user_config_round_trips_known_and_extra_fields(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("cyoa.core.user_config.CONFIG_FILE", str(config_path))

    save_user_config(
        UserConfig(
            provider="ollama",
            model_path="/models/demo.gguf",
            theme="space_explorer",
            dark=False,
            typewriter=False,
            typewriter_speed="fast",
            preset="balanced",
            runtime_preset="ollama-dev",
            setup_completed=True,
            setup_choice="ollama",
            extras={"custom_flag": "enabled"},
        )
    )

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    restored = load_user_config()

    assert payload["version"] == 1
    assert restored.provider == "ollama"
    assert restored.model_path == "/models/demo.gguf"
    assert restored.theme == "space_explorer"
    assert restored.dark is False
    assert restored.typewriter is False
    assert restored.typewriter_speed == "fast"
    assert restored.preset == "balanced"
    assert restored.runtime_preset == "ollama-dev"
    assert restored.setup_completed is True
    assert restored.setup_choice == "ollama"
    assert restored.extras == {"custom_flag": "enabled"}
