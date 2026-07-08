from dataclasses import dataclass
from typing import Any

from cyoa.core import constants
from cyoa.ui.keybindings import APP_BINDING_SPECS, format_key_for_display
from cyoa.ui.presenter_shared import (
    _locked_reason_lines,
    _use_plain_labels,
    normalize_verbosity,
)


@dataclass(frozen=True, slots=True)
class RuntimeTextInput:
    generation_preset: str
    engine_phase: str
    provider_label: str
    runtime_profile: str
    screen_reader_mode: bool = False
    simplified_mode: bool = False
    verbosity: str = "standard"


def loading_story_text(*, screen_reader_mode: bool) -> str:
    return "Loading story..." if screen_reader_mode else constants.LOADING_ART


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


def build_choice_label(
    index: int,
    choice_text: str,
    disabled_reason: str | None = None,
    *,
    screen_reader_mode: bool = False,
    verbosity: str = "standard",
    hint_lines: list[str] | None = None,
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
    if hint_lines:
        cleaned_lines = [line.strip() for line in hint_lines if line.strip()]
        if cleaned_lines:
            if screen_reader_mode:
                return f"{label}\n" + "\n".join(cleaned_lines)
            return f"{label}\n" + "\n".join(f"[dim]{line}[/dim]" for line in cleaned_lines)
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


def build_help_text(
    *,
    screen_reader_mode: bool,
    cognitive_load_reduction_mode: bool = False,
    current_bindings: dict[str, str] | None = None,
    safety_summary: str = "",
    safety_advisories: tuple[str, ...] = (),
) -> str:
    bindings = current_bindings or {}
    safety_lines = [line for line in (safety_summary, *safety_advisories) if line]
    safety_bullets = "\n".join(f"- {line}" for line in safety_lines)
    safety_block = (
        "\n".join(["- Active safety profile is shown in Settings.", safety_bullets])
        if safety_bullets
        else "- Active safety profile is shown in Settings."
    )
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

# Play Loop

- Use number keys or arrow keys to choose, then press Enter to confirm.
- Branch rewinds from an older turn without deleting manual saves or bookmarks.
- Save and load manage full runs, while bookmarks create fast named restore points.
- Export writes markdown, accessible markdown, and JSON copies of the current adventure.
- Generation preset cycling and directive editing let you tune the active run mid-session.

---

# Panels And Reference Views

- Help, Settings, and the command palette expose the full action surface without leaving the keyboard.
- Inventory Inspector surfaces carried items, hidden lore, and current item hooks.
- Scene Recap summarizes the current turn, Character shows persistent state, and Codex lists discovered lore.
- Endings Discovered groups seen ending types, Hidden Achievements tracks unlocked meta-goals, and Run Archive compares completed adventures by flags and branch history.
- Journal Summary and Story Map Summary provide text-first review modes for long sessions.
- Repeat Status and notification history make transient status messages reviewable.

---

# Accessibility

- Screen Reader Friendly mode removes ASCII art, uses plainer labels, and keeps the latest status message in the status panel.
- Cognitive Load Reduction mode trims side-panel detail and uses simpler wording in status updates.
- Verbosity controls let you tune notifications, recaps, runtime metadata, and locked-choice detail separately. Screen Reader Friendly keeps plain wording, while Cognitive Load Reduction may still hide lower-priority runtime detail.
- High Contrast mode uses a fixed readable palette for story cards, choices, and side panels.
- Key bindings can be customized in Settings. Footer hints and this help sheet follow your saved keys.
- Reduced Motion disables spinner animation and narrated text animation.
- Journal and Story Map panels move keyboard focus automatically when opened.
{safety_block}

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

# 🧭 Adventure Flow

- Choose with number keys or arrow keys, then confirm with Enter.
- Branch lets you revisit an earlier scene without deleting your manual saves or bookmarks.
- Save and load manage full runs; bookmarks give you fast named checkpoints.
- Export writes markdown, accessible markdown, and JSON copies of the current story.
- `g` cycles generation presets and `x` edits run-specific directives while you play.

---

# 🗂️ Reference Views

- `h`, `o`, and the command palette keep help, settings, and action discovery close at hand.
- Inventory Inspector, Recap, Character, and Codex cover carried items, the current scene, persistent stats, and discovered lore.
- Endings Discovered, Hidden Achievements, and Run Archive summarize completed adventures, unlocked meta-goals, ending types, and divergence points.
- Journal Summary and Story Map Summary turn long runs into readable linear summaries.
- `n` repeats the latest status and notification history keeps recent messages reviewable.

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
{safety_block}

---

*Press Escape or click Close to return to the adventure.*
"""


def _help_key_cell(key: str) -> str:
    display = format_key_for_display(key)
    return f" {display} "
