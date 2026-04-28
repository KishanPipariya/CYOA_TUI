import re
import unicodedata
from typing import Any

from cyoa.core import constants
from cyoa.ui.keybindings import APP_BINDING_SPECS, format_key_for_display

MARKUP_TAG_RE = re.compile(r"\[/?[a-zA-Z][^\]]*\]")


def normalize_verbosity(value: str | None, default: str = "standard") -> str:
    if isinstance(value, str) and value in constants.VERBOSITY_OPTIONS:
        return value
    return default


def _use_plain_labels(*, screen_reader_mode: bool, simplified_mode: bool = False) -> bool:
    return screen_reader_mode or simplified_mode


def loading_story_text(*, screen_reader_mode: bool) -> str:
    return "Loading story..." if screen_reader_mode else constants.LOADING_ART


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


def build_branch_preview(scene: dict[str, Any], turn_index: int, choice_text: str) -> str:
    """Build a compact branch preview for rewind selection."""
    raw = str(scene.get("narrative", "")).replace("\n", " ").strip()
    preview = (raw[:180].rsplit(" ", 1)[0] + "…") if len(raw) > 180 else raw
    preview = preview or "No scene summary available."
    available_choices = scene.get("available_choices")
    branch_count = len(available_choices) if isinstance(available_choices, list) else 0
    inventory = scene.get("inventory")
    item_count = len(inventory) if isinstance(inventory, list) else 0
    return (
        f"[b]Turn {turn_index + 1}[/b]  [dim]Next choice: {choice_text}[/dim]\n"
        f"{preview}\n"
        f"[dim]{branch_count} future path(s) • {item_count} item(s) carried[/dim]"
    )


def format_save_display_name(save_file: str) -> str:
    """Convert a save filename into a readable list label."""
    return save_file.replace(".json", "").replace("_", " ")


def format_inventory_label(
    inventory: list[str],
    *,
    screen_reader_mode: bool = False,
    simplified_mode: bool = False,
) -> str:
    prefix = (
        "Inventory"
        if _use_plain_labels(
            screen_reader_mode=screen_reader_mode,
            simplified_mode=simplified_mode,
        )
        else "🎒 Inventory"
    )
    return f"{prefix}: {', '.join(inventory)}" if inventory else f"{prefix}: Empty"


def format_objectives_label(
    objectives: list[str],
    *,
    screen_reader_mode: bool = False,
    simplified_mode: bool = False,
) -> str:
    if simplified_mode:
        return f"Focus: {objectives[0]}" if objectives else "Focus: None"
    prefix = "Objectives" if screen_reader_mode else "🎯 Objectives"
    return f"{prefix}: {' | '.join(objectives[:2])}" if objectives else f"{prefix}: None"


def format_directives_label(
    directives: list[str],
    *,
    screen_reader_mode: bool = False,
    simplified_mode: bool = False,
) -> str:
    if simplified_mode:
        return f"Guidance: {directives[0]}" if directives else "Guidance: None"
    prefix = "Directives" if screen_reader_mode else "🧭 Directives"
    return f"{prefix}: {' | '.join(directives[:2])}" if directives else f"{prefix}: None"


def format_stats_text(
    *,
    gold: int,
    reputation: int,
    screen_reader_mode: bool = False,
    simplified_mode: bool = False,
) -> str:
    if _use_plain_labels(screen_reader_mode=screen_reader_mode, simplified_mode=simplified_mode):
        return f"Gold {gold} | Reputation {reputation}"
    return f"🪙 Gold {gold}  •  🌟 Reputation {reputation}"


def format_runtime_text(
    *,
    generation_preset: str,
    engine_phase: str,
    provider_label: str,
    runtime_profile: str,
    screen_reader_mode: bool = False,
    simplified_mode: bool = False,
    verbosity: str = "standard",
) -> str:
    resolved_verbosity = normalize_verbosity(verbosity)
    if resolved_verbosity == "minimal":
        if _use_plain_labels(
            screen_reader_mode=screen_reader_mode,
            simplified_mode=simplified_mode,
        ):
            return f"Phase {engine_phase}"
        return f"⏱ {engine_phase}"
    if _use_plain_labels(screen_reader_mode=screen_reader_mode, simplified_mode=simplified_mode):
        if resolved_verbosity == "standard":
            return f"Preset {generation_preset} | Phase {engine_phase}"
        return (
            f"Preset {generation_preset} | Phase {engine_phase} | "
            f"Provider {provider_label} | Profile {runtime_profile}"
        )
    if resolved_verbosity == "standard":
        return f"⚙️ {generation_preset}  •  ⏱ {engine_phase}"
    return (
        f"⚙️ {generation_preset}  •  ⏱ {engine_phase}  •  🖧 {provider_label}  •  ⛭ {runtime_profile}"
    )


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


def build_choice_label(
    index: int,
    choice_text: str,
    disabled_reason: str | None = None,
    *,
    screen_reader_mode: bool = False,
    verbosity: str = "standard",
) -> str:
    label = (
        f"{index + 1}. {choice_text}"
        if screen_reader_mode
        else f"[b]{index + 1}.[/b] {choice_text}"
    )
    if disabled_reason:
        reason_lines = _locked_reason_lines(
            disabled_reason,
            screen_reader_mode=screen_reader_mode,
            verbosity=verbosity,
        )
        if not reason_lines:
            return (
                f"{label}\nUnavailable"
                if screen_reader_mode
                else f"{label}\n[dim]Unavailable[/dim]"
            )
        detail_lines = "\n".join(f"- {part}" for part in reason_lines)
        if screen_reader_mode:
            return f"{label}\nUnavailable:\n{detail_lines}"
        return f"{label}\n[dim]Unavailable:[/dim]\n[dim]{detail_lines}[/dim]"
    return label


def format_choice_confirmation(choice_text: str, *, screen_reader_mode: bool) -> str:
    return f"You chose: {choice_text}" if screen_reader_mode else f"**You chose:** {choice_text}"


def format_branch_restore_text(turn_index: int, *, screen_reader_mode: bool) -> str:
    message = f"Time fractures. You return to Turn {turn_index + 1}."
    return (
        message
        if screen_reader_mode
        else f"**[Time fractures... you return to Turn {turn_index + 1}]**"
    )


def format_error_notice(*, screen_reader_mode: bool) -> str:
    return (
        "\n\nAn error occurred. The story engine could not generate a valid response."
        if screen_reader_mode
        else "\n\n> ⚠️ **An error occurred.** The story engine could not generate a valid response."
    )


def format_retry_label(*, screen_reader_mode: bool) -> str:
    return "Retry generation" if screen_reader_mode else "🔄 Retry Generation"


def format_new_adventure_label(*, screen_reader_mode: bool) -> str:
    return "Start a new adventure" if screen_reader_mode else "✦ Start a New Adventure"


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

    objective_texts = _active_objective_texts(objectives)
    lines.append("Current Progress:")
    if resolved_verbosity == "minimal":
        lines.append(
            "- Stats: "
            f"Health {player_stats.get('health', 100)} | "
            f"Gold {player_stats.get('gold', 0)} | "
            f"Reputation {player_stats.get('reputation', 0)}"
        )
        lines.append(f"- Inventory: {len(inventory)} item(s)")
        lines.append(f"- Objectives: {len(objective_texts)} active")
    else:
        lines.append(f"- Health: {player_stats.get('health', 100)}")
        lines.append(f"- Gold: {player_stats.get('gold', 0)}")
        lines.append(f"- Reputation: {player_stats.get('reputation', 0)}")
        lines.append(f"- Inventory: {', '.join(inventory) if inventory else 'Empty'}")
        lines.append(f"- Objectives: {' | '.join(objective_texts) if objective_texts else 'None'}")
        if resolved_verbosity == "detailed" and objective_texts:
            lines.append("Objective Details:")
            lines.extend(f"- {objective}" for objective in objective_texts)
    return "\n".join(lines).strip() + "\n"


def build_scene_recap(  # noqa: C901
    *,
    narrative: str,
    choices: list[Any],
    inventory: list[str],
    player_stats: dict[str, int],
    objectives: list[Any],
    screen_reader_mode: bool,
    turn_count: int,
    scene_recap_verbosity: str = "standard",
    locked_choice_verbosity: str = "standard",
    story_title: str | None = None,
    last_choice_text: str | None = None,
    story_flags: set[str] | list[str] | None = None,
    items_gained: list[str] | None = None,
    items_lost: list[str] | None = None,
    stat_updates: dict[str, int] | None = None,
    objectives_updated: list[Any] | None = None,
    faction_updates: dict[str, int] | None = None,
    npc_affinity_updates: dict[str, int] | None = None,
    story_flags_set: list[str] | None = None,
    story_flags_cleared: list[str] | None = None,
) -> str:
    resolved_recap_verbosity = normalize_verbosity(scene_recap_verbosity)
    resolved_locked_choice_verbosity = normalize_verbosity(locked_choice_verbosity)
    recap_lines = [f"Turn {turn_count}"]
    if story_title:
        recap_lines[0] = f"{story_title} | Turn {turn_count}"
    if last_choice_text and (screen_reader_mode or resolved_recap_verbosity == "detailed"):
        recap_lines.append(f"Last choice: {last_choice_text}")

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

    recap_lines.extend(["", "## Progress"])
    if resolved_recap_verbosity == "minimal":
        recap_lines.extend(
            [
                f"- Stats: Health {health} | Gold {gold} | Reputation {reputation}",
                f"- Inventory: {len(inventory)} item(s)",
                f"- Objectives: {len(active_objectives)} active",
            ]
        )
    elif screen_reader_mode or resolved_recap_verbosity == "detailed":
        recap_lines.extend(
            [
                f"- Health: {health}",
                f"- Gold: {gold}",
                f"- Reputation: {reputation}",
                f"- Inventory: {inventory_text}",
            ]
        )
    else:
        recap_lines.extend(
            [
                f"- Stats: Health {health} | Gold {gold} | Reputation {reputation}",
                f"- Inventory: {inventory_text}",
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

    if resolved_recap_verbosity != "minimal":
        recap_lines.extend(["", "## Recent Changes"])
        if recent_changes:
            recap_lines.extend(f"- {change}" for change in recent_changes)
        else:
            recap_lines.append("- No major changes this turn.")

    return "\n".join(recap_lines)


def build_help_text(
    *,
    screen_reader_mode: bool,
    cognitive_load_reduction_mode: bool = False,
    current_bindings: dict[str, str] | None = None,
) -> str:
    bindings = current_bindings or {}
    key_rows = "\n".join(
        f"| [b][reverse]{_help_key_cell(bindings.get(spec.id, spec.key))}[/reverse][/b] | {spec.settings_label} |"
        for spec in APP_BINDING_SPECS
    )

    if screen_reader_mode:
        return f"""\
# Keyboard Shortcuts

| Key | Action |
|:---:|:-------|
{key_rows}
| [b][reverse]ENTER[/reverse][/b] | Confirm focused choice |

---

# Accessibility

- Screen Reader Friendly mode removes ASCII art, uses plainer labels, and keeps the latest status message in the status panel.
- Cognitive Load Reduction mode trims side-panel detail and uses simpler wording in status updates.
- Verbosity controls let you tune notifications, recaps, runtime metadata, and locked-choice detail separately. Screen Reader Friendly keeps plain wording, while Cognitive Load Reduction may still hide lower-priority runtime detail.
- High Contrast mode uses a fixed readable palette for story cards, choices, and side panels.
- Key bindings can be customized in Settings. Footer hints and this help sheet follow your saved keys.
- Reduced Motion disables spinner animation and narrated text animation.
- Journal and Story Map panels move keyboard focus automatically when opened.

---

*Press Escape or click Close to return to the adventure.*
"""
    return f"""\
# ⌨️ Keyboard Shortcuts

| Key | Action |
|:---:|:-------|
{key_rows}
| [b][reverse]ENTER[/reverse][/b] | Confirm focused choice |

---

# 📊 Player Stats

| Stat | Description |
|:-----|:------------|
| ❤️ **Health** | Your vitality. Low health disables risky choices. |
| 🪙 **Gold** | Currency earned through the adventure. |
| 🌟 **Reputation** | Your standing — high rep unlocks dialogue. |
| 🎒 **Inventory** | Items you carry. Some unlock special choices! |

---

# ♿ Accessibility

- Screen Reader Friendly mode removes ASCII art, uses plainer labels, and keeps the latest status message in the status panel.
- Cognitive Load Reduction mode trims side-panel detail and uses simpler wording in status updates.
- Verbosity controls let you tune notifications, recaps, runtime metadata, and locked-choice detail separately. Screen Reader Friendly keeps plain wording, while Cognitive Load Reduction may still hide lower-priority runtime detail.
- High Contrast mode uses a fixed readable palette for story cards, choices, and side panels.
- Key bindings can be customized in Settings. Footer hints and this help sheet follow your saved keys.
- Locked choices include a written reason and do not rely on color alone.
- Reduced Motion is available in Settings and disables spinner animation and narrated text animation.
- Journal and Story Map panels move keyboard focus automatically when opened.

---

*Press Escape or click Close to return to the adventure.*
"""


def _story_map_summary_empty() -> str:
    return (
        "# Story Map Summary\n\n"
        "## Structure\n"
        "No story-map data is available yet.\n\n"
        "## Branch Restores\n"
        "No timeline fractures recorded."
    )


def _story_map_branch_targets(
    timeline_metadata: list[dict[str, Any]],
) -> dict[str, list[int]]:
    branch_targets: dict[str, list[int]] = {}
    for entry in timeline_metadata:
        if entry.get("kind") != "branch_restore":
            continue
        target_scene_id = entry.get("target_scene_id")
        restored_turn = entry.get("restored_turn")
        if isinstance(target_scene_id, str) and isinstance(restored_turn, int):
            branch_targets.setdefault(target_scene_id, []).append(restored_turn)
    return branch_targets


def _story_map_scene_lines(
    scene: dict[str, Any],
    scene_id: str,
    *,
    depth: int,
    turn: int,
    current_scene_id: str | None,
    branch_targets: dict[str, list[int]],
    via_choice: str | None = None,
) -> list[str]:
    narrative = str(scene.get("narrative", "")).replace("\n", " ").strip()
    preview = narrative[:90] + ("..." if len(narrative) > 90 else "")
    status_parts = [f"Turn {turn}", f"Depth {depth}"]
    if scene_id == current_scene_id:
        status_parts.append("Current")
    if not bool(scene.get("available_choices")):
        status_parts.append("Ending")
    restored_turns = branch_targets.get(scene_id, [])
    if restored_turns:
        status_parts.append(
            "Restored from " + ", ".join(f"Turn {value}" for value in sorted(set(restored_turns)))
        )

    indent = "  " * depth
    lines: list[str] = []
    if via_choice:
        lines.append(f"{indent}Choice: {via_choice}")
    lines.append(f"{indent}- {' | '.join(status_parts)}")
    lines.append(f"{indent}  Scene: {preview or 'No scene summary available.'}")
    return lines


def _append_story_map_structure(
    *,
    scene_id: str,
    nodes: dict[str, Any],
    edges: dict[str, Any],
    current_scene_id: str | None,
    branch_targets: dict[str, list[int]],
    output: list[str],
    depth: int,
    turn: int,
    via_choice: str | None = None,
) -> None:
    scene = nodes.get(scene_id)
    if not isinstance(scene, dict):
        return

    output.extend(
        _story_map_scene_lines(
            scene,
            scene_id,
            depth=depth,
            turn=turn,
            current_scene_id=current_scene_id,
            branch_targets=branch_targets,
            via_choice=via_choice,
        )
    )
    for edge in edges.get(scene_id, []):
        if not isinstance(edge, dict):
            continue
        target_id = edge.get("target_id")
        if not isinstance(target_id, str):
            continue
        choice_text = edge.get("choice")
        _append_story_map_structure(
            scene_id=target_id,
            nodes=nodes,
            edges=edges,
            current_scene_id=current_scene_id,
            branch_targets=branch_targets,
            output=output,
            depth=depth + 1,
            turn=turn + 1,
            via_choice=str(choice_text).strip() if choice_text else None,
        )


def build_journal_summary(
    entries: list[dict[str, object]],
    *,
    screen_reader_mode: bool,
) -> str:
    if not entries:
        return (
            "# Journal Summary\n\n"
            "## Timeline\n"
            "No journal entries yet.\n\n"
            "## Branch Restores\n"
            "No timeline fractures recorded."
        )

    timeline_lines: list[str] = []
    branch_lines: list[str] = []
    for index, entry in enumerate(entries, start=1):
        label = str(entry.get("label", "")).strip() or f"Turn {index}"
        entry_kind = str(entry.get("entry_kind", "choice")).strip().lower()
        scene_index = entry.get("scene_index")
        scene_label = (
            f"Turn {int(scene_index) + 1}"
            if isinstance(scene_index, int) and scene_index >= 0
            else "Unknown Turn"
        )
        if entry_kind == "branch":
            branch_lines.append(f"- {scene_label}: {label}")
        else:
            timeline_lines.append(f"- {scene_label}: {label}")

    title = "# Journal Summary"
    if screen_reader_mode:
        title = "# Accessible Journal Summary"
    parts = [title, "", "## Timeline"]
    parts.extend(timeline_lines or ["No turn-by-turn journal entries yet."])
    parts.extend(["", "## Branch Restores"])
    parts.extend(branch_lines or ["No timeline fractures recorded."])
    return "\n".join(parts)


def build_story_map_summary(
    tree_data: dict[str, Any] | None,
    *,
    current_scene_id: str | None,
    timeline_metadata: list[dict[str, Any]],
    screen_reader_mode: bool,
) -> str:
    if not tree_data:
        return _story_map_summary_empty()

    nodes = tree_data.get("nodes", {})
    edges = tree_data.get("edges", {})
    root_id = tree_data.get("root_id")
    if not isinstance(nodes, dict) or not isinstance(edges, dict) or not isinstance(root_id, str):
        return _story_map_summary_empty()

    branch_targets = _story_map_branch_targets(timeline_metadata)
    structure_lines: list[str] = []
    _append_story_map_structure(
        scene_id=root_id,
        nodes=nodes,
        edges=edges,
        current_scene_id=current_scene_id,
        branch_targets=branch_targets,
        output=structure_lines,
        depth=0,
        turn=1,
    )

    title = "# Story Map Summary"
    if screen_reader_mode:
        title = "# Accessible Story Map Summary"
    parts = [title, "", "## Structure"]
    parts.extend(structure_lines or ["No story-map data is available yet."])
    parts.extend(["", "## Branch Restores"])
    if branch_targets:
        for scene_id, restored_turns in sorted(branch_targets.items()):
            parts.append(
                f"- {scene_id}: restored from "
                + ", ".join(f"Turn {value}" for value in sorted(set(restored_turns)))
            )
    else:
        parts.append("No timeline fractures recorded.")
    return "\n".join(parts)


def _help_key_cell(key: str) -> str:
    display = format_key_for_display(key)
    return f" {display} "
