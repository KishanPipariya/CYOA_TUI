"""Strict, versioned schemas for data that is persisted between runs.

This module deliberately performs validation before constructing runtime objects.  Save
files are an external compatibility boundary: accepting partial or coerced payloads
would make a damaged/old file silently change a player's run.
"""

from __future__ import annotations

from typing import Any, cast

from cyoa.core.mementos import GameStateSnapshot
from cyoa.core.models import (
    CampaignClock,
    CampaignPack,
    CampaignProgress,
    Companion,
    LoreEntry,
    Objective,
    StoryNode,
    WorldTime,
)

SAVE_SCHEMA_VERSION = 1

STATE_KEYS = frozenset(
    {
        "schema_version",
        "story_title",
        "turn_count",
        "inventory",
        "player_stats",
        "current_node",
        "current_scene_id",
        "last_choice_text",
        "last_choice_submission",
        "timeline_metadata",
        "objectives",
        "faction_reputation",
        "npc_affinity",
        "story_flags",
        "lore_entries",
        "companions",
        "world_time",
        "campaign",
        "campaign_progress",
        "campaign_clocks",
        "undo_history",
        "redo_history",
        "bookmarks",
    }
)
SNAPSHOT_KEYS = (frozenset(GameStateSnapshot.__dataclass_fields__) - {"story_context"}) | {
    "schema_version",
    "story_context_history",
}
ENGINE_KEYS = STATE_KEYS | {"starting_prompt", "context_history", "prompt_config"}
SAVE_KEYS = ENGINE_KEYS | {"autosave", "restore_points", "ui_state", "saved_at"}
UI_STATE_KEYS = frozenset(
    {
        "current_story_text",
        "story_segments",
        "journal_entries",
        "current_turn_text",
        "active_turn",
        "mood",
        "journal_panel_collapsed",
        "story_map_panel_collapsed",
    }
)
RUN_ARCHIVE_ENTRY_KEYS = frozenset(
    {
        "story_title",
        "completed_at",
        "turn_count",
        "current_scene_id",
        "last_choice_text",
        "ending_type",
        "ending_label",
        "ending_narrative",
        "inventory",
        "player_stats",
        "objectives",
        "companions",
        "faction_reputation",
        "npc_affinity",
        "story_flags",
        "world_time",
        "campaign",
        "campaign_progress",
        "campaign_clocks",
        "timeline_metadata",
        "branch_restores",
        "divergence_points",
        "journal_entries",
        "story_segments",
        "discovered_lore_count",
        "objective_status_counts",
        "notification_hint",
    }
)


class PersistenceValidationError(ValueError):
    """Raised for malformed, unsupported, or incompatible persisted data."""


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise PersistenceValidationError(f"{label} must be a JSON object")
    return cast(dict[str, object], value)


def _exact_keys(data: dict[str, object], expected: frozenset[str], label: str) -> None:
    missing = sorted(expected - data.keys())
    unknown = sorted(data.keys() - expected)
    if missing:
        raise PersistenceValidationError(f"{label} is missing required keys: {', '.join(missing)}")
    if unknown:
        raise PersistenceValidationError(f"{label} has unknown keys: {', '.join(unknown)}")


def _version(data: dict[str, object], label: str) -> None:
    if data.get("schema_version") != SAVE_SCHEMA_VERSION or isinstance(
        data.get("schema_version"), bool
    ):
        raise PersistenceValidationError(
            f"{label} has unsupported schema_version; expected {SAVE_SCHEMA_VERSION}"
        )


def _str(value: object, label: str, *, nullable: bool = False, nonempty: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or (nonempty and not value):
        raise PersistenceValidationError(
            f"{label} must be {'a non-empty ' if nonempty else 'a '}string"
        )


def _int(value: object, label: str, *, minimum: int | None = None) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or (minimum is not None and value < minimum)
    ):
        qualifier = f" greater than or equal to {minimum}" if minimum is not None else ""
        raise PersistenceValidationError(f"{label} must be an integer{qualifier}")


def _string_list(value: object, label: str) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PersistenceValidationError(f"{label} must be a list of strings")


def _int_map(value: object, label: str) -> None:
    data = _object(value, label)
    if any(
        not isinstance(key, str) or isinstance(item, bool) or not isinstance(item, int)
        for key, item in data.items()
    ):
        raise PersistenceValidationError(f"{label} must map strings to integers")


def _model(value: object, model_type: type[Any], label: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, dict):
        raise PersistenceValidationError(f"{label} must be a JSON object")
    try:
        model = model_type.model_validate(value, strict=True)
    except Exception as exc:
        raise PersistenceValidationError(f"{label} is invalid: {exc}") from exc
    # model_dump includes defaults and strips unknown fields. Equality consequently
    # rejects both partial and unknown nested model payloads, as well as normalization.
    if model.model_dump() != value:
        raise PersistenceValidationError(f"{label} must use the exact canonical schema")


def _history(value: object, label: str) -> None:
    if not isinstance(value, list):
        raise PersistenceValidationError(f"{label} must be a list")
    for index, message in enumerate(value):
        data = _object(message, f"{label}[{index}]")
        _exact_keys(data, frozenset({"role", "content"}), f"{label}[{index}]")
        _str(data["role"], f"{label}[{index}].role")
        _str(data["content"], f"{label}[{index}].content")


def _timeline(value: object, label: str) -> None:
    if not isinstance(value, list):
        raise PersistenceValidationError(f"{label} must be a list")
    for index, raw in enumerate(value):
        data = _object(raw, f"{label}[{index}]")
        allowed = {"kind", "source_scene_id", "target_scene_id", "restored_turn"}
        if "kind" not in data or set(data) - allowed:
            raise PersistenceValidationError(f"{label}[{index}] must use the exact timeline schema")
        _str(data["kind"], f"{label}[{index}].kind")
        for key in ("source_scene_id", "target_scene_id"):
            if key in data:
                _str(data[key], f"{label}[{index}].{key}")
        if "restored_turn" in data:
            _int(data["restored_turn"], f"{label}[{index}].restored_turn", minimum=1)


def _snapshot(value: object, label: str) -> None:
    data = _object(value, label)
    _exact_keys(data, SNAPSHOT_KEYS, label)
    _version(data, label)
    _validate_state_fields(data, label, snapshot=True)
    _history(data["story_context_history"], f"{label}.story_context_history")


def _validate_state_fields(  # noqa: C901
    data: dict[str, object], label: str, *, snapshot: bool = False
) -> None:
    _str(data["story_title"], f"{label}.story_title", nullable=True)
    _int(data["turn_count"], f"{label}.turn_count", minimum=1)
    _string_list(data["inventory"], f"{label}.inventory")
    _int_map(data["player_stats"], f"{label}.player_stats")
    _model(data["current_node"], StoryNode, f"{label}.current_node", nullable=True)
    for key in ("current_scene_id", "last_choice_text", "last_choice_submission"):
        _str(data[key], f"{label}.{key}", nullable=True)
    _timeline(data["timeline_metadata"], f"{label}.timeline_metadata")
    _models(data["objectives"], Objective, f"{label}.objectives")
    _int_map(data["faction_reputation"], f"{label}.faction_reputation")
    _int_map(data["npc_affinity"], f"{label}.npc_affinity")
    _string_list(data["story_flags"], f"{label}.story_flags")
    _models(data["lore_entries"], LoreEntry, f"{label}.lore_entries")
    _models(data["companions"], Companion, f"{label}.companions")
    _model(data["world_time"], WorldTime, f"{label}.world_time")
    _model(data["campaign"], CampaignPack, f"{label}.campaign", nullable=True)
    _model(data["campaign_progress"], CampaignProgress, f"{label}.campaign_progress", nullable=True)
    if not isinstance(data["campaign_clocks"], list):
        raise PersistenceValidationError(f"{label}.campaign_clocks must be a list")
    # Campaign clocks are nested in CampaignProgress and validated there; this
    # duplicate serialized compatibility field must exactly agree when present.
    if data["campaign"] is None:
        if data["campaign_progress"] is not None or data["campaign_clocks"] != []:
            raise PersistenceValidationError(f"{label}.campaign state is inconsistent")
    elif data["campaign_progress"] is None:
        raise PersistenceValidationError(f"{label}.campaign requires campaign_progress")
    elif (
        cast(dict[str, object], data["campaign_progress"])["campaign_id"]
        != cast(dict[str, object], data["campaign"])["id"]
    ):
        raise PersistenceValidationError(f"{label}.campaign_progress does not match campaign")
    elif data["campaign_clocks"] != cast(dict[str, object], data["campaign_progress"])["clocks"]:
        raise PersistenceValidationError(
            f"{label}.campaign_clocks must match campaign_progress.clocks"
        )
    if not snapshot:
        for key in ("undo_history", "redo_history"):
            snapshots = data[key]
            if not isinstance(snapshots, list):
                raise PersistenceValidationError(f"{label}.{key} must be a list")
            for index, item in enumerate(snapshots):
                _snapshot(item, f"{label}.{key}[{index}]")
        bookmarks = _object(data["bookmarks"], f"{label}.bookmarks")
        for name, item in bookmarks.items():
            if not isinstance(name, str) or not name.strip():
                raise PersistenceValidationError(f"{label}.bookmarks has an invalid name")
            _snapshot(item, f"{label}.bookmarks[{name!r}]")


def _models(value: object, model_type: type[Any], label: str) -> None:
    if not isinstance(value, list):
        raise PersistenceValidationError(f"{label} must be a list")
    for index, item in enumerate(value):
        _model(item, model_type, f"{label}[{index}]")


def validate_state_payload(payload: object) -> dict[str, object]:
    data = _object(payload, "state payload")
    _exact_keys(data, STATE_KEYS, "state payload")
    _version(data, "state payload")
    _validate_state_fields(data, "state payload")
    return data


def validate_engine_save_payload(
    payload: object, *, allow_ui_fields: bool = False
) -> dict[str, object]:
    data = _object(payload, "engine save payload")
    expected = SAVE_KEYS if allow_ui_fields else ENGINE_KEYS
    _exact_keys(data, expected, "engine save payload")
    _version(data, "engine save payload")
    _str(data["starting_prompt"], "engine save payload.starting_prompt", nonempty=True)
    _history(data["context_history"], "engine save payload.context_history")
    prompt_config = _object(data["prompt_config"], "engine save payload.prompt_config")
    _exact_keys(
        prompt_config, frozenset({"goals", "directives"}), "engine save payload.prompt_config"
    )
    _string_list(prompt_config["goals"], "engine save payload.prompt_config.goals")
    _string_list(prompt_config["directives"], "engine save payload.prompt_config.directives")
    _validate_state_fields(data, "engine save payload")
    if allow_ui_fields:
        if not isinstance(data["autosave"], bool):
            raise PersistenceValidationError("save payload.autosave must be a boolean")
        _str(data["saved_at"], "save payload.saved_at", nonempty=True)
        validate_ui_state(data["ui_state"])
        restore_points = _object(data["restore_points"], "save payload.restore_points")
        for name, point in restore_points.items():
            if not isinstance(name, str) or not name.strip():
                raise PersistenceValidationError("save payload has an invalid restore point name")
            validate_save_payload(point)
    return data


def validate_ui_state(payload: object) -> dict[str, object]:
    data = _object(payload, "ui_state")
    _exact_keys(data, UI_STATE_KEYS, "ui_state")
    for key in ("current_story_text", "current_turn_text", "mood"):
        _str(data[key], f"ui_state.{key}")
    _int(data["active_turn"], "ui_state.active_turn", minimum=1)
    for key in ("journal_panel_collapsed", "story_map_panel_collapsed"):
        if not isinstance(data[key], bool):
            raise PersistenceValidationError(f"ui_state.{key} must be a boolean")
    segments = data["story_segments"]
    if not isinstance(segments, list):
        raise PersistenceValidationError("ui_state.story_segments must be a list")
    for index, entry in enumerate(segments):
        segment = _object(entry, f"ui_state.story_segments[{index}]")
        _exact_keys(segment, frozenset({"kind", "text"}), f"ui_state.story_segments[{index}]")
        if segment["kind"] not in {"story_turn", "player_choice", "branch_marker"}:
            raise PersistenceValidationError("ui_state.story_segments has an invalid kind")
        _str(segment["text"], f"ui_state.story_segments[{index}].text")
    journal = data["journal_entries"]
    if not isinstance(journal, list):
        raise PersistenceValidationError("ui_state.journal_entries must be a list")
    for index, entry in enumerate(journal):
        item = _object(entry, f"ui_state.journal_entries[{index}]")
        _exact_keys(
            item,
            frozenset({"label", "scene_index", "entry_kind"}),
            f"ui_state.journal_entries[{index}]",
        )
        _str(item["label"], f"ui_state.journal_entries[{index}].label")
        _int(item["scene_index"], f"ui_state.journal_entries[{index}].scene_index", minimum=0)
        _str(item["entry_kind"], f"ui_state.journal_entries[{index}].entry_kind")
    return data


def validate_save_payload(payload: object) -> dict[str, object]:
    return validate_engine_save_payload(payload, allow_ui_fields=True)


def validate_run_archive(payload: object) -> list[dict[str, object]]:
    data = _object(payload, "run archive")
    _exact_keys(data, frozenset({"schema_version", "entries"}), "run archive")
    _version(data, "run archive")
    entries = data["entries"]
    if not isinstance(entries, list):
        raise PersistenceValidationError("run archive.entries must be a list")
    for index, entry in enumerate(entries):
        raw = _object(entry, f"run archive.entries[{index}]")
        _exact_keys(raw, RUN_ARCHIVE_ENTRY_KEYS, f"run archive.entries[{index}]")
        for key in (
            "story_title",
            "completed_at",
            "ending_type",
            "ending_label",
            "ending_narrative",
            "notification_hint",
        ):
            _str(raw[key], f"run archive.entries[{index}].{key}", nonempty=True)
        _int(raw["turn_count"], f"run archive.entries[{index}].turn_count", minimum=1)
        _str(
            raw["current_scene_id"], f"run archive.entries[{index}].current_scene_id", nullable=True
        )
        _str(
            raw["last_choice_text"], f"run archive.entries[{index}].last_choice_text", nullable=True
        )
        _string_list(raw["inventory"], f"run archive.entries[{index}].inventory")
        _int_map(raw["player_stats"], f"run archive.entries[{index}].player_stats")
        _models(raw["objectives"], Objective, f"run archive.entries[{index}].objectives")
        _models(raw["companions"], Companion, f"run archive.entries[{index}].companions")
        _int_map(raw["faction_reputation"], f"run archive.entries[{index}].faction_reputation")
        _int_map(raw["npc_affinity"], f"run archive.entries[{index}].npc_affinity")
        _string_list(raw["story_flags"], f"run archive.entries[{index}].story_flags")
        _model(raw["world_time"], WorldTime, f"run archive.entries[{index}].world_time")
        _model(
            raw["campaign"], CampaignPack, f"run archive.entries[{index}].campaign", nullable=True
        )
        _model(
            raw["campaign_progress"],
            CampaignProgress,
            f"run archive.entries[{index}].campaign_progress",
            nullable=True,
        )
        _models(
            raw["campaign_clocks"], CampaignClock, f"run archive.entries[{index}].campaign_clocks"
        )
        _timeline(raw["timeline_metadata"], f"run archive.entries[{index}].timeline_metadata")
        _timeline(raw["branch_restores"], f"run archive.entries[{index}].branch_restores")
        if not isinstance(raw["divergence_points"], list):
            raise PersistenceValidationError("run archive has invalid divergence_points")
        for point in raw["divergence_points"]:
            _int(point, "run archive divergence point", minimum=1)
        validate_ui_state(
            {
                "current_story_text": "",
                "story_segments": raw["story_segments"],
                "journal_entries": raw["journal_entries"],
                "current_turn_text": "",
                "active_turn": 1,
                "mood": "default",
                "journal_panel_collapsed": False,
                "story_map_panel_collapsed": False,
            }
        )
        _int(raw["discovered_lore_count"], "run archive discovered_lore_count", minimum=0)
        counts = _object(raw["objective_status_counts"], "run archive objective_status_counts")
        _exact_keys(
            counts,
            frozenset({"active", "completed", "failed"}),
            "run archive objective_status_counts",
        )
        for count in counts.values():
            _int(count, "run archive objective status count", minimum=0)
    return cast(list[dict[str, object]], entries)
