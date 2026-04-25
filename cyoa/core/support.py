import os
import subprocess
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO, cast

from cyoa.core.constants import (
    APP_NAME,
    CONFIG_FILE,
    CRASH_LOG_FILE,
    SAVES_DIR,
    STORY_LOG_FILE,
    get_user_config_dir,
    get_user_data_dir,
    get_user_state_dir,
)


def support_paths() -> dict[str, Path]:
    return {
        "config_dir": get_user_config_dir(),
        "data_dir": get_user_data_dir(),
        "state_dir": get_user_state_dir(),
        "config_file": Path(CONFIG_FILE),
        "saves_dir": Path(SAVES_DIR),
        "story_log_file": Path(STORY_LOG_FILE),
        "crash_log_file": Path(CRASH_LOG_FILE),
    }


def open_private_text_file(path: str | Path, mode: str) -> TextIO:
    """Open a text file with owner-only permissions where supported."""
    if mode not in {"w", "a"}:
        raise ValueError(f"Unsupported mode {mode!r}; expected 'w' or 'a'.")

    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)

    flags = os.O_WRONLY | os.O_CREAT
    if mode == "w":
        flags |= os.O_TRUNC
    else:
        flags |= os.O_APPEND

    fd = os.open(target, flags, 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        return cast(TextIO, os.fdopen(fd, mode, encoding="utf-8"))
    except Exception:
        os.close(fd)
        raise


def reveal_in_file_manager(path: str | Path) -> tuple[bool, str]:
    target = Path(path).expanduser()
    target.mkdir(parents=True, exist_ok=True)

    if sys.platform == "darwin":
        command = ["open", str(target)]
    elif sys.platform == "win32":
        command = ["explorer", str(target)]
    else:
        command = ["xdg-open", str(target)]

    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (FileNotFoundError, OSError, subprocess.CalledProcessError):
        return False, str(target)
    return True, str(target)


def write_crash_log(
    exc: BaseException,
    *,
    resolved_config: dict[str, Any] | None = None,
    runtime_diagnostics: dict[str, Any] | None = None,
) -> Path:
    crash_log_path = Path(CRASH_LOG_FILE)
    crash_log_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        f"{APP_NAME} crash report",
        f"timestamp_utc: {datetime.now(UTC).isoformat()}",
        f"platform: {sys.platform}",
        f"python: {sys.version.split()[0]}",
        f"cwd: {os.getcwd()}",
        f"exception: {type(exc).__name__}: {exc}",
        "",
        "support_paths:",
    ]

    for key, value in support_paths().items():
        lines.append(f"  {key}: {value}")

    if resolved_config:
        lines.extend(["", "resolved_config:"])
        for key, value in resolved_config.items():
            lines.append(f"  {key}: {value}")

    if runtime_diagnostics:
        lines.extend(["", "runtime_diagnostics:"])
        for key, value in runtime_diagnostics.items():
            lines.append(f"  {key}: {value}")

    lines.extend(["", "traceback:", traceback.format_exc().rstrip(), ""])
    with open_private_text_file(crash_log_path, "w") as handle:
        handle.write("\n".join(lines))
    return crash_log_path
