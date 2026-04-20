import argparse
import io
import tomllib
from unittest.mock import MagicMock, patch

import pytest

import main
from cyoa.core.theme_loader import ThemeValidationError
from cyoa.core.user_config import UserConfig


def _args(**overrides: str | None) -> argparse.Namespace:
    values = {
        "model": None,
        "theme": "dark_dungeon",
        "prompt": None,
        "preset": None,
        "runtime_preset": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.mark.smoke
def test_validate_startup_config_requires_model_for_env_llama_cpp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "llama_cpp")

    with pytest.raises(main.StartupConfigError, match="No local model configured"):
        main.validate_startup_config(_args())


def test_validate_startup_config_requires_existing_model_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "llama_cpp")

    with pytest.raises(main.StartupConfigError, match="model file does not exist"):
        main.validate_startup_config(_args(model="missing.gguf"))


def test_validate_startup_config_rejects_ollama_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")

    with pytest.raises(main.StartupConfigError, match="Unsupported LLM_PROVIDER"):
        main.validate_startup_config(_args())


def test_validate_startup_config_rejects_invalid_numeric_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_N_CTX", "zero")

    with pytest.raises(main.StartupConfigError, match="LLM_N_CTX must be an integer"):
        main.validate_startup_config(_args())


def test_validate_startup_config_rejects_non_positive_integer_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_MAX_TOKENS", "0")

    with pytest.raises(main.StartupConfigError, match="LLM_MAX_TOKENS must be greater than 0"):
        main.validate_startup_config(_args())


def test_validate_startup_config_rejects_negative_float_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_TEMPERATURE", "-0.1")

    with pytest.raises(main.StartupConfigError, match="LLM_TEMPERATURE must be non-negative"):
        main.validate_startup_config(_args())


def test_validate_startup_config_rejects_unknown_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "unsupported")

    with pytest.raises(main.StartupConfigError, match="Unsupported LLM_PROVIDER"):
        main.validate_startup_config(_args())


def test_validate_startup_config_rejects_unknown_preset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")

    with pytest.raises(main.StartupConfigError, match="Unsupported preset"):
        main.validate_startup_config(_args(preset="chaos"))


def test_validate_startup_config_applies_runtime_preset_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    config = main.validate_startup_config(_args(runtime_preset="mock-smoke"))

    assert config.runtime_preset == "mock-smoke"
    assert config.provider == "mock"
    assert config.preset == "precise"


def test_validate_startup_config_defaults_to_mock_when_no_provider_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL_PATH", raising=False)
    with patch("cyoa.core.user_config.load_user_config", return_value=UserConfig()):
        config = main.validate_startup_config(_args())

    assert config.provider == "mock"
    assert config.model is None


def test_validate_startup_config_falls_back_from_saved_llama_cpp_to_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_PRESET", raising=False)
    monkeypatch.delenv("LLM_MODEL_PATH", raising=False)

    with patch(
        "cyoa.core.user_config.load_user_config",
        return_value=UserConfig(provider="llama_cpp", model_path="/missing.gguf"),
    ):
        config = main.validate_startup_config(_args())

    assert config.provider == "mock"
    assert config.startup_note == "Configured local model was unavailable. Starting in mock mode instead."


def test_validate_startup_config_uses_saved_user_config_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_PRESET", raising=False)
    monkeypatch.delenv("LLM_MODEL_PATH", raising=False)
    with patch(
        "cyoa.core.user_config.load_user_config",
        return_value=UserConfig(provider="mock", theme="space_explorer", preset="balanced"),
    ):
        config = main.validate_startup_config(_args(theme=None, preset=None, runtime_preset=None))

    assert config.provider == "mock"
    assert config.theme == "space_explorer"
    assert config.preset == "balanced"


def test_validate_startup_config_prefers_cli_args_over_saved_config() -> None:
    with patch(
        "cyoa.core.user_config.load_user_config",
        return_value=UserConfig(
            provider="mock",
            model_path="/models/saved.gguf",
            theme="space_explorer",
            preset="balanced",
        ),
    ):
        config = main.validate_startup_config(
            _args(model="/models/cli.gguf", theme="dark_dungeon", preset="precise")
        )

    assert config.model == "/models/cli.gguf"
    assert config.theme == "dark_dungeon"
    assert config.preset == "precise"


@pytest.mark.smoke
def test_main_closes_logger_on_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    logger_service = MagicMock()
    app = MagicMock()
    app.run.side_effect = KeyboardInterrupt

    with (
        patch("main.os._exit", side_effect=AssertionError("os._exit should not be used")),
        patch("cyoa.core.constants.ensure_user_directories"),
        patch("cyoa.core.observability.setup_observability"),
        patch("cyoa.core.theme_loader.list_themes", return_value=["dark_dungeon"]),
        patch(
            "cyoa.core.theme_loader.load_theme",
            return_value={"prompt": "Start", "spinner_frames": ["-"], "accent_color": None},
        ),
        patch("cyoa.db.story_logger.StoryLogger", return_value=logger_service),
        patch("cyoa.ui.app.CYOAApp", return_value=app) as app_cls,
    ):
        exit_code = main.main([])

    assert exit_code == 130
    logger_service.close.assert_called_once_with()
    app_cls.assert_called_once()


def test_main_returns_error_for_invalid_startup_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "invalid")

    with (
        patch("cyoa.core.constants.ensure_user_directories"),
        patch("cyoa.core.observability.setup_observability"),
        patch("cyoa.core.theme_loader.list_themes", return_value=["dark_dungeon"]),
        patch("cyoa.db.story_logger.StoryLogger") as logger_cls,
        patch("cyoa.ui.app.CYOAApp") as app_cls,
    ):
        exit_code = main.main([])

    assert exit_code == 2
    logger_cls.assert_not_called()
    app_cls.assert_not_called()


def test_main_returns_error_for_invalid_theme_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")

    with (
        patch("cyoa.core.constants.ensure_user_directories"),
        patch("cyoa.core.observability.setup_observability"),
        patch("cyoa.core.theme_loader.list_themes", return_value=["dark_dungeon"]),
        patch(
            "cyoa.core.theme_loader.load_theme",
            side_effect=ThemeValidationError("Theme 'dark_dungeon': prompt must be a non-empty string."),
        ),
        patch("cyoa.db.story_logger.StoryLogger") as logger_cls,
        patch("cyoa.ui.app.CYOAApp") as app_cls,
    ):
        exit_code = main.main([])

    assert exit_code == 2
    logger_cls.assert_not_called()
    app_cls.assert_not_called()


def test_main_bootstraps_user_directories(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")

    with (
        patch("cyoa.core.constants.ensure_user_directories") as ensure_dirs,
        patch("cyoa.core.observability.setup_observability"),
        patch("cyoa.core.theme_loader.list_themes", return_value=["dark_dungeon"]),
        patch(
            "cyoa.core.theme_loader.load_theme",
            return_value={"prompt": "Start", "spinner_frames": ["-"], "accent_color": None},
        ),
        patch("cyoa.db.story_logger.StoryLogger"),
        patch("cyoa.ui.app.CYOAApp") as app_cls,
    ):
        app_cls.return_value.run.return_value = None
        exit_code = main.main([])

    assert exit_code == 0
    ensure_dirs.assert_called_once_with()


def test_main_writes_crash_log_for_unexpected_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    logger_service = MagicMock()
    app = MagicMock()
    app.run.side_effect = RuntimeError("startup exploded")
    stderr = io.StringIO()

    with (
        patch("sys.stderr", stderr),
        patch("cyoa.core.constants.ensure_user_directories"),
        patch("cyoa.core.observability.setup_observability"),
        patch("cyoa.core.theme_loader.list_themes", return_value=["dark_dungeon"]),
        patch(
            "cyoa.core.theme_loader.load_theme",
            return_value={"prompt": "Start", "spinner_frames": ["-"], "accent_color": None},
        ),
        patch("cyoa.core.support.write_crash_log", return_value="/tmp/last_crash.log") as write_crash_log,
        patch("cyoa.db.story_logger.StoryLogger", return_value=logger_service),
        patch("cyoa.ui.app.CYOAApp", return_value=app),
    ):
        exit_code = main.main([])

    assert exit_code == 1
    assert "Unexpected startup failure. Details were written to /tmp/last_crash.log" in stderr.getvalue()
    write_crash_log.assert_called_once()
    logger_service.close.assert_called_once_with()


def test_main_persists_resolved_user_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")

    with (
        patch("cyoa.core.constants.ensure_user_directories"),
        patch("cyoa.core.observability.setup_observability"),
        patch("cyoa.core.theme_loader.list_themes", return_value=["dark_dungeon"]),
        patch(
            "cyoa.core.theme_loader.load_theme",
            return_value={"prompt": "Start", "spinner_frames": ["-"], "accent_color": None},
        ),
        patch("cyoa.core.user_config.update_user_config") as update_config,
        patch("cyoa.db.story_logger.StoryLogger"),
        patch("cyoa.ui.app.CYOAApp") as app_cls,
    ):
        app_cls.return_value.run.return_value = None
        exit_code = main.main([])

    assert exit_code == 0
    update_config.assert_called_once()


def test_pyproject_registers_installed_cli_entrypoint() -> None:
    with open("pyproject.toml", "rb") as handle:
        pyproject = tomllib.load(handle)

    assert pyproject["project"]["scripts"]["cyoa-tui"] == "cyoa.cli:main"
