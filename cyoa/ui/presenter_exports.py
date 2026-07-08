from dataclasses import dataclass
from typing import Any

from cyoa.ui.presenter_shared import (
    _active_objective_texts,
    _choice_export_text,
    _clean_export_text,
    _resolved_choice_check_lines,
    _world_time_summary,
    normalize_verbosity,
)


@dataclass(frozen=True, slots=True)
class AccessibleExportInput:
    story_title: str | None
    turn_count: int | None
    saved_at: str | None
    story_segments: list[dict[str, str]]
    current_story_text: str | None
    directives: list[str]
    inventory: list[str]
    player_stats: dict[str, int]
    objectives: list[Any]
    world_time: Any = None
    last_choice_text: str | None = None
    last_resolved_choice_check: Any = None
    verbosity: str = "standard"


def build_replay_steps(payload: dict[str, object]) -> list[dict[str, object]]:
    """Build linear replay steps from a saved story payload."""
    ui_state = payload.get("ui_state")
    if not isinstance(ui_state, dict):
        return []

    story_segments = ui_state.get("story_segments")
    if not isinstance(story_segments, list):
        return []

    steps: list[dict[str, object]] = []
    turn_number = 0
    for segment in story_segments:
        if not isinstance(segment, dict):
            continue
        kind = segment.get("kind")
        raw_text = segment.get("text")
        if kind not in {"story_turn", "player_choice", "branch_marker"}:
            continue
        if not isinstance(raw_text, str) or not raw_text.strip():
            continue

        text = _clean_export_text(raw_text)
        if kind == "story_turn":
            turn_number += 1
            title = f"Turn {turn_number}"
            label = "Scene"
        elif kind == "player_choice":
            title = f"Choice after Turn {max(turn_number, 1)}"
            label = "Choice"
            text = f"Choice: {_choice_export_text(text)}"
        else:
            title = f"Branch Marker {len(steps) + 1}"
            label = "Branch"

        steps.append(
            {
                "title": title,
                "label": label,
                "text": text,
                "turn": turn_number,
                "index": len(steps) + 1,
            }
        )

    return steps


def _build_accessible_progress_lines(
    *,
    inventory: list[str],
    player_stats: dict[str, int],
    objectives: list[Any],
    world_time: Any = None,
    last_choice_text: str | None,
    last_resolved_choice_check: Any,
    verbosity: str,
) -> list[str]:
    objective_texts = _active_objective_texts(objectives)
    lines = ["Current Progress:"]
    if verbosity == "minimal":
        lines.append(
            "- Stats: "
            f"Health {player_stats.get('health', 100)} | "
            f"Gold {player_stats.get('gold', 0)} | "
            f"Reputation {player_stats.get('reputation', 0)}"
        )
        lines.append(f"- Inventory: {len(inventory)} item(s)")
        lines.append(f"- Objectives: {len(objective_texts)} active")
        return lines

    lines.append(f"- Health: {player_stats.get('health', 100)}")
    lines.append(f"- Gold: {player_stats.get('gold', 0)}")
    lines.append(f"- Reputation: {player_stats.get('reputation', 0)}")
    lines.append(f"- Inventory: {', '.join(inventory) if inventory else 'Empty'}")
    lines.append(f"- Objectives: {' | '.join(objective_texts) if objective_texts else 'None'}")
    world_time_summary = _world_time_summary(world_time)
    if world_time_summary:
        lines.append(f"- World time: {world_time_summary}")
    if last_choice_text:
        lines.append(f"- Last choice: {last_choice_text}")
    lines.extend(f"- {line}" for line in _resolved_choice_check_lines(last_resolved_choice_check))
    if verbosity == "detailed" and objective_texts:
        lines.append("Objective Details:")
        lines.extend(f"- {objective}" for objective in objective_texts)
    return lines


def build_accessible_export(
    *,
    story_title: str | None,
    turn_count: int | None,
    saved_at: str | None,
    story_segments: list[dict[str, str]],
    current_story_text: str | None,
    directives: list[str],
    inventory: list[str],
    player_stats: dict[str, int],
    objectives: list[Any],
    world_time: Any = None,
    last_choice_text: str | None = None,
    last_resolved_choice_check: Any = None,
    verbosity: str = "standard",
) -> str:
    resolved_verbosity = normalize_verbosity(verbosity)
    lines = [f"Title: {story_title or 'Untitled Adventure'}"]
    if isinstance(turn_count, int):
        lines.append(f"Turn Count: {turn_count}")
    if resolved_verbosity != "minimal" and isinstance(saved_at, str) and saved_at.strip():
        lines.append(f"Saved At: {saved_at.strip()}")
    lines.append("")

    if directives and resolved_verbosity != "minimal":
        lines.append("Active Directives:")
        lines.extend(f"- {directive}" for directive in directives if directive.strip())
        lines.append("")

    lines.append("Transcript:")
    rendered_segments = story_segments or (
        [{"kind": "story_turn", "text": current_story_text}]
        if isinstance(current_story_text, str) and current_story_text.strip()
        else []
    )
    for segment in rendered_segments:
        kind = segment.get("kind", "story_turn")
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        if kind == "player_choice":
            lines.append(f"Choice: {_choice_export_text(text)}")
        elif kind == "branch_marker":
            lines.append(f"Branch: {_clean_export_text(text)}")
        else:
            lines.append("Scene:")
            lines.append(_clean_export_text(text))
        lines.append("")

    lines.extend(
        _build_accessible_progress_lines(
            inventory=inventory,
            player_stats=player_stats,
            objectives=objectives,
            world_time=world_time,
            last_choice_text=last_choice_text,
            last_resolved_choice_check=last_resolved_choice_check,
            verbosity=resolved_verbosity,
        )
    )
    return "\n".join(lines).strip() + "\n"
