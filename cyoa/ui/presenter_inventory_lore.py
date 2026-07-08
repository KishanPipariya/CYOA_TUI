from typing import Any


def _extract_inventory_lore_record(entry: Any) -> tuple[str, dict[str, Any]] | None:
    if isinstance(entry, dict):
        category = entry.get("category")
        name = entry.get("name")
        summary = entry.get("summary")
        discovered_turn = entry.get("discovered_turn")
    else:
        category = getattr(entry, "category", None)
        name = getattr(entry, "name", None)
        summary = getattr(entry, "summary", None)
        discovered_turn = getattr(entry, "discovered_turn", None)

    if category != "item" or not isinstance(name, str) or not isinstance(summary, str):
        return None

    normalized_name = name.strip()
    normalized_summary = summary.strip()
    if not normalized_name or not normalized_summary:
        return None

    return normalized_name.casefold(), {
        "summary": normalized_summary,
        "discovered_turn": discovered_turn if isinstance(discovered_turn, int) else None,
    }


def _build_related_choices_by_item(choices: list[Any]) -> dict[str, list[str]]:
    related_choices_by_item: dict[str, list[str]] = {}
    for choice in choices:
        choice_text = getattr(choice, "text", None)
        requirements = getattr(choice, "requirements", None)
        required_items = getattr(requirements, "items", None)
        if not isinstance(choice_text, str) or not isinstance(required_items, list):
            continue
        for item in required_items:
            if not isinstance(item, str) or not item.strip():
                continue
            related_choices_by_item.setdefault(item.casefold(), []).append(choice_text.strip())
    return related_choices_by_item


def build_inventory_inspector_entries(
    *,
    inventory: list[str],
    lore_entries: list[Any],
    choices: list[Any],
    items_gained: list[str] | None = None,
) -> list[dict[str, Any]]:
    lore_by_item: dict[str, dict[str, Any]] = {}
    for entry in lore_entries:
        lore_record = _extract_inventory_lore_record(entry)
        if lore_record is not None:
            key, value = lore_record
            lore_by_item[key] = value

    related_choices_by_item = _build_related_choices_by_item(choices)

    gained_items = {
        item.casefold() for item in items_gained or [] if isinstance(item, str) and item.strip()
    }
    entries: list[dict[str, Any]] = []
    for item in inventory:
        if not isinstance(item, str) or not item.strip():
            continue
        lore = lore_by_item.get(item.casefold())
        entries.append(
            {
                "name": item,
                "summary": (
                    lore["summary"] if lore else "No discovered lore is tied to this item yet."
                ),
                "discovered_turn": lore["discovered_turn"] if lore else None,
                "related_choices": related_choices_by_item.get(item.casefold(), []),
                "recently_gained": item.casefold() in gained_items,
                "has_lore": lore is not None,
            }
        )
    return entries


def build_inventory_item_summary(
    *,
    story_title: str | None,
    turn_count: int,
    item_name: str,
    item_summary: str,
    discovered_turn: int | None,
    related_choices: list[str],
    recently_gained: bool = False,
    has_lore: bool = False,
) -> str:
    lines = [
        "## Inventory",
        f"- Adventure: {story_title or 'Untitled Adventure'}",
        f"- Turn: {turn_count}",
        "",
        "## Item",
        f"- Name: {item_name}",
        f"- Lore discovered: {'Yes' if has_lore else 'Not yet'}",
    ]
    if discovered_turn is not None:
        lines.append(f"- First recorded: Turn {discovered_turn}")
    if recently_gained:
        lines.append("- Status: Newly acquired this turn")

    lines.extend(["", "## Hidden Lore", item_summary, "", "## Current Uses"])
    if related_choices:
        lines.extend(f"- {choice}" for choice in related_choices)
    else:
        lines.append("- No current choice requirements mention this item.")

    return "\n".join(lines)


def build_inventory_empty_summary(
    *,
    story_title: str | None,
    turn_count: int,
) -> str:
    return "\n".join(
        [
            "## Inventory",
            f"- Adventure: {story_title or 'Untitled Adventure'}",
            f"- Turn: {turn_count}",
            "- Items carried: 0",
            "",
            "No items are currently in your inventory.",
        ]
    )


def build_lore_codex_summary(
    *,
    story_title: str | None,
    turn_count: int,
    lore_entries: list[Any],
) -> str:
    grouped: dict[str, list[tuple[str, str, int | None]]] = {
        "npc": [],
        "location": [],
        "faction": [],
        "item": [],
    }

    for entry in lore_entries:
        if isinstance(entry, dict):
            category = entry.get("category")
            name = entry.get("name")
            summary = entry.get("summary")
            discovered_turn = entry.get("discovered_turn")
        else:
            category = getattr(entry, "category", None)
            name = getattr(entry, "name", None)
            summary = getattr(entry, "summary", None)
            discovered_turn = getattr(entry, "discovered_turn", None)

        if category not in grouped or not isinstance(name, str) or not isinstance(summary, str):
            continue
        normalized_name = name.strip()
        normalized_summary = summary.strip()
        if not normalized_name or not normalized_summary:
            continue
        grouped[category].append(
            (
                normalized_name,
                normalized_summary,
                discovered_turn if isinstance(discovered_turn, int) else None,
            )
        )

    lines = [
        "## Overview",
        f"- Adventure: {story_title or 'Untitled Adventure'}",
        f"- Turn: {turn_count}",
        f"- Entries discovered: {sum(len(items) for items in grouped.values())}",
    ]

    for title, key in (
        ("NPCs", "npc"),
        ("Locations", "location"),
        ("Factions", "faction"),
        ("Items", "item"),
    ):
        lines.extend(["", f"## {title}"])
        entries = sorted(grouped[key], key=lambda item: (item[0].casefold(), item[2] or 0))
        if not entries:
            lines.append("- None discovered")
            continue
        for name, summary, discovered_turn in entries:
            suffix = f" (Turn {discovered_turn})" if discovered_turn is not None else ""
            lines.append(f"- {name}{suffix}: {summary}")

    return "\n".join(lines)
