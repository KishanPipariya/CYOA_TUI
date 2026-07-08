from typing import Any


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
