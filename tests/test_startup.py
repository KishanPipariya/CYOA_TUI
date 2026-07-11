import argparse
from unittest.mock import patch

from cyoa.core.startup import validate_startup_config
from cyoa.core.user_config import UserConfig


def _args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "model": None,
        "theme": None,
        "prompt": None,
        "preset": None,
        "runtime_preset": None,
        "screen_reader": False,
        "high_contrast": False,
        "reduced_motion": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _clear_startup_env(monkeypatch) -> None:
    for name in (
        "APP_RUNTIME_PRESET",
        "LLM_PROVIDER",
        "LLM_MODEL_PATH",
        "LLM_PRESET",
        "LLM_N_CTX",
        "LLM_MAX_TOKENS",
        "LLM_TOKEN_BUDGET",
        "LLM_TEMPERATURE",
    ):
        monkeypatch.delenv(name, raising=False)


def test_startup_cli_values_override_env_and_saved_settings(monkeypatch, tmp_path) -> None:
    _clear_startup_env(monkeypatch)
    cli_model = tmp_path / "cli.gguf"
    cli_model.write_text("stub", encoding="utf-8")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_PRESET", "balanced")
    monkeypatch.setenv("APP_RUNTIME_PRESET", "mock-smoke")

    with patch(
        "cyoa.core.user_config.load_user_config",
        return_value=UserConfig(
            provider="mock",
            model_path="/models/saved.gguf",
            theme="space_explorer",
            preset="cinematic",
            runtime_preset="local-fast",
        ),
    ):
        config = validate_startup_config(
            _args(
                model=str(cli_model),
                theme="neon_heist",
                preset="precise",
                runtime_preset="local-quality",
            )
        )

    assert config.model == str(cli_model)
    assert config.provider == "llama_cpp"
    assert config.theme == "neon_heist"
    assert config.preset == "precise"
    assert config.runtime_preset == "local-quality"


def test_startup_env_values_override_saved_settings(monkeypatch, tmp_path) -> None:
    _clear_startup_env(monkeypatch)
    env_model = tmp_path / "env.gguf"
    env_model.write_text("stub", encoding="utf-8")
    monkeypatch.setenv("LLM_PROVIDER", "llama_cpp")
    monkeypatch.setenv("LLM_MODEL_PATH", str(env_model))
    monkeypatch.setenv("LLM_PRESET", "precise")
    monkeypatch.setenv("APP_RUNTIME_PRESET", "local-fast")

    with patch(
        "cyoa.core.user_config.load_user_config",
        return_value=UserConfig(
            provider="mock",
            model_path="/models/saved.gguf",
            theme="space_explorer",
            preset="cinematic",
            runtime_preset="mock-smoke",
        ),
    ):
        config = validate_startup_config(_args())

    assert config.model == str(env_model)
    assert config.provider == "llama_cpp"
    assert config.theme == "space_explorer"
    assert config.preset == "precise"
    assert config.runtime_preset == "local-fast"


def test_startup_saved_settings_fill_gaps_before_runtime_defaults(monkeypatch) -> None:
    _clear_startup_env(monkeypatch)

    with patch(
        "cyoa.core.user_config.load_user_config",
        return_value=UserConfig(
            provider="mock",
            theme="space_explorer",
            preset="cinematic",
            runtime_preset="mock-smoke",
        ),
    ):
        config = validate_startup_config(_args())

    assert config.provider == "mock"
    assert config.theme == "space_explorer"
    assert config.preset == "cinematic"
    assert config.runtime_preset == "mock-smoke"


def test_startup_runtime_preset_supplies_provider_and_preset_defaults(monkeypatch) -> None:
    _clear_startup_env(monkeypatch)

    with patch("cyoa.core.user_config.load_user_config", return_value=UserConfig()):
        config = validate_startup_config(_args(runtime_preset="mock-smoke"))

    assert config.provider == "mock"
    assert config.preset == "precise"
    assert config.runtime_preset == "mock-smoke"


def test_startup_default_theme_is_stable_when_no_source_provides_one(monkeypatch) -> None:
    _clear_startup_env(monkeypatch)

    with patch("cyoa.core.user_config.load_user_config", return_value=UserConfig(theme="")):
        config = validate_startup_config(_args())

    assert config.theme == "dark_dungeon"
