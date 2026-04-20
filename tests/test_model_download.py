from pathlib import Path
from types import SimpleNamespace

import pytest

from cyoa.core.model_download import (
    DownloadProgress,
    ModelDownloadCancelled,
    download_recommended_model,
    recommend_model,
)


def test_recommend_model_covers_expected_ram_tiers() -> None:
    assert recommend_model(33).filename == "qwen2.5-32b-instruct-q4_k_m.gguf"
    assert recommend_model(24).filename == "qwen2.5-14b-instruct-q5_k_m.gguf"
    assert recommend_model(16).filename == "qwen2.5-7b-instruct-q5_k_m.gguf"
    assert recommend_model(8).filename == "qwen2.5-3b-instruct-q5_k_m.gguf"
    assert recommend_model(4).filename == "qwen2.5-1.5b-instruct-q5_k_m.gguf"


def test_download_recommended_model_uses_cached_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "qwen2.5-7b-instruct-q5_k_m.gguf"
    model_path.write_text("cached", encoding="utf-8")
    events: list[DownloadProgress] = []

    monkeypatch.setattr("cyoa.core.model_download.get_models_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "cyoa.core.model_download.recommend_model_for_current_machine",
        lambda: SimpleNamespace(
            label="7B (Balanced - Q5_K_M)",
            filename=model_path.name,
            repo_id="Qwen/demo",
        ),
    )

    result = download_recommended_model(progress_callback=events.append)

    assert result.path == str(model_path)
    assert result.from_cache is True
    assert events[-1].percent == 100


def test_download_recommended_model_honors_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cancel_event = SimpleNamespace(is_set=lambda: True)
    monkeypatch.setattr("cyoa.core.model_download.get_models_dir", lambda: tmp_path)

    with pytest.raises(ModelDownloadCancelled):
        download_recommended_model(cancel_event=cancel_event)
