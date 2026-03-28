import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv, set_key
from huggingface_hub import hf_hub_download

# Setup logging with a premium feel
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("model-downloader")

# Constants
MODEL_DIR = Path("models")
ENV_FILE = Path(".env")
REPO_USER = "Qwen"

def get_total_ram_gb() -> float:
    """Detect total system memory."""
    try:
        # Mac / POSIX
        import subprocess
        if sys.platform == "darwin":
            cmd = ["sysctl", "-n", "hw.memsize"]
            val = subprocess.check_output(cmd).decode().strip()
            return int(val) / (1024**3)

        # Generic POSIX (Linux)
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024**3)
    except Exception:
        # Ultimate fallback
        return 8.0

def recommend_model(ram_gb: float) -> tuple[str, str, str]:
    """Recommend a model based on hardware profile."""
    # We use Qwen2.5 as it has excellent reasoning/schema following for the CYOA engine.
    if ram_gb >= 32:
        return (f"{REPO_USER}/Qwen2.5-32B-Instruct-GGUF", "qwen2.5-32b-instruct-q4_k_m.gguf", "32B (Ultra - Q4_K_M)")
    elif ram_gb >= 24:
        return (f"{REPO_USER}/Qwen2.5-14B-Instruct-GGUF", "qwen2.5-14b-instruct-q5_k_m.gguf", "14B (High - Q5_K_M)")
    elif ram_gb >= 16:
        return (f"{REPO_USER}/Qwen2.5-7B-Instruct-GGUF", "qwen2.5-7b-instruct-q5_k_m.gguf", "7B (Balanced - Q5_K_M)")
    elif ram_gb >= 8:
        return (f"{REPO_USER}/Qwen2.5-3B-Instruct-GGUF", "qwen2.5-3b-instruct-q5_k_m.gguf", "3B (Lite - Q5_K_M)")
    else:
        return (f"{REPO_USER}/Qwen2.5-1.5B-Instruct-GGUF", "qwen2.5-1.5b-instruct-q5_k_m.gguf", "1.5B (Pocket - Q5_K_M)")

def check_for_existing_model() -> str | None:
    """Find if a GGUF model already exists in standard locations."""
    if ENV_FILE.exists():
        load_dotenv(ENV_FILE)

    # Priority 1: .env path
    env_model = os.getenv("LLM_MODEL_PATH")
    if env_model and Path(env_model).is_file():
        return env_model

    # Priority 2: Root directory
    root_ggufs = list(Path(".").glob("*.gguf"))
    if root_ggufs:
        return str(root_ggufs[0])

    # Priority 3: models/ directory
    if MODEL_DIR.is_dir():
        model_ggufs = list(MODEL_DIR.glob("*.gguf"))
        if model_ggufs:
            return str(model_ggufs[0])

    return None

def setup_env_file():
    """Ensure .env exists with initial comments if missing."""
    if not ENV_FILE.exists():
        logger.info("Creating new .env file.")
        with open(ENV_FILE, "w") as f:
            f.write("# CYOA TUI Environment Configuration\n\n")

def main():
    logger.info("--- CYOA Model Bootstrapper ---")

    existing = check_for_existing_model()
    if existing:
        logger.info(f"Existing model detected: {existing}")
        logger.info("Weights already present. Skipping download.")
        return

    ram = get_total_ram_gb()
    repo_id, filename, desc = recommend_model(ram)

    logger.info(f"Hardware Detection: {ram:.1f} GB RAM identified.")
    logger.info(f"Target Model: {desc}")
    logger.info(f"Repository: {repo_id}")

    confirm = input(f"\nProceed with download of '{filename}'? (y/n): ")
    if confirm.lower() != 'y':
        logger.warn("Download aborted by user.")
        return

    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    try:
        logger.info("Initializing download via HuggingFace Hub (this may take a while)...")
        final_path = hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            local_dir=MODEL_DIR,
            local_dir_use_symlinks=False
        )

        setup_env_file()
        relative_path = os.path.relpath(final_path, os.getcwd())
        set_key(str(ENV_FILE), "LLM_MODEL_PATH", relative_path)

        logger.info("--------------------------------------------------")
        logger.info(f"SUCCESS: Model downloaded to {relative_path}")
        logger.info("Application is now ready to launch.")
        logger.info("--------------------------------------------------")

    except KeyboardInterrupt:
        logger.warn("\nDownload interrupted by user.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Download Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

