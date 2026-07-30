from typing import Any


def classify_ending_type(
    narrative: str,
    *,
    health: int | None = None,
) -> str:
    normalized = narrative.casefold()
    if health is not None and health <= 0:
        return "death"
    if any(
        keyword in normalized
        for keyword in (
            "die",
            "dies",
            "dead",
            "death",
            "slain",
            "perish",
            "perishes",
            "killed",
            "consumed",
            "drowned",
            "executed",
        )
    ):
        return "death"
    if any(
        keyword in normalized
        for keyword in (
            "escape",
            "escaped",
            "freedom",
            "free at last",
            "fled",
            "liberated",
        )
    ):
        return "escape"
    if any(
        keyword in normalized
        for keyword in (
            "victory",
            "victorious",
            "triumph",
            "triumphed",
            "saved",
            "vanquished",
            "crowned",
        )
    ):
        return "victory"
    if any(keyword in normalized for keyword in ("sacrifice", "sacrificed", "martyr")):
        return "sacrifice"
    return "ending"


def format_ending_type_label(ending_type: str) -> str:
    labels = {
        "death": "Death",
        "escape": "Escape",
        "victory": "Victory",
        "sacrifice": "Sacrifice",
        "ending": "Ending",
    }
    return labels.get(ending_type, ending_type.replace("_", " ").title())


def _coerce_run_archive_entry(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    ending_type = entry.get("ending_type")
    completed_at = entry.get("completed_at")
    if not isinstance(ending_type, str) or not isinstance(completed_at, str):
        return None
    return entry


def derive_hidden_achievements(archive_entries: list[Any]) -> list[dict[str, Any]]:
    entries = [
        normalized
        for entry in archive_entries
        if (normalized := _coerce_run_archive_entry(entry)) is not None
    ]
    if not entries:
        return []

    sorted_entries = sorted(
        entries,
        key=lambda item: str(item.get("completed_at", "")),
        reverse=True,
    )
    unlocked: list[dict[str, Any]] = []

    def add_if_present(
        achievement_id: str,
        title: str,
        requirement: str,
        matching_entry: dict[str, Any] | None,
    ) -> None:
        if matching_entry is None:
            return
        unlocked.append(
            {
                "id": achievement_id,
                "title": title,
                "requirement": requirement,
                "story_title": matching_entry.get("story_title") or "Untitled Adventure",
                "completed_at": str(matching_entry.get("completed_at", "Unknown")),
            }
        )

    add_if_present(
        "story_survivor",
        "Story Survivor",
        "Reach any archived ending.",
        sorted_entries[0],
    )
    add_if_present(
        "branch_cartographer",
        "Branch Cartographer",
        "Finish a run after restoring and diverging from an earlier turn.",
        next(
            (
                entry
                for entry in sorted_entries
                if isinstance(entry.get("divergence_points"), list) and entry["divergence_points"]
            ),
            None,
        ),
    )
    add_if_present(
        "lorekeeper",
        "Lorekeeper",
        "Finish a run after discovering at least 5 codex entries.",
        next(
            (
                entry
                for entry in sorted_entries
                if isinstance(entry.get("discovered_lore_count"), int)
                and int(entry["discovered_lore_count"]) >= 5
            ),
            None,
        ),
    )
    add_if_present(
        "objective_closer",
        "Objective Closer",
        "Finish a run with at least 3 completed objectives.",
        next(
            (
                entry
                for entry in sorted_entries
                if isinstance(entry.get("objective_status_counts"), dict)
                and int(entry["objective_status_counts"].get("completed", 0)) >= 3
            ),
            None,
        ),
    )
    add_if_present(
        "silver_tongue",
        "Silver Tongue",
        "Finish a run with reputation 10+.",
        next(
            (
                entry
                for entry in sorted_entries
                if (
                    isinstance(entry.get("player_stats"), dict)
                    and int(entry["player_stats"].get("reputation", 0)) >= 10
                )
            ),
            None,
        ),
    )
    add_if_present(
        "fellowship_ending",
        "Fellowship Ending",
        "Reach an ending with an active companion still at your side.",
        next(
            (
                entry
                for entry in sorted_entries
                if isinstance(entry.get("companions"), list)
                and any(
                    isinstance(companion, dict) and companion.get("status") == "active"
                    for companion in entry["companions"]
                )
            ),
            None,
        ),
    )
    return unlocked


def identify_newly_unlocked_hidden_achievements(
    previous_archive_entries: list[Any],
    current_archive_entries: list[Any],
) -> list[dict[str, Any]]:
    previous_ids = {
        str(entry.get("id"))
        for entry in derive_hidden_achievements(previous_archive_entries)
        if isinstance(entry.get("id"), str)
    }
    return [
        entry
        for entry in derive_hidden_achievements(current_archive_entries)
        if isinstance(entry.get("id"), str) and str(entry["id"]) not in previous_ids
    ]


def build_hidden_achievements_summary(archive_entries: list[Any]) -> str:
    unlocked = derive_hidden_achievements(archive_entries)
    total_achievements = 6
    if not unlocked:
        return (
            "## Hidden Achievements\n"
            f"- Unlocked: 0 / {total_achievements}\n"
            f"- Still hidden: {total_achievements}\n\n"
            "No hidden achievements have surfaced yet. Finish runs and vary your playstyle to reveal them."
        )

    lines = [
        "## Hidden Achievements",
        f"- Unlocked: {len(unlocked)} / {total_achievements}",
        f"- Still hidden: {total_achievements - len(unlocked)}",
        "",
        "Hidden achievements unlock from archived run history. Undiscovered entries stay concealed.",
    ]

    for index, achievement in enumerate(unlocked, start=1):
        lines.extend(
            [
                "",
                f"## {index}. {achievement['title']}",
                f"- Requirement: {achievement['requirement']}",
                f"- First surfaced in: {achievement['story_title']}",
                f"- Unlocked at: {achievement['completed_at']}",
            ]
        )

    return "\n".join(lines)


def build_endings_discovered_summary(archive_entries: list[Any]) -> str:
    normalized_entries = [
        normalized
        for entry in archive_entries
        if (normalized := _coerce_run_archive_entry(entry)) is not None
    ]
    if not normalized_entries:
        return (
            "## Endings Discovered\n"
            "- Completed runs: 0\n"
            "- Ending types found: 0\n\n"
            "No endings have been archived yet."
        )

    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in normalized_entries:
        grouped.setdefault(str(entry["ending_type"]), []).append(entry)

    lines = [
        "## Endings Discovered",
        f"- Completed runs: {len(normalized_entries)}",
        f"- Ending types found: {len(grouped)}",
    ]

    for ending_type in sorted(grouped, key=lambda value: format_ending_type_label(value)):
        entries = sorted(
            grouped[ending_type],
            key=lambda item: str(item.get("completed_at", "")),
            reverse=True,
        )
        latest = entries[0]
        lines.extend(
            [
                "",
                f"## {format_ending_type_label(ending_type)}",
                f"- Seen: {len(entries)}",
                f"- Latest adventure: {latest.get('story_title') or 'Untitled Adventure'}",
                f"- Latest turn count: {latest.get('turn_count') or 'Unknown'}",
            ]
        )
        divergence_points = latest.get("divergence_points")
        if isinstance(divergence_points, list) and divergence_points:
            lines.append(
                "- Latest divergence points: "
                + ", ".join(f"Turn {turn}" for turn in divergence_points if isinstance(turn, int))
            )
        flags = latest.get("story_flags")
        if isinstance(flags, list) and flags:
            lines.append(
                "- Latest flags: "
                + ", ".join(str(flag) for flag in flags if isinstance(flag, str))[:200]
            )
        narrative = str(latest.get("ending_narrative", "")).strip()
        if narrative:
            preview = narrative[:180] + ("..." if len(narrative) > 180 else "")
            lines.append(f"- Latest ending: {preview}")

    return "\n".join(lines)


def build_run_archive_summary(archive_entries: list[Any]) -> str:
    normalized_entries = [
        normalized
        for entry in archive_entries
        if (normalized := _coerce_run_archive_entry(entry)) is not None
    ]
    if not normalized_entries:
        return (
            "## Run Archive\n- Completed runs: 0\n\nNo completed adventures have been archived yet."
        )

    entries = sorted(
        normalized_entries,
        key=lambda item: str(item.get("completed_at", "")),
        reverse=True,
    )
    ending_types = sorted(
        {format_ending_type_label(str(entry.get("ending_type", "ending"))) for entry in entries}
    )
    lines = [
        "## Run Archive",
        f"- Completed runs: {len(entries)}",
        f"- Ending types: {', '.join(ending_types)}",
    ]

    for index, entry in enumerate(entries, start=1):
        lines.extend(
            [
                "",
                f"## {index}. {entry.get('story_title') or 'Untitled Adventure'}",
                f"- Ending: {entry.get('ending_label') or format_ending_type_label(str(entry.get('ending_type', 'ending')))}",
                f"- Completed: {entry.get('completed_at')}",
                f"- Turns: {entry.get('turn_count') or 'Unknown'}",
            ]
        )
        last_choice_text = entry.get("last_choice_text")
        if isinstance(last_choice_text, str) and last_choice_text.strip():
            lines.append(f"- Final choice: {last_choice_text.strip()}")
        divergence_points = entry.get("divergence_points")
        if isinstance(divergence_points, list) and divergence_points:
            turns = [f"Turn {turn}" for turn in divergence_points if isinstance(turn, int)]
            if turns:
                lines.append(f"- Divergence points: {', '.join(turns)}")
        flags = entry.get("story_flags")
        if isinstance(flags, list) and flags:
            lines.append(
                "- Flags: " + ", ".join(str(flag) for flag in flags if isinstance(flag, str))
            )
        objective_counts = entry.get("objective_status_counts")
        if isinstance(objective_counts, dict) and objective_counts:
            lines.append(
                "- Objectives: "
                f"{objective_counts.get('active', 0)} active | "
                f"{objective_counts.get('completed', 0)} completed | "
                f"{objective_counts.get('failed', 0)} failed"
            )
        inventory = entry.get("inventory")
        if isinstance(inventory, list):
            lines.append(
                "- Inventory: "
                + (", ".join(str(item) for item in inventory if isinstance(item, str)) or "Empty")
            )
        narrative = str(entry.get("ending_narrative", "")).strip()
        if narrative:
            preview = narrative[:220] + ("..." if len(narrative) > 220 else "")
            lines.append(f"- Ending scene: {preview}")

    return "\n".join(lines)
