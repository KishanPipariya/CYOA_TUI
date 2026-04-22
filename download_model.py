import logging
import sys

from cyoa.core.model_download import (
    ModelDownloadCancelled,
    ModelDownloadError,
    download_recommended_model,
)
from cyoa.core.user_config import update_user_config

# Setup logging with a premium feel
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("model-downloader")


def main() -> int:
    logger.info("--- CYOA Model Bootstrapper ---")

    try:
        result = download_recommended_model(
            progress_callback=lambda progress: logger.info("%s: %s", progress.stage, progress.detail)
        )
        update_user_config(
            provider="llama_cpp",
            model_path=result.path,
            preset="balanced",
            runtime_preset="local-fast",
            setup_completed=True,
            setup_choice="download",
        )

        logger.info("--------------------------------------------------")
        logger.info("Recommended model: %s", result.recommendation.label)
        logger.info("SUCCESS: Model ready at %s", result.path)
        logger.info("Application is now ready to launch.")
        logger.info("--------------------------------------------------")
        return 0

    except ModelDownloadCancelled:
        logger.warning("Download cancelled.")
        return 1
    except ModelDownloadError as exc:
        logger.error("Download Error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
