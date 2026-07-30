from dataclasses import dataclass
from typing import Any

from cyoa.ui.presenter_shared import _world_time_summary


@dataclass(frozen=True, slots=True)
class WorldStateSummaryInput:
    story_title: str | None
    turn_count: int
    player_stats: dict[str, int]
    inventory: list[str]
    objectives: list[Any]
    companions: list[Any] | None
    faction_reputation: dict[str, int]
    npc_affinity: dict[str, int]
    story_flags: set[str] | list[str] | None
    world_time: Any = None
    last_choice_text: str | None = None
    current_scene_id: str | None = None
    campaign_progress: Any = None


def build_world_state_summary(  # noqa: C901
    *,
    story_title: str | None,
    turn_count: int,
    player_stats: dict[str, int],
    inventory: list[str],
    objectives: list[Any],
    companions: list[Any] | None,
    faction_reputation: dict[str, int],
    npc_affinity: dict[str, int],
    story_flags: set[str] | list[str] | None,
    world_time: Any = None,
    last_choice_text: str | None = None,
    current_scene_id: str | None = None,
    campaign_progress: Any = None,
) -> str:
    def _normalize_objective(objective: Any) -> tuple[str, str] | None:
        if isinstance(objective, dict):
            text = objective.get("text")
            status = objective.get("status", "active")
        else:
            text = getattr(objective, "text", None)
            status = getattr(objective, "status", "active")
        if not isinstance(text, str) or not text.strip():
            return None
        normalized_status = status if isinstance(status, str) and status.strip() else "active"
        return text.strip(), normalized_status.strip().lower()

    def _normalize_companion(companion: Any) -> dict[str, Any] | None:
        if isinstance(companion, dict):
            name = companion.get("name")
            status = companion.get("status", "available")
            affinity = companion.get("affinity", 0)
            summary = companion.get("summary")
            effect = companion.get("effect")
        else:
            name = getattr(companion, "name", None)
            status = getattr(companion, "status", "available")
            affinity = getattr(companion, "affinity", 0)
            summary = getattr(companion, "summary", None)
            effect = getattr(companion, "effect", None)
        if not isinstance(name, str) or not name.strip():
            return None
        return {
            "name": name.strip(),
            "status": status if isinstance(status, str) and status.strip() else "available",
            "affinity": affinity if isinstance(affinity, int) else 0,
            "summary": summary.strip() if isinstance(summary, str) and summary.strip() else None,
            "effect": effect.strip() if isinstance(effect, str) and effect.strip() else None,
        }

    def _value(source: Any, field: str, default: Any = None) -> Any:
        if isinstance(source, dict):
            return source.get(field, default)
        return getattr(source, field, default)

    def _normalize_campaign_clock(clock: Any) -> str | None:
        label = _value(clock, "label")
        value = _value(clock, "value", 0)
        maximum = _value(clock, "maximum", 0)
        if not isinstance(label, str) or not label.strip():
            return None
        if not isinstance(value, int) or not isinstance(maximum, int):
            return None
        return f"{label.strip()} {value}/{maximum}"

    def _active_campaign_milestones(progress: Any, active_chapter_id: str | None) -> list[Any]:
        if active_chapter_id is None:
            return []
        for chapter in _value(progress, "chapters", []) or []:
            if _value(chapter, "chapter_id") == active_chapter_id:
                return list(_value(chapter, "completed_milestone_ids", []) or [])
        return []

    def _campaign_status_lines(progress: Any) -> list[str]:
        if progress is None:
            return ["- No active campaign"]
        lines: list[str] = []
        active_act_id = _value(progress, "active_act_id")
        active_chapter_id = _value(progress, "active_chapter_id")
        if isinstance(active_act_id, str) and active_act_id.strip():
            lines.append(f"- Act: {active_act_id.strip()}")
        if isinstance(active_chapter_id, str) and active_chapter_id.strip():
            lines.append(f"- Chapter: {active_chapter_id.strip()}")
        started_turn = _value(progress, "started_turn")
        completed_turn = _value(progress, "completed_turn")
        if isinstance(started_turn, int):
            lines.append(f"- Started turn: {started_turn}")
        if isinstance(completed_turn, int):
            lines.append(f"- Completed turn: {completed_turn}")

        clocks = [
            summary
            for clock in _value(progress, "clocks", []) or []
            if (summary := _normalize_campaign_clock(clock)) is not None
        ]
        if clocks:
            lines.append("- Pressure: " + " | ".join(clocks))

        completed_milestones = _active_campaign_milestones(
            progress,
            active_chapter_id if isinstance(active_chapter_id, str) else None,
        )
        if completed_milestones:
            lines.append("- Milestones: " + ", ".join(str(item) for item in completed_milestones))
        return lines or ["- Campaign active"]

    lines = ["## Overview"]
    lines.append(f"- Adventure: {story_title or 'Untitled Adventure'}")
    lines.append(f"- Turn: {turn_count}")
    world_time_summary = _world_time_summary(world_time)
    if world_time_summary:
        lines.append(f"- World Time: {world_time_summary}")
    if current_scene_id:
        lines.append(f"- Scene ID: {current_scene_id}")
    if last_choice_text:
        lines.append(f"- Last choice: {last_choice_text}")

    lines.extend(
        [
            "",
            "## Stats",
            f"- Health: {player_stats.get('health', 100)}",
            f"- Gold: {player_stats.get('gold', 0)}",
            f"- Reputation: {player_stats.get('reputation', 0)}",
            "",
            "## Inventory",
        ]
    )
    if inventory:
        lines.extend(f"- {item}" for item in inventory)
    else:
        lines.append("- Empty")

    objective_buckets: dict[str, list[str]] = {
        "active": [],
        "completed": [],
        "failed": [],
        "other": [],
    }
    for objective in objectives:
        normalized = _normalize_objective(objective)
        if normalized is None:
            continue
        text, status = normalized
        if status in objective_buckets:
            objective_buckets[status].append(text)
        else:
            objective_buckets["other"].append(f"{text} ({status})")

    lines.extend(["", "## Objectives"])
    if not any(objective_buckets.values()):
        lines.append("- None")
    else:
        for heading, items in (
            ("Active", objective_buckets["active"]),
            ("Completed", objective_buckets["completed"]),
            ("Failed", objective_buckets["failed"]),
            ("Other", objective_buckets["other"]),
        ):
            if not items:
                continue
            lines.append(f"### {heading}")
            lines.extend(f"- {item}" for item in items)

    lines.extend(["", "## Faction Reputation"])
    if faction_reputation:
        for name, value in sorted(faction_reputation.items()):
            lines.append(f"- {name}: {value}")
    else:
        lines.append("- None")

    lines.extend(["", "## NPC Affinity"])
    if npc_affinity:
        for name, value in sorted(npc_affinity.items()):
            lines.append(f"- {name}: {value}")
    else:
        lines.append("- None")

    companion_buckets: dict[str, list[dict[str, Any]]] = {
        "active": [],
        "available": [],
        "lost": [],
        "other": [],
    }
    for companion in companions or []:
        normalized_companion = _normalize_companion(companion)
        if normalized_companion is None:
            continue
        status = str(normalized_companion["status"]).lower()
        companion_buckets.get(status, companion_buckets["other"]).append(normalized_companion)

    lines.extend(["", "## Companions"])
    if not any(companion_buckets.values()):
        lines.append("- None")
    else:
        for heading, companion_items in (
            ("Active", companion_buckets["active"]),
            ("Available", companion_buckets["available"]),
            ("Lost", companion_buckets["lost"]),
            ("Other", companion_buckets["other"]),
        ):
            if not companion_items:
                continue
            lines.append(f"### {heading}")
            for companion in sorted(
                companion_items,
                key=lambda item: str(item["name"]).casefold(),
            ):
                line = f"- {companion['name']} (Affinity {companion['affinity']})"
                if companion["effect"]:
                    line = f"{line}: {companion['effect']}"
                lines.append(line)
                if companion["summary"]:
                    lines.append(f"  {companion['summary']}")

    lines.extend(["", "## Story Flags"])
    normalized_flags = sorted(
        {flag for flag in story_flags or [] if isinstance(flag, str) and flag}
    )
    if normalized_flags:
        lines.extend(f"- {flag}" for flag in normalized_flags)
    else:
        lines.append("- None")

    lines.extend(["", "## Campaign"])
    lines.extend(_campaign_status_lines(campaign_progress))

    return "\n".join(lines)
