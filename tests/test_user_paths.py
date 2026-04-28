import importlib
import json
import os
import stat
import sys
from pathlib import Path

from cyoa.core import constants as constants_module
from cyoa.core import utils
from cyoa.core.support import open_private_text_file, write_crash_log
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
        assert Path(constants_module.CRASH_LOG_FILE) == Path(
            "/tmp/test-state/cyoa-tui/last_crash.log"
        )

    importlib.reload(constants_module)


def test_save_config_creates_parent_directory(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "nested" / "config.json"
    monkeypatch.setattr("cyoa.core.user_config.CONFIG_FILE", str(config_path))

    utils.save_config({"dark": True, "typewriter": False})

    assert config_path.exists()
    assert utils.load_config() == {
        "dark": True,
        "high_contrast": False,
        "reduced_motion": False,
        "screen_reader_mode": False,
        "cognitive_load_reduction_mode": False,
        "text_scale": "standard",
        "line_width": "standard",
        "line_spacing": "standard",
        "notification_verbosity": "standard",
        "scene_recap_verbosity": "standard",
        "runtime_metadata_verbosity": "standard",
        "locked_choice_verbosity": "standard",
        "typewriter": False,
        "typewriter_speed": "normal",
    }


def test_user_config_round_trips_known_and_extra_fields(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("cyoa.core.user_config.CONFIG_FILE", str(config_path))

    save_user_config(
        UserConfig(
            provider="llama_cpp",
            model_path="/models/demo.gguf",
            theme="space_explorer",
            dark=False,
            high_contrast=True,
            accessibility_preset="high_contrast",
            cognitive_load_reduction_mode=True,
            text_scale="xlarge",
            line_width="focused",
            line_spacing="relaxed",
            notification_verbosity="minimal",
            scene_recap_verbosity="detailed",
            runtime_metadata_verbosity="minimal",
            locked_choice_verbosity="detailed",
            keybindings={"show_settings": "f2"},
            typewriter=False,
            typewriter_speed="fast",
            preset="balanced",
            runtime_preset="local-fast",
            setup_completed=True,
            setup_choice="download",
            extras={"custom_flag": "enabled"},
        )
    )

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    restored = load_user_config()

    assert payload["version"] == 1
    assert restored.provider == "llama_cpp"
    assert restored.model_path == "/models/demo.gguf"
    assert restored.theme == "space_explorer"
    assert restored.dark is False
    assert restored.high_contrast is True
    assert restored.accessibility_preset == "high_contrast"
    assert restored.cognitive_load_reduction_mode is True
    assert restored.text_scale == "xlarge"
    assert restored.line_width == "focused"
    assert restored.line_spacing == "relaxed"
    assert restored.notification_verbosity == "minimal"
    assert restored.scene_recap_verbosity == "detailed"
    assert restored.runtime_metadata_verbosity == "minimal"
    assert restored.locked_choice_verbosity == "detailed"
    assert restored.keybindings == {"show_settings": "f2"}
    assert restored.typewriter is False
    assert restored.typewriter_speed == "fast"
    assert restored.preset == "balanced"
    assert restored.runtime_preset == "local-fast"
    assert restored.setup_completed is True
    assert restored.setup_choice == "download"
    assert restored.extras == {"custom_flag": "enabled"}


def test_open_private_text_file_uses_owner_only_permissions(tmp_path) -> None:
    target = tmp_path / "private.txt"

    with open_private_text_file(target, "w") as handle:
        handle.write("secret")

    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_write_crash_log_uses_private_permissions(tmp_path, monkeypatch) -> None:
    crash_log_path = tmp_path / "last_crash.log"
    monkeypatch.setattr("cyoa.core.support.CRASH_LOG_FILE", str(crash_log_path))

    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        written = write_crash_log(exc)

    assert written == crash_log_path
    assert crash_log_path.exists()
    if os.name != "nt":
        assert stat.S_IMODE(crash_log_path.stat().st_mode) == 0o600
