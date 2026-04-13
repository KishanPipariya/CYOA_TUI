import argparse
from unittest.mock import MagicMock, patch

import pytest

import main


def _args(**overrides: str | None) -> argparse.Namespace:
    values = {
        "model": None,
        "theme": "dark_dungeon",
        "prompt": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_validate_startup_config_requires_model_for_llama_cpp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "llama_cpp")

    with pytest.raises(main.StartupConfigError, match="No local model configured"):
        main.validate_startup_config(_args())


def test_validate_startup_config_requires_existing_model_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "llama_cpp")

    with pytest.raises(main.StartupConfigError, match="model file does not exist"):
        main.validate_startup_config(_args(model="missing.gguf"))


def test_validate_startup_config_allows_ollama_without_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")

    config = main.validate_startup_config(_args())

    assert config.provider == "ollama"
    assert config.model is None


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


def test_main_closes_logger_on_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    logger_service = MagicMock()
    app = MagicMock()
    app.run.side_effect = KeyboardInterrupt

    with (
        patch("main.os._exit", side_effect=AssertionError("os._exit should not be used")),
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
        patch("cyoa.core.observability.setup_observability"),
        patch("cyoa.core.theme_loader.list_themes", return_value=["dark_dungeon"]),
        patch("cyoa.db.story_logger.StoryLogger") as logger_cls,
        patch("cyoa.ui.app.CYOAApp") as app_cls,
    ):
        exit_code = main.main([])

    assert exit_code == 2
    logger_cls.assert_not_called()
    app_cls.assert_not_called()
