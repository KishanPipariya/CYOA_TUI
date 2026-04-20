import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cyoa.core.model_download import ModelRecommendation

PreflightSeverity = Literal["info", "warning", "error"]

MIN_TERMINAL_WIDTH = 100
MIN_TERMINAL_HEIGHT = 28
DISK_HEADROOM_GB = 2.0


@dataclass(slots=True, frozen=True)
class PreflightIssue:
    severity: PreflightSeverity
    summary: str
    guidance: str
    blocks_action: bool = False

    def render(self) -> str:
        if self.guidance:
            return f"{self.summary} {self.guidance}"
        return self.summary


@dataclass(slots=True, frozen=True)
class PreflightReport:
    issues: tuple[PreflightIssue, ...] = ()

    @property
    def has_blocking_issues(self) -> bool:
        return any(issue.blocks_action for issue in self.issues)

    @property
    def blocking_reason(self) -> str | None:
        for issue in self.issues:
            if issue.blocks_action:
                return issue.render()
        return None

    def render_lines(self) -> list[str]:
        return [issue.render() for issue in self.issues]


def _existing_path_for_disk_usage(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _free_disk_gb(path: Path) -> float:
    usage = shutil.disk_usage(_existing_path_for_disk_usage(path))
    return usage.free / (1024**3)


def check_terminal_conditions(
    *,
    width: int,
    height: int,
    term: str | None = None,
    is_headless: bool = False,
) -> PreflightReport:
    if is_headless:
        return PreflightReport()

    issues: list[PreflightIssue] = []
    active_term = (term or os.getenv("TERM") or "").strip().lower()
    if active_term == "dumb":
        issues.append(
            PreflightIssue(
                severity="error",
                summary="This terminal session is missing the features the Textual UI expects.",
                guidance="Use Terminal, iTerm2, Windows Terminal, or another modern terminal emulator.",
                blocks_action=True,
            )
        )

    if width < MIN_TERMINAL_WIDTH or height < MIN_TERMINAL_HEIGHT:
        issues.append(
            PreflightIssue(
                severity="warning",
                summary=(
                    f"Your terminal is {width}x{height}. The app reads best at "
                    f"{MIN_TERMINAL_WIDTH}x{MIN_TERMINAL_HEIGHT} or larger."
                ),
                guidance="Resize the terminal if panels or dialogs feel cramped.",
            )
        )

    return PreflightReport(tuple(issues))


def check_local_model_preflight(
    recommendation: ModelRecommendation,
    *,
    models_dir: str | Path,
    width: int,
    height: int,
    term: str | None = None,
    is_headless: bool = False,
) -> PreflightReport:
    issues = list(
        check_terminal_conditions(width=width, height=height, term=term, is_headless=is_headless).issues
    )
    required_disk_gb = recommendation.approx_size_gb + DISK_HEADROOM_GB
    free_disk_gb = _free_disk_gb(Path(models_dir))

    if recommendation.ram_gb < recommendation.minimum_ram_gb:
        issues.append(
            PreflightIssue(
                severity="error",
                summary=(
                    f"This machine reports about {recommendation.ram_gb:.1f} GB RAM, "
                    f"below the recommended {recommendation.minimum_ram_gb:.1f} GB for {recommendation.label}."
                ),
                guidance="Choose Quick Demo, or use a smaller local model on a machine with more memory.",
                blocks_action=True,
            )
        )

    if free_disk_gb < required_disk_gb:
        issues.append(
            PreflightIssue(
                severity="error",
                summary=(
                    f"Only about {free_disk_gb:.1f} GB free disk space is available, "
                    f"but this download needs roughly {required_disk_gb:.1f} GB."
                ),
                guidance="Free disk space or change the model storage location before downloading.",
                blocks_action=True,
            )
        )

    if not issues:
        issues.append(
            PreflightIssue(
                severity="info",
                summary=(
                    f"Machine check passed for {recommendation.label}: "
                    f"{recommendation.ram_gb:.1f} GB RAM detected and about {free_disk_gb:.1f} GB disk free."
                ),
                guidance="The model will be stored in your app data folder for future launches.",
            )
        )

    return PreflightReport(tuple(issues))
