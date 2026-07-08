import re
import unicodedata
from typing import Any

from cyoa.core import constants

MARKUP_TAG_RE = re.compile(r"\[/?[a-zA-Z][^\]]*\]")


def normalize_verbosity(value: str | None, default: str = "standard") -> str:
    if isinstance(value, str) and value in constants.VERBOSITY_OPTIONS:
        return value
    return default


def _use_plain_labels(*, screen_reader_mode: bool, simplified_mode: bool = False) -> bool:
    return screen_reader_mode or simplified_mode


def _strip_markup(text: str) -> str:
    plain = MARKUP_TAG_RE.sub("", text)
    for token in ("**", "__", "`"):
        plain = plain.replace(token, "")
    return plain.replace("> ", "")


def _strip_leading_decorations(text: str) -> str:
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\ufe0f":
            index += 1
            continue
        if char.isspace():
            index += 1
            continue
        category = unicodedata.category(char)
        if category == "So" or char in {"↩", "↪", "⚙", "⏱", "⟲", "•", "|"}:
            index += 1
            continue
        break
    return text[index:].lstrip(" :-|")


def format_status_message(
    message: str,
    *,
    screen_reader_mode: bool,
    simplified_mode: bool = False,
) -> str:
    cleaned = _strip_markup(message.strip())
    if not _use_plain_labels(
        screen_reader_mode=screen_reader_mode,
        simplified_mode=simplified_mode,
    ):
        return cleaned
    plain = _strip_leading_decorations(cleaned)
    return plain or cleaned


def _locked_reason_lines(
    disabled_reason: str,
    *,
    screen_reader_mode: bool,
    verbosity: str,
) -> list[str]:
    resolved_verbosity = normalize_verbosity(verbosity)
    if resolved_verbosity == "minimal":
        return []
    reason = format_status_message(disabled_reason, screen_reader_mode=screen_reader_mode)
    reason_lines = [part.strip() for part in reason.split("|") if part.strip()]
    return reason_lines or [reason]


def _choice_check_lines(choice: Any) -> list[str]:
    summary_builder = getattr(choice, "check_summary", None)
    if callable(summary_builder):
        summary = summary_builder()
        if isinstance(summary, list):
            return [line for line in summary if isinstance(line, str) and line.strip()]
    return []


def _resolved_choice_check_lines(value: Any) -> list[str]:
    if isinstance(value, dict):
        summary_builder = value.get("summary_lines")
        if callable(summary_builder):
            summary = summary_builder()
        else:
            stat = value.get("stat")
            stat_value = value.get("stat_value")
            difficulty = value.get("difficulty")
            roll = value.get("roll")
            total = value.get("total")
            success = value.get("success")
            stakes = value.get("stakes")
            if not isinstance(stat, str) or not isinstance(success, bool):
                return []
            if not all(isinstance(part, int) for part in (stat_value, difficulty, roll, total)):
                return []
            outcome = "passed" if success else "failed"
            lines = [
                (
                    f"Last check: {stat.replace('_', ' ')} {outcome} "
                    f"({roll} + {stat_value} = {total} vs {difficulty})"
                )
            ]
            if isinstance(stakes, str) and stakes.strip():
                lines.append(f"Stakes: {stakes.strip()}")
            return lines
    else:
        summary_builder = getattr(value, "summary_lines", None)
        if callable(summary_builder):
            summary = summary_builder()
        else:
            summary = None
    if isinstance(summary, list):
        return [line for line in summary if isinstance(line, str) and line.strip()]
    return []


def _format_signed_change(value: int) -> str:
    return f"{value:+d}"


def _format_stat_name(name: str) -> str:
    return name.replace("_", " ").title()


def _active_objective_texts(objectives: list[Any]) -> list[str]:
    active: list[str] = []
    for objective in objectives:
        if isinstance(objective, dict):
            text = objective.get("text")
            status = objective.get("status", "active")
        else:
            text = getattr(objective, "text", None)
            status = getattr(objective, "status", "active")
        if isinstance(text, str) and text.strip() and status == "active":
            active.append(text.strip())
    return active


def _clean_export_text(text: str) -> str:
    plain = text.replace("**", "").replace("__", "").replace("`", "").replace("\r\n", "\n").strip()
    if plain.startswith("[") and plain.endswith("]") and "[/" not in plain and "][" not in plain:
        plain = plain[1:-1].strip()
    return MARKUP_TAG_RE.sub("", plain).strip()


def _choice_export_text(text: str) -> str:
    cleaned = _clean_export_text(text)
    prefix = "you chose:"
    if cleaned.lower().startswith(prefix):
        return cleaned[len(prefix) :].strip()
    return cleaned


def _world_time_summary(world_time: Any) -> str | None:
    if isinstance(world_time, dict):
        day = world_time.get("day")
        hour = world_time.get("hour")
        if isinstance(day, int) and isinstance(hour, int):
            label = _world_time_period(hour)
            return f"Day {day}, {label} ({hour:02d}:00)"
        return None

    summary_builder = getattr(world_time, "summary", None)
    if callable(summary_builder):
        summary = summary_builder()
        return summary if isinstance(summary, str) and summary.strip() else None

    day = getattr(world_time, "day", None)
    hour = getattr(world_time, "hour", None)
    if isinstance(day, int) and isinstance(hour, int):
        return f"Day {day}, {_world_time_period(hour)} ({hour:02d}:00)"
    return None


def _world_time_period(hour: int) -> str:
    if 5 <= hour <= 7:
        return "Dawn"
    if 8 <= hour <= 11:
        return "Morning"
    if 12 <= hour <= 16:
        return "Afternoon"
    if 17 <= hour <= 19:
        return "Dusk"
    return "Night"
