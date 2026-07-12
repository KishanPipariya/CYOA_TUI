import json
import os
from typing import cast

from cyoa.ui.presenters import build_accessible_export

REQUIRED_SAVE_KEYS = {
    "starting_prompt",
    "context_history",
    "prompt_config",
    "turn_count",
    "inventory",
    "player_stats",
    "current_node",
    "ui_state",
    "saved_at",
}

REQUIRED_UI_STATE_KEYS = {
    "current_story_text",
    "story_segments",
    "journal_entries",
    "current_turn_text",
    "active_turn",
    "mood",
    "journal_panel_collapsed",
    "story_map_panel_collapsed",
}


def clone_payload(payload: dict[str, object]) -> dict[str, object]:
    """Deep-copy a JSON-compatible payload without sharing nested state."""
    return cast(dict[str, object], json.loads(json.dumps(payload)))


def coerce_ui_state(payload: object) -> dict[str, object]:
    return payload if isinstance(payload, dict) else {}


def require_dict(payload: object, message: str) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError(message)
    return cast(dict[str, object], payload)


def require_keys(payload: dict[str, object], required_keys: set[str], label: str) -> None:
    missing = sorted(required_keys.difference(payload))
    if missing:
        raise ValueError(f"{label} is missing required keys: {', '.join(missing)}")


def validate_save_payload(payload: object) -> dict[str, object]:
    data = require_dict(payload, "save payload must be a JSON object")
    require_keys(data, REQUIRED_SAVE_KEYS, "save payload")
    validate_engine_save_fields(data)
    validate_ui_state(data["ui_state"])
    validate_restore_points(data.get("restore_points"))
    return data


def validate_ui_state(payload: object) -> None:
    data = require_dict(payload, "save payload has invalid ui_state")
    require_keys(data, REQUIRED_UI_STATE_KEYS, "ui_state")
    validate_ui_scalar_fields(data)
    validate_story_segments(data["story_segments"])
    validate_journal_entries(data["journal_entries"])


def validate_engine_save_fields(payload: dict[str, object]) -> None:
    if not isinstance(payload["starting_prompt"], str) or not payload["starting_prompt"]:
        raise ValueError("save payload has invalid starting_prompt")
    if not isinstance(payload["context_history"], list):
        raise ValueError("save payload has invalid context_history")
    if not isinstance(payload["prompt_config"], dict):
        raise ValueError("save payload has invalid prompt_config")
    if isinstance(payload["turn_count"], bool) or not isinstance(payload["turn_count"], int):
        raise ValueError("save payload has invalid turn_count")
    if not isinstance(payload["inventory"], list):
        raise ValueError("save payload has invalid inventory")
    if not isinstance(payload["player_stats"], dict):
        raise ValueError("save payload has invalid player_stats")
    if payload["current_node"] is not None and not isinstance(payload["current_node"], dict):
        raise ValueError("save payload has invalid current_node")
    if not isinstance(payload["saved_at"], str) or not payload["saved_at"]:
        raise ValueError("save payload has invalid saved_at")


def validate_ui_scalar_fields(payload: dict[str, object]) -> None:
    if not isinstance(payload["current_story_text"], str):
        raise ValueError("ui_state has invalid current_story_text")
    if not isinstance(payload["current_turn_text"], str):
        raise ValueError("ui_state has invalid current_turn_text")
    if isinstance(payload["active_turn"], bool) or not isinstance(payload["active_turn"], int):
        raise ValueError("ui_state has invalid active_turn")
    if not isinstance(payload["mood"], str):
        raise ValueError("ui_state has invalid mood")
    if not isinstance(payload["journal_panel_collapsed"], bool):
        raise ValueError("ui_state has invalid journal_panel_collapsed")
    if not isinstance(payload["story_map_panel_collapsed"], bool):
        raise ValueError("ui_state has invalid story_map_panel_collapsed")


def validate_story_segments(payload: object) -> None:
    if coerce_story_segments(payload) != payload:
        raise ValueError("ui_state has invalid story_segments")


def validate_journal_entries(journal_entries: object) -> None:
    if not isinstance(journal_entries, list):
        raise ValueError("ui_state has invalid journal_entries")
    for entry in journal_entries:
        if not isinstance(entry, dict):
            raise ValueError("ui_state has invalid journal entry")
        if not isinstance(entry.get("label"), str):
            raise ValueError("ui_state has invalid journal entry label")
        if isinstance(entry.get("scene_index"), bool) or not isinstance(
            entry.get("scene_index"), int
        ):
            raise ValueError("ui_state has invalid journal entry scene_index")
        if not isinstance(entry.get("entry_kind"), str):
            raise ValueError("ui_state has invalid journal entry kind")


def validate_restore_points(restore_points: object) -> None:
    if restore_points is None:
        return
    if not isinstance(restore_points, dict):
        raise ValueError("save payload has invalid restore_points")
    for name, restore_point in restore_points.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("save payload has invalid restore point name")
        validate_save_payload(restore_point)


def coerce_journal_entries(payload: object) -> list[dict[str, object]]:
    return (
        [entry for entry in payload if isinstance(entry, dict)] if isinstance(payload, list) else []
    )


def coerce_run_archive_entries(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, list):
        return []

    entries: list[dict[str, object]] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        completed_at = raw.get("completed_at")
        ending_type = raw.get("ending_type")
        if not isinstance(completed_at, str) or not isinstance(ending_type, str):
            continue
        entries.append(cast(dict[str, object], raw))
    return entries


def coerce_restore_points(payload: object) -> dict[str, dict[str, object]]:
    if not isinstance(payload, dict):
        return {}

    normalized: dict[str, dict[str, object]] = {}
    for raw_name, raw_point in payload.items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            continue
        if not isinstance(raw_point, dict):
            continue
        normalized[raw_name.strip()] = cast(dict[str, object], raw_point)
    return normalized


def coerce_story_segments(payload: object) -> list[dict[str, str]]:
    if not isinstance(payload, list):
        return []

    normalized: list[dict[str, str]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind")
        text = entry.get("text")
        if kind not in {"story_turn", "player_choice", "branch_marker"} or not isinstance(
            text, str
        ):
            continue
        normalized.append({"kind": kind, "text": text})
    return normalized


def render_story_segments(segments: list[dict[str, str]]) -> str:
    story_text = ""
    for segment in segments:
        if segment["kind"] == "player_choice":
            if story_text:
                story_text += "\n\n"
            story_text += f"> {segment['text']}\n\n---\n\n"
        elif segment["kind"] == "branch_marker":
            story_text += f"\n\n***\n\n{segment['text']}"
        else:
            story_text += segment["text"]
    return story_text


def coerce_scene_index(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    if isinstance(value, (str, bytes, bytearray)):
        try:
            parsed = int(value)
        except ValueError:
            return 0
        return max(0, parsed)
    return 0


def obsidian_safe_name(value: str) -> str:
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in value).strip()
    return safe or "Untitled"


def obsidian_state_lines(payload: dict[str, object]) -> list[str]:
    lines: list[str] = []
    turn_count = payload.get("turn_count")
    if isinstance(turn_count, int):
        lines.append(f"- Turns: {turn_count}")
    inventory = payload.get("inventory")
    if isinstance(inventory, list):
        carried = ", ".join(str(item) for item in inventory if isinstance(item, str))
        lines.append(f"- Inventory: {carried or 'Empty'}")
    player_stats = payload.get("player_stats")
    if isinstance(player_stats, dict) and player_stats:
        stats = ", ".join(f"{key}: {value}" for key, value in sorted(player_stats.items()))
        lines.append(f"- Stats: {stats}")
    flags = payload.get("story_flags")
    if isinstance(flags, list) and flags:
        lines.append("- Flags: " + ", ".join(str(flag) for flag in flags))
    return lines


def render_markdown_export(payload: dict[str, object]) -> str:
    ui_state = coerce_ui_state(payload.get("ui_state"))
    story_segments = coerce_story_segments(ui_state.get("story_segments"))
    lines = [f"# {payload.get('story_title') or 'Untitled Adventure'}", ""]
    directives = payload.get("prompt_config", {})
    if isinstance(directives, dict):
        active = directives.get("directives")
        if isinstance(active, list) and active:
            lines.append("## Active Directives")
            lines.extend(f"- {directive}" for directive in active if isinstance(directive, str))
            lines.append("")
    lines.append("## Story")
    if story_segments:
        for segment in story_segments:
            if segment["kind"] == "player_choice":
                lines.append(f"> {segment['text']}")
            elif segment["kind"] == "branch_marker":
                lines.append(f"---\n{segment['text']}")
            else:
                lines.append(segment["text"])
            lines.append("")
    else:
        current_story = ui_state.get("current_story_text")
        if isinstance(current_story, str) and current_story:
            lines.append(current_story)
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def render_accessible_export(
    payload: dict[str, object],
    *,
    verbosity: str,
) -> str:
    ui_state = coerce_ui_state(payload.get("ui_state"))
    prompt_config = payload.get("prompt_config", {})
    directives: list[str] = []
    if isinstance(prompt_config, dict):
        raw_directives = prompt_config.get("directives")
        if isinstance(raw_directives, list):
            directives = [directive for directive in raw_directives if isinstance(directive, str)]
    story_title = payload.get("story_title")
    turn_count = payload.get("turn_count")
    saved_at = payload.get("saved_at")
    current_story_text = ui_state.get("current_story_text")
    inventory = payload.get("inventory")
    player_stats = payload.get("player_stats")
    objectives = payload.get("objectives")
    world_time = payload.get("world_time")
    last_choice_text = payload.get("last_choice_text")
    return build_accessible_export(
        story_title=story_title if isinstance(story_title, str) else None,
        turn_count=turn_count if isinstance(turn_count, int) else None,
        saved_at=saved_at if isinstance(saved_at, str) else None,
        story_segments=coerce_story_segments(ui_state.get("story_segments")),
        current_story_text=current_story_text if isinstance(current_story_text, str) else None,
        directives=directives,
        inventory=inventory if isinstance(inventory, list) else [],
        player_stats=player_stats if isinstance(player_stats, dict) else {},
        objectives=objectives if isinstance(objectives, list) else [],
        world_time=world_time if isinstance(world_time, dict) else None,
        last_choice_text=last_choice_text if isinstance(last_choice_text, str) else None,
        last_resolved_choice_check=payload.get("last_resolved_choice_check"),
        verbosity=verbosity,
    )


def build_timeline_export(payload: dict[str, object]) -> dict[str, object]:
    ui_state = coerce_ui_state(payload.get("ui_state"))
    return {
        "story_title": payload.get("story_title"),
        "turn_count": payload.get("turn_count"),
        "inventory": payload.get("inventory"),
        "player_stats": payload.get("player_stats"),
        "world_time": payload.get("world_time"),
        "campaign": payload.get("campaign"),
        "campaign_progress": payload.get("campaign_progress"),
        "campaign_clocks": payload.get("campaign_clocks"),
        "timeline_metadata": payload.get("timeline_metadata"),
        "story_segments": coerce_story_segments(ui_state.get("story_segments")),
        "journal_entries": coerce_journal_entries(ui_state.get("journal_entries")),
        "prompt_config": payload.get("prompt_config"),
        "saved_at": payload.get("saved_at"),
    }


def build_obsidian_records(payload: dict[str, object]) -> list[dict[str, object]]:
    ui_state = coerce_ui_state(payload.get("ui_state"))
    records: list[dict[str, object]] = []
    current_turn = 0
    for segment in coerce_story_segments(ui_state.get("story_segments")):
        kind = segment["kind"]
        text = segment["text"].strip()
        if not text:
            continue
        if kind == "story_turn":
            current_turn += 1
            records.append(
                {
                    "kind": "turn",
                    "turn": current_turn,
                    "title": f"Turn {current_turn:02d}",
                    "text": text,
                    "choices": [],
                }
            )
            continue
        if kind == "player_choice" and records:
            choices = records[-1].setdefault("choices", [])
            if isinstance(choices, list):
                choices.append(text)
            continue
        records.append(
            {
                "kind": "branch",
                "turn": current_turn,
                "title": f"Branch Marker {len(records) + 1:02d}",
                "text": text,
                "choices": [],
            }
        )
    return records


def export_stem(exports_dir: str, title: str) -> str:
    safe_title = "".join(c if c.isalnum() or c in " _-" else "_" for c in title).strip()
    return os.path.join(exports_dir, safe_title or "adventure")
