from dataclasses import dataclass
from typing import Any

from cyoa.ui.presenter_shared import (
    _active_objective_texts,
    _choice_check_lines,
    _format_signed_change,
    _format_stat_name,
    _locked_reason_lines,
    _resolved_choice_check_lines,
    normalize_verbosity,
)


@dataclass(frozen=True, slots=True)
class SceneRecapInput:
    narrative: str
    choices: list[Any]
    inventory: list[str]
    player_stats: dict[str, int]
    objectives: list[Any]
    companions: list[Any] | None
    screen_reader_mode: bool
    turn_count: int
    scene_recap_verbosity: str = "standard"
    locked_choice_verbosity: str = "standard"
    story_title: str | None = None
    last_choice_text: str | None = None
    last_resolved_choice_check: Any = None
    story_flags: set[str] | list[str] | None = None
    items_gained: list[str] | None = None
    items_lost: list[str] | None = None
    stat_updates: dict[str, int] | None = None
    objectives_updated: list[Any] | None = None
    faction_updates: dict[str, int] | None = None
    npc_affinity_updates: dict[str, int] | None = None
    story_flags_set: list[str] | None = None
    story_flags_cleared: list[str] | None = None
    companions_updated: list[Any] | None = None


def build_scene_recap(  # noqa: C901
    *,
    narrative: str,
    choices: list[Any],
    inventory: list[str],
    player_stats: dict[str, int],
    objectives: list[Any],
    companions: list[Any] | None,
    screen_reader_mode: bool,
    turn_count: int,
    scene_recap_verbosity: str = "standard",
    locked_choice_verbosity: str = "standard",
    story_title: str | None = None,
    last_choice_text: str | None = None,
    last_resolved_choice_check: Any = None,
    story_flags: set[str] | list[str] | None = None,
    items_gained: list[str] | None = None,
    items_lost: list[str] | None = None,
    stat_updates: dict[str, int] | None = None,
    objectives_updated: list[Any] | None = None,
    faction_updates: dict[str, int] | None = None,
    npc_affinity_updates: dict[str, int] | None = None,
    story_flags_set: list[str] | None = None,
    story_flags_cleared: list[str] | None = None,
    companions_updated: list[Any] | None = None,
) -> str:
    resolved_recap_verbosity = normalize_verbosity(scene_recap_verbosity)
    resolved_locked_choice_verbosity = normalize_verbosity(locked_choice_verbosity)
    recap_lines = [f"Turn {turn_count}"]
    if story_title:
        recap_lines[0] = f"{story_title} | Turn {turn_count}"
    if last_choice_text and (screen_reader_mode or resolved_recap_verbosity == "detailed"):
        recap_lines.append(f"Last choice: {last_choice_text}")
    resolved_check_lines = _resolved_choice_check_lines(last_resolved_choice_check)
    if resolved_check_lines and (screen_reader_mode or resolved_recap_verbosity == "detailed"):
        recap_lines.extend(resolved_check_lines)

    recap_lines.extend(
        [
            "",
            "## Scene",
            narrative.strip() or "No current scene available.",
            "",
            "## Choices",
        ]
    )

    normalized_flags = set(story_flags or [])
    if choices:
        for index, choice in enumerate(choices, start=1):
            choice_text = str(getattr(choice, "text", "")).strip() or "Unnamed choice"
            availability_reason = None
            reason_builder = getattr(choice, "availability_reason", None)
            if callable(reason_builder):
                availability_reason = reason_builder(
                    inventory,
                    player_stats,
                    normalized_flags,
                    companions,
                )
            if availability_reason:
                reason_lines = _locked_reason_lines(
                    availability_reason,
                    screen_reader_mode=True,
                    verbosity=resolved_locked_choice_verbosity,
                )
                if not reason_lines:
                    recap_lines.append(f"{index}. {choice_text} (Unavailable)")
                elif screen_reader_mode or resolved_recap_verbosity == "detailed":
                    recap_lines.append(f"{index}. {choice_text}")
                    for line in reason_lines:
                        recap_lines.append(f"   Unavailable: {line}")
                else:
                    recap_lines.append(f"{index}. {choice_text} (Unavailable: {reason_lines[0]})")
            else:
                check_lines = _choice_check_lines(choice)
                if check_lines and (screen_reader_mode or resolved_recap_verbosity == "detailed"):
                    recap_lines.append(f"{index}. {choice_text}")
                    for line in check_lines:
                        recap_lines.append(f"   {line}")
                elif check_lines:
                    recap_lines.append(f"{index}. {choice_text} ({check_lines[0]})")
                else:
                    recap_lines.append(f"{index}. {choice_text}")
    else:
        recap_lines.append("No further choices. This scene is an ending.")

    active_objectives = _active_objective_texts(objectives)
    recap_lines.extend(["", "## Objectives"])
    if active_objectives:
        recap_lines.extend(f"- {objective}" for objective in active_objectives)
    else:
        recap_lines.append("- None")

    health = player_stats.get("health", 0)
    gold = player_stats.get("gold", 0)
    reputation = player_stats.get("reputation", 0)
    inventory_text = ", ".join(inventory) if inventory else "Empty"
    active_companions: list[str] = []
    for companion in companions or []:
        if isinstance(companion, dict):
            name = companion.get("name")
            status = companion.get("status")
            effect = companion.get("effect")
        else:
            name = getattr(companion, "name", None)
            status = getattr(companion, "status", None)
            effect = getattr(companion, "effect", None)
        if not isinstance(name, str) or not name.strip() or status != "active":
            continue
        if isinstance(effect, str) and effect.strip():
            active_companions.append(f"{name.strip()} ({effect.strip()})")
        else:
            active_companions.append(name.strip())

    recap_lines.extend(["", "## Progress"])
    if resolved_recap_verbosity == "minimal":
        recap_lines.extend(
            [
                f"- Stats: Health {health} | Gold {gold} | Reputation {reputation}",
                f"- Inventory: {len(inventory)} item(s)",
                f"- Objectives: {len(active_objectives)} active",
                f"- Companions: {len(active_companions)} active",
            ]
        )
    elif screen_reader_mode or resolved_recap_verbosity == "detailed":
        recap_lines.extend(
            [
                f"- Health: {health}",
                f"- Gold: {gold}",
                f"- Reputation: {reputation}",
                f"- Inventory: {inventory_text}",
                "- Active companions: "
                + (", ".join(active_companions) if active_companions else "None"),
            ]
        )
    else:
        recap_lines.extend(
            [
                f"- Stats: Health {health} | Gold {gold} | Reputation {reputation}",
                f"- Inventory: {inventory_text}",
                "- Active companions: "
                + (", ".join(active_companions) if active_companions else "None"),
            ]
        )

    recent_changes: list[str] = []
    if items_gained:
        recent_changes.append(f"Items gained: {', '.join(items_gained)}")
    if items_lost:
        recent_changes.append(f"Items lost: {', '.join(items_lost)}")
    if stat_updates:
        ordered_stats = ["health", "gold", "reputation"]
        stat_parts = [
            f"{_format_stat_name(name)} {_format_signed_change(stat_updates[name])}"
            for name in ordered_stats
            if stat_updates.get(name)
        ]
        stat_parts.extend(
            f"{_format_stat_name(name)} {_format_signed_change(change)}"
            for name, change in sorted(stat_updates.items())
            if name not in ordered_stats and change
        )
        if stat_parts:
            recent_changes.append("Stats changed: " + "; ".join(stat_parts))
    if objectives_updated:
        objective_parts = []
        for objective in objectives_updated:
            if isinstance(objective, dict):
                text = objective.get("text")
                status = objective.get("status", "active")
            else:
                text = getattr(objective, "text", None)
                status = getattr(objective, "status", "active")
            if isinstance(text, str) and text.strip():
                objective_parts.append(f"{text.strip()} ({status})")
        if objective_parts:
            recent_changes.append("Objective updates: " + "; ".join(objective_parts))
    if faction_updates:
        faction_parts = [
            f"{name} {_format_signed_change(change)}"
            for name, change in sorted(faction_updates.items())
            if change
        ]
        if faction_parts:
            recent_changes.append("Faction changes: " + "; ".join(faction_parts))
    if npc_affinity_updates:
        affinity_parts = [
            f"{name} {_format_signed_change(change)}"
            for name, change in sorted(npc_affinity_updates.items())
            if change
        ]
        if affinity_parts:
            recent_changes.append("NPC affinity changes: " + "; ".join(affinity_parts))
    if story_flags_set:
        recent_changes.append("Flags set: " + ", ".join(story_flags_set))
    if story_flags_cleared:
        recent_changes.append("Flags cleared: " + ", ".join(story_flags_cleared))
    if companions_updated:
        companion_parts: list[str] = []
        for companion in companions_updated:
            if isinstance(companion, dict):
                name = companion.get("name")
                status = companion.get("status", "available")
                affinity = companion.get("affinity", 0)
                effect = companion.get("effect")
            else:
                name = getattr(companion, "name", None)
                status = getattr(companion, "status", "available")
                affinity = getattr(companion, "affinity", 0)
                effect = getattr(companion, "effect", None)
            if not isinstance(name, str) or not name.strip():
                continue
            detail = f"{name.strip()} ({status}, affinity {affinity})"
            if isinstance(effect, str) and effect.strip():
                detail = f"{detail}: {effect.strip()}"
            companion_parts.append(detail)
        if companion_parts:
            recent_changes.append("Companion updates: " + "; ".join(companion_parts))

    if resolved_recap_verbosity != "minimal":
        recap_lines.extend(["", "## Recent Changes"])
        if recent_changes:
            recap_lines.extend(f"- {change}" for change in recent_changes)
        else:
            recap_lines.append("- No major changes this turn.")

    return "\n".join(recap_lines)
