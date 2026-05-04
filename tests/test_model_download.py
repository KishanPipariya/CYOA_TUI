import threading
from pathlib import Path

import pytest

from cyoa.core.model_download import (
    DownloadProgress,
    ModelDownloadCancelled,
    ModelRecommendation,
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
        lambda: ModelRecommendation(
            label="7B (Balanced - Q5_K_M)",
            filename=model_path.name,
            repo_id="Qwen/demo",
            ram_gb=16.0,
            minimum_ram_gb=12.0,
            approx_size_gb=5.0,
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
    cancel_event = threading.Event()
    cancel_event.set()
    monkeypatch.setattr("cyoa.core.model_download.get_models_dir", lambda: tmp_path)

    with pytest.raises(ModelDownloadCancelled):
        download_recommended_model(cancel_event=cancel_event)
