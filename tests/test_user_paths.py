import importlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from cyoa.core import constants as constants_module
from cyoa.core import utils
from cyoa.core.support import (
    open_private_text_file,
    reveal_in_file_manager,
    write_crash_log,
)
from cyoa.core.user_config import (
    UserConfig,
    UserConfigLoadError,
    load_user_config,
    save_user_config,
)


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


def test_user_path_overrides_take_precedence(monkeypatch, tmp_path) -> None:
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    state_dir = tmp_path / "state"
    monkeypatch.setenv("CYOA_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("CYOA_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CYOA_STATE_DIR", str(state_dir))

    assert constants_module.get_user_config_dir() == config_dir
    assert constants_module.get_user_data_dir() == data_dir
    assert constants_module.get_user_state_dir() == state_dir


def test_user_paths_follow_macos_conventions(monkeypatch) -> None:
    monkeypatch.setenv("HOME", "/tmp/test-home")
    monkeypatch.setenv("CYOA_PLATFORM", "darwin")

    assert constants_module.get_user_config_dir() == Path(
        "/tmp/test-home/Library/Application Support/cyoa-tui"
    )
    assert constants_module.get_user_data_dir() == Path(
        "/tmp/test-home/Library/Application Support/cyoa-tui"
    )
    assert constants_module.get_user_state_dir() == Path("/tmp/test-home/Library/Logs/cyoa-tui")


def test_user_paths_follow_windows_conventions(monkeypatch) -> None:
    monkeypatch.setenv("HOME", "/tmp/test-home")
    monkeypatch.setenv("CYOA_PLATFORM", "win32")
    monkeypatch.setenv("APPDATA", "/tmp/roaming")
    monkeypatch.setenv("LOCALAPPDATA", "/tmp/local")

    assert constants_module.get_user_config_dir() == Path("/tmp/roaming/cyoa-tui")
    assert constants_module.get_user_data_dir() == Path("/tmp/local/cyoa-tui")
    assert constants_module.get_user_state_dir() == Path("/tmp/local/cyoa-tui/Logs")


def test_user_paths_use_windows_fallbacks_and_create_directories(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CYOA_PLATFORM", "win32")
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    constants_module.ensure_user_directories()

    assert constants_module.get_user_config_dir() == home / "AppData/Roaming/cyoa-tui"
    assert constants_module.get_user_data_dir() == home / "AppData/Local/cyoa-tui"
    assert constants_module.get_user_state_dir() == home / "AppData/Local/cyoa-tui/Logs"
    assert constants_module.get_user_config_dir().is_dir()
    assert constants_module.get_user_data_dir().is_dir()
    assert constants_module.get_user_state_dir().is_dir()


def test_default_story_database_uses_the_app_data_root(tmp_path, monkeypatch) -> None:
    from cyoa.db.sqlite_db import CYOASQLiteDB

    monkeypatch.setenv("CYOA_DATA_DIR", str(tmp_path / "app-data"))
    database = CYOASQLiteDB()

    assert database.database_path == tmp_path / "app-data" / "stories.sqlite3"
    assert database.database_path.exists()


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
        "input_timing_profile": "default",
        "confirm_high_impact_actions": False,
        "typewriter": False,
        "typewriter_speed": "normal",
    }


def test_user_config_round_trips_exact_known_fields(tmp_path, monkeypatch) -> None:
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
            input_timing_profile="steady",
            confirm_high_impact_actions=True,
            keybindings={"show_settings": "f2"},
            typewriter=False,
            typewriter_speed="fast",
            preset="balanced",
            runtime_preset="local-fast",
            setup_completed=True,
            setup_choice="download",
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
    assert restored.input_timing_profile == "steady"
    assert restored.confirm_high_impact_actions is True
    assert restored.keybindings == {"show_settings": "f2"}
    assert restored.typewriter is False
    assert restored.typewriter_speed == "fast"
    assert restored.preset == "balanced"
    assert restored.runtime_preset == "local-fast"
    assert restored.setup_completed is True
    assert restored.setup_choice == "download"


def test_existing_invalid_config_is_rejected_without_rewriting_it(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("cyoa.core.user_config.CONFIG_FILE", str(config_path))
    payload = UserConfig().to_dict()
    payload["unknown_setting"] = True
    original = json.dumps(payload)
    config_path.write_text(original, encoding="utf-8")

    with pytest.raises(UserConfigLoadError, match=str(config_path)):
        load_user_config()

    assert config_path.read_text(encoding="utf-8") == original


def test_open_private_text_file_uses_owner_only_permissions(tmp_path) -> None:
    target = tmp_path / "private.txt"

    with open_private_text_file(target, "w") as handle:
        handle.write("secret")

    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_open_private_text_file_rejects_read_mode(tmp_path) -> None:
    with pytest.raises(ValueError, match="Unsupported mode"):
        open_private_text_file(tmp_path / "private.txt", "r")


@pytest.mark.parametrize(
    "platform_name, command", [("darwin", "open"), ("win32", "explorer"), ("linux", "xdg-open")]
)
def test_reveal_in_file_manager_uses_platform_command(
    monkeypatch, tmp_path, platform_name, command
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr("cyoa.core.support.sys.platform", platform_name)
    monkeypatch.setattr(
        "cyoa.core.support.subprocess.run", lambda args, **_kwargs: commands.append(args)
    )

    revealed, path = reveal_in_file_manager(tmp_path / "exports")

    assert revealed is True
    assert path == str(tmp_path / "exports")
    assert commands == [[command, path]]


def test_reveal_in_file_manager_reports_launch_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "cyoa.core.support.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )

    revealed, path = reveal_in_file_manager(tmp_path / "exports")

    assert revealed is False
    assert path == str(tmp_path / "exports")


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


def test_write_crash_log_includes_optional_diagnostics(tmp_path, monkeypatch) -> None:
    crash_log_path = tmp_path / "last_crash.log"
    monkeypatch.setattr("cyoa.core.support.CRASH_LOG_FILE", str(crash_log_path))

    try:
        raise ValueError("bad config")
    except ValueError as exc:
        write_crash_log(
            exc,
            resolved_config={"provider": "mock"},
            runtime_diagnostics={"startup": "complete"},
        )

    contents = crash_log_path.read_text(encoding="utf-8")
    assert "resolved_config:\n  provider: mock" in contents
    assert "runtime_diagnostics:\n  startup: complete" in contents
