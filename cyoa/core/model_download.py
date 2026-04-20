import os
import platform
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cyoa.core.constants import MODELS_DIR

REPO_USER = "Qwen"


class ModelDownloadError(RuntimeError):
    """Raised when guided model download fails with a user-facing message."""


class ModelDownloadCancelled(ModelDownloadError):
    """Raised when the user cancels a guided model download."""


@dataclass(slots=True, frozen=True)
class ModelRecommendation:
    repo_id: str
    filename: str
    label: str
    ram_gb: float


@dataclass(slots=True, frozen=True)
class DownloadProgress:
    percent: int
    stage: str
    detail: str


@dataclass(slots=True, frozen=True)
class DownloadResult:
    recommendation: ModelRecommendation
    path: str
    from_cache: bool


def get_total_ram_gb() -> float:
    """Detect total system memory with conservative fallback."""
    try:
        system_name = platform.system()
        if system_name == "Darwin":
            value = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip()
            return int(value) / (1024**3)
        if system_name == "Linux":
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024**3)
    except Exception:
        return 8.0
    return 8.0


def recommend_model(ram_gb: float) -> ModelRecommendation:
    """Recommend a default GGUF based on available RAM."""
    if ram_gb >= 32:
        return ModelRecommendation(
            repo_id=f"{REPO_USER}/Qwen2.5-32B-Instruct-GGUF",
            filename="qwen2.5-32b-instruct-q4_k_m.gguf",
            label="32B (Ultra - Q4_K_M)",
            ram_gb=ram_gb,
        )
    if ram_gb >= 24:
        return ModelRecommendation(
            repo_id=f"{REPO_USER}/Qwen2.5-14B-Instruct-GGUF",
            filename="qwen2.5-14b-instruct-q5_k_m.gguf",
            label="14B (High - Q5_K_M)",
            ram_gb=ram_gb,
        )
    if ram_gb >= 16:
        return ModelRecommendation(
            repo_id=f"{REPO_USER}/Qwen2.5-7B-Instruct-GGUF",
            filename="qwen2.5-7b-instruct-q5_k_m.gguf",
            label="7B (Balanced - Q5_K_M)",
            ram_gb=ram_gb,
        )
    if ram_gb >= 8:
        return ModelRecommendation(
            repo_id=f"{REPO_USER}/Qwen2.5-3B-Instruct-GGUF",
            filename="qwen2.5-3b-instruct-q5_k_m.gguf",
            label="3B (Lite - Q5_K_M)",
            ram_gb=ram_gb,
        )
    return ModelRecommendation(
        repo_id=f"{REPO_USER}/Qwen2.5-1.5B-Instruct-GGUF",
        filename="qwen2.5-1.5b-instruct-q5_k_m.gguf",
        label="1.5B (Pocket - Q5_K_M)",
        ram_gb=ram_gb,
    )


def recommend_model_for_current_machine() -> ModelRecommendation:
    return recommend_model(get_total_ram_gb())


def get_models_dir() -> Path:
    return Path(MODELS_DIR)


def download_recommended_model(
    *,
    progress_callback: Callable[[DownloadProgress], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> DownloadResult:
    """Download the recommended GGUF into the durable user models directory."""
    def publish(percent: int, stage: str, detail: str) -> None:
        if progress_callback is not None:
            progress_callback(DownloadProgress(percent=percent, stage=stage, detail=detail))

    def check_cancel() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise ModelDownloadCancelled("Model download cancelled.")

    publish(5, "Inspecting system", "Checking available memory and recommending a local model.")
    check_cancel()
    recommendation = recommend_model_for_current_machine()

    models_dir = get_models_dir()
    target_path = models_dir / recommendation.filename
    if target_path.exists():
        publish(100, "Ready", f"Using cached model at {target_path}.")
        return DownloadResult(
            recommendation=recommendation,
            path=str(target_path),
            from_cache=True,
        )

    publish(15, "Preparing storage", f"Saving the model under {models_dir}.")
    try:
        models_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ModelDownloadError(f"Unable to prepare the model directory: {exc}.") from exc

    check_cancel()
    publish(35, "Connecting to Hugging Face", f"Fetching {recommendation.label} from {recommendation.repo_id}.")

    try:
        from huggingface_hub import hf_hub_download
    except ModuleNotFoundError as exc:
        raise ModelDownloadError(
            "Model downloads require the 'huggingface-hub' package, but it is not installed in this environment."
        ) from exc

    try:
        downloaded_path = hf_hub_download(
            repo_id=recommendation.repo_id,
            filename=recommendation.filename,
            local_dir=models_dir,
            local_dir_use_symlinks=False,
        )
    except KeyboardInterrupt as exc:
        raise ModelDownloadCancelled("Model download cancelled.") from exc
    except Exception as exc:
        raise ModelDownloadError(
            "Download failed. Check your network connection, free disk space, and Hugging Face access, then try again."
        ) from exc

    check_cancel()
    publish(95, "Finalizing", "Saving the model path for future launches.")
    final_path = Path(downloaded_path)
    publish(100, "Complete", f"Local model ready at {final_path}.")
    return DownloadResult(
        recommendation=recommendation,
        path=str(final_path),
        from_cache=False,
    )
