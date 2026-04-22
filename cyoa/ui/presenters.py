from typing import Any


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


def format_inventory_label(inventory: list[str]) -> str:
    return f"🎒 Inventory: {', '.join(inventory)}" if inventory else "🎒 Inventory: Empty"


def format_objectives_label(objectives: list[str]) -> str:
    return f"🎯 Objectives: {' | '.join(objectives[:2])}" if objectives else "🎯 Objectives: None"


def format_directives_label(directives: list[str]) -> str:
    return f"🧭 Directives: {' | '.join(directives[:2])}" if directives else "🧭 Directives: None"


def format_stats_text(
    *,
    gold: int,
    reputation: int,
) -> str:
    return f"🪙 Gold {gold}  •  🌟 Reputation {reputation}"


def format_runtime_text(
    *,
    generation_preset: str,
    engine_phase: str,
    provider_label: str,
    runtime_profile: str,
) -> str:
    return f"⚙️ {generation_preset}  •  ⏱ {engine_phase}  •  🖧 {provider_label}  •  ⛭ {runtime_profile}"


def build_choice_label(index: int, choice_text: str, disabled_reason: str | None = None) -> str:
    label = f"[b]{index + 1}.[/b] {choice_text}"
    if disabled_reason:
        return f"{label}\n[dim]Unavailable: {disabled_reason}[/dim]"
    return label
