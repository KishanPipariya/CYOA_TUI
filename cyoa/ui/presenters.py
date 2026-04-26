import re
import unicodedata
from typing import Any

from cyoa.core import constants
from cyoa.ui.keybindings import APP_BINDING_SPECS, format_key_for_display

MARKUP_TAG_RE = re.compile(r"\[/?[a-zA-Z][^\]]*\]")


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


def format_status_message(message: str, *, screen_reader_mode: bool) -> str:
    cleaned = _strip_markup(message.strip())
    if not screen_reader_mode:
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


def format_inventory_label(inventory: list[str], *, screen_reader_mode: bool = False) -> str:
    prefix = "Inventory" if screen_reader_mode else "🎒 Inventory"
    return f"{prefix}: {', '.join(inventory)}" if inventory else f"{prefix}: Empty"


def format_objectives_label(objectives: list[str], *, screen_reader_mode: bool = False) -> str:
    prefix = "Objectives" if screen_reader_mode else "🎯 Objectives"
    return f"{prefix}: {' | '.join(objectives[:2])}" if objectives else f"{prefix}: None"


def format_directives_label(directives: list[str], *, screen_reader_mode: bool = False) -> str:
    prefix = "Directives" if screen_reader_mode else "🧭 Directives"
    return f"{prefix}: {' | '.join(directives[:2])}" if directives else f"{prefix}: None"


def format_stats_text(
    *,
    gold: int,
    reputation: int,
    screen_reader_mode: bool = False,
) -> str:
    if screen_reader_mode:
        return f"Gold {gold} | Reputation {reputation}"
    return f"🪙 Gold {gold}  •  🌟 Reputation {reputation}"


def format_runtime_text(
    *,
    generation_preset: str,
    engine_phase: str,
    provider_label: str,
    runtime_profile: str,
    screen_reader_mode: bool = False,
) -> str:
    if screen_reader_mode:
        return (
            f"Preset {generation_preset} | Phase {engine_phase} | "
            f"Provider {provider_label} | Profile {runtime_profile}"
        )
    return (
        f"⚙️ {generation_preset}  •  ⏱ {engine_phase}  •  🖧 {provider_label}  •  ⛭ {runtime_profile}"
    )


def build_choice_label(
    index: int,
    choice_text: str,
    disabled_reason: str | None = None,
    *,
    screen_reader_mode: bool = False,
) -> str:
    label = (
        f"{index + 1}. {choice_text}"
        if screen_reader_mode
        else f"[b]{index + 1}.[/b] {choice_text}"
    )
    if disabled_reason:
        reason = format_status_message(disabled_reason, screen_reader_mode=screen_reader_mode)
        detail_lines = "\n".join(f"- {part.strip()}" for part in reason.split("|"))
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


def build_help_text(
    *,
    screen_reader_mode: bool,
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
- High Contrast mode uses a fixed readable palette for story cards, choices, and side panels.
- Key bindings can be customized in Settings. Footer hints and this help sheet follow your saved keys.
- Locked choices include a written reason and do not rely on color alone.
- Reduced Motion is available in Settings and disables spinner animation and narrated text animation.
- Journal and Story Map panels move keyboard focus automatically when opened.

---

*Press Escape or click Close to return to the adventure.*
"""


def _help_key_cell(key: str) -> str:
    display = format_key_for_display(key)
    return f" {display} "
