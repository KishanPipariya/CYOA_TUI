from typing import Any, cast

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.markup import escape
from textual.screen import ModalScreen
from textual.widgets import Button, Label, ListItem, ListView, Markdown, Static

from cyoa.ui.dialogs import DialogActions, DialogFrame, LoadGameScreen
from cyoa.ui.presenters import (
    build_help_text,
    build_inventory_empty_summary,
    build_inventory_inspector_entries,
    build_inventory_item_summary,
)
from cyoa.ui.widgets import InventoryListItem

__all__ = [
    "HelpScreen",
    "NotificationHistoryScreen",
    "SceneRecapScreen",
    "InventoryInspectorScreen",
    "CharacterSheetScreen",
    "LoreCodexScreen",
    "EndingsDiscoveredScreen",
    "HiddenAchievementsScreen",
    "RunArchiveScreen",
    "ReplayScreen",
    "AccessibleSummaryScreen",
]


class HelpScreen(ModalScreen[None]):
    """Full-screen help overlay showing keybindings and game mechanics."""

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
        background: $background 80%;
    }
    #help-dialog {
        width: 70;
        height: 80%;
    }
    #btn-help-close {
        width: 100%;
        margin-top: 1;
    }
    """

    BINDINGS = [
        ("escape", "close", "Close"),
        ("h", "close", "Close"),
    ]

    def __init__(
        self,
        *,
        screen_reader_mode: bool = False,
        cognitive_load_reduction_mode: bool = False,
        current_bindings: dict[str, str] | None = None,
        safety_summary: str = "",
        safety_advisories: tuple[str, ...] = (),
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._screen_reader_mode = screen_reader_mode
        self._cognitive_load_reduction_mode = cognitive_load_reduction_mode
        self._current_bindings = current_bindings or {}
        self._safety_summary = safety_summary
        self._safety_advisories = safety_advisories

    def compose(self) -> ComposeResult:
        with DialogFrame(
            id="help-dialog", classes="dialog-frame dialog-frame-scroll dialog-frame-accent"
        ):
            with Container(id="help-content", classes="dialog-content"):
                yield Markdown(
                    build_help_text(
                        screen_reader_mode=self._screen_reader_mode,
                        cognitive_load_reduction_mode=self._cognitive_load_reduction_mode,
                        current_bindings=self._current_bindings,
                        safety_summary=self._safety_summary,
                        safety_advisories=self._safety_advisories,
                    ),
                    id="help-text",
                )
            yield Button("Close [b](Esc)[/b]", id="btn-help-close", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#btn-help-close", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-help-close":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class NotificationHistoryScreen(ModalScreen[None]):
    """Modal screen that exposes recent notifications in chronological order."""

    DEFAULT_CSS = LoadGameScreen.DEFAULT_CSS
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, entries: list[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._entries = entries

    def compose(self) -> ComposeResult:
        with DialogFrame(
            id="notification-history-dialog", classes="dialog-frame dialog-frame-scroll"
        ):
            yield Label(
                "[b]Notification History[/b]",
                id="notification-history-title",
                classes="dialog-title",
            )
            if self._entries:
                yield ListView(id="notification-history-list", classes="dialog-list")
            else:
                yield Static(
                    "No notifications yet.", id="notification-history-empty", classes="dialog-entry"
                )
            yield Button(
                "Close [b](Esc)[/b]", id="btn-notification-history-close", variant="primary"
            )

    def on_mount(self) -> None:
        if not self._entries:
            self.query_one("#btn-notification-history-close", Button).focus()
            return

        list_view = self.query_one("#notification-history-list", ListView)
        for index, entry in enumerate(self._entries, start=1):
            list_view.append(ListItem(Label(escape(f"{index}. {entry}"), classes="dialog-entry")))
        list_view.index = len(self._entries) - 1
        list_view.scroll_end(animate=False)
        self.call_after_refresh(list_view.focus)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-notification-history-close":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class SceneRecapScreen(ModalScreen[None]):
    """Modal screen showing a structured recap of the current scene and state."""

    DEFAULT_CSS = HelpScreen.DEFAULT_CSS
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, recap_text: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._recap_text = recap_text

    def compose(self) -> ComposeResult:
        with DialogFrame(
            id="scene-recap-dialog", classes="dialog-frame dialog-frame-scroll dialog-frame-accent"
        ):
            with Container(id="scene-recap-content", classes="dialog-content"):
                yield Markdown(self._recap_text, id="scene-recap-text")
            yield Button("Close [b](Esc)[/b]", id="btn-scene-recap-close", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#btn-scene-recap-close", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-scene-recap-close":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class InventoryInspectorScreen(ModalScreen[None]):
    """Modal screen for item-first inventory inspection and discovered item lore."""

    DEFAULT_CSS = """
    InventoryInspectorScreen {
        align: center middle;
        background: $background 80%;
    }
    #inventory-inspector-dialog {
        width: 78;
        height: 80%;
        max-width: 96%;
    }
    #inventory-inspector-list {
        height: 9;
        margin: 1 0;
    }
    #inventory-inspector-text {
        height: 1fr;
    }
    #btn-inventory-inspector-close {
        width: 100%;
        margin-top: 1;
    }
    """

    BINDINGS = [("escape", "close", "Close")]

    def __init__(
        self,
        *,
        story_title: str | None,
        turn_count: int,
        inventory: list[str],
        lore_entries: list[Any],
        choices: list[Any],
        items_gained: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._story_title = story_title
        self._turn_count = turn_count
        self._entries = build_inventory_inspector_entries(
            inventory=inventory,
            lore_entries=lore_entries,
            choices=choices,
            items_gained=items_gained,
        )

    def compose(self) -> ComposeResult:
        with DialogFrame(
            id="inventory-inspector-dialog",
            classes="dialog-frame dialog-frame-scroll dialog-frame-accent",
        ):
            yield Label(
                "[b]Inventory Inspector[/b]",
                id="inventory-inspector-title",
                classes="dialog-title",
            )
            yield Label(
                "Inspect carried items for discovered lore and current choice hooks.",
                id="inventory-inspector-kicker",
                classes="dialog-entry",
            )
            if self._entries:
                yield ListView(id="inventory-inspector-list", classes="dialog-list")
            with Container(id="inventory-inspector-content", classes="dialog-content"):
                yield Markdown("", id="inventory-inspector-text")
            yield Button(
                "Close [b](Esc)[/b]", id="btn-inventory-inspector-close", variant="primary"
            )

    def on_mount(self) -> None:
        detail = self.query_one("#inventory-inspector-text", Markdown)
        if not self._entries:
            detail.update(
                build_inventory_empty_summary(
                    story_title=self._story_title,
                    turn_count=self._turn_count,
                )
            )
            self.query_one("#btn-inventory-inspector-close", Button).focus()
            return

        list_view = self.query_one("#inventory-inspector-list", ListView)
        for entry in self._entries:
            suffixes: list[str] = []
            if entry["has_lore"]:
                suffixes.append("lore")
            if entry["related_choices"]:
                suffixes.append("hook")
            if entry["recently_gained"]:
                suffixes.append("new")
            label = entry["name"]
            if suffixes:
                label = f"{label} [{' | '.join(suffixes)}]"
            list_view.append(
                InventoryListItem(
                    Label(escape(label), classes="dialog-entry"),
                    item_name=entry["name"],
                    item_summary=entry["summary"],
                    discovered_turn=cast(int | None, entry["discovered_turn"]),
                    related_choices=cast(list[str], entry["related_choices"]),
                    recently_gained=bool(entry["recently_gained"]),
                    has_lore=bool(entry["has_lore"]),
                )
            )
        list_view.index = 0
        first_item = next(iter(list_view.query(InventoryListItem)), None)
        if first_item is not None:
            self._update_selected_item(first_item)
        self.call_after_refresh(list_view.focus)

    def _update_selected_item(self, item: InventoryListItem) -> None:
        self.query_one("#inventory-inspector-text", Markdown).update(
            build_inventory_item_summary(
                story_title=self._story_title,
                turn_count=self._turn_count,
                item_name=item.item_name,
                item_summary=item.item_summary,
                discovered_turn=item.discovered_turn,
                related_choices=item.related_choices,
                recently_gained=item.recently_gained,
                has_lore=item.has_lore,
            )
        )

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, InventoryListItem):
            self._update_selected_item(event.item)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-inventory-inspector-close":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class CharacterSheetScreen(ModalScreen[None]):
    """Modal screen showing the player's persistent state in readable sections."""

    DEFAULT_CSS = HelpScreen.DEFAULT_CSS
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, summary_text: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._summary_text = summary_text

    def compose(self) -> ComposeResult:
        with DialogFrame(
            id="character-sheet-dialog",
            classes="dialog-frame dialog-frame-scroll dialog-frame-accent",
        ):
            with Container(id="character-sheet-content", classes="dialog-content"):
                yield Label(
                    "[b]Character Sheet[/b]", id="character-sheet-title", classes="dialog-title"
                )
                yield Markdown(self._summary_text, id="character-sheet-text")
            yield Button("Close [b](Esc)[/b]", id="btn-character-sheet-close", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#btn-character-sheet-close", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-character-sheet-close":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class LoreCodexScreen(ModalScreen[None]):
    """Modal screen showing discovered lore entries grouped by category."""

    DEFAULT_CSS = HelpScreen.DEFAULT_CSS
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, summary_text: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._summary_text = summary_text

    def compose(self) -> ComposeResult:
        with DialogFrame(
            id="lore-codex-dialog",
            classes="dialog-frame dialog-frame-scroll dialog-frame-accent",
        ):
            with Container(id="lore-codex-content", classes="dialog-content"):
                yield Label("[b]Lore Codex[/b]", id="lore-codex-title", classes="dialog-title")
                yield Markdown(self._summary_text, id="lore-codex-text")
            yield Button("Close [b](Esc)[/b]", id="btn-lore-codex-close", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#btn-lore-codex-close", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-lore-codex-close":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class EndingsDiscoveredScreen(ModalScreen[None]):
    """Modal screen summarizing discovered ending types."""

    DEFAULT_CSS = HelpScreen.DEFAULT_CSS
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, summary_text: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._summary_text = summary_text

    def compose(self) -> ComposeResult:
        with DialogFrame(
            id="endings-discovered-dialog",
            classes="dialog-frame dialog-frame-scroll dialog-frame-accent",
        ):
            with Container(id="endings-discovered-content", classes="dialog-content"):
                yield Label(
                    "[b]Endings Discovered[/b]",
                    id="endings-discovered-title",
                    classes="dialog-title",
                )
                yield Markdown(self._summary_text, id="endings-discovered-text")
            yield Button("Close [b](Esc)[/b]", id="btn-endings-discovered-close", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#btn-endings-discovered-close", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-endings-discovered-close":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class HiddenAchievementsScreen(ModalScreen[None]):
    """Modal screen summarizing hidden achievements unlocked from run history."""

    DEFAULT_CSS = HelpScreen.DEFAULT_CSS
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, summary_text: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._summary_text = summary_text

    def compose(self) -> ComposeResult:
        with DialogFrame(
            id="hidden-achievements-dialog",
            classes="dialog-frame dialog-frame-scroll dialog-frame-accent",
        ):
            with Container(id="hidden-achievements-content", classes="dialog-content"):
                yield Label(
                    "[b]Hidden Achievements[/b]",
                    id="hidden-achievements-title",
                    classes="dialog-title",
                )
                yield Markdown(self._summary_text, id="hidden-achievements-text")
            yield Button(
                "Close [b](Esc)[/b]", id="btn-hidden-achievements-close", variant="primary"
            )

    def on_mount(self) -> None:
        self.query_one("#btn-hidden-achievements-close", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-hidden-achievements-close":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class RunArchiveScreen(ModalScreen[None]):
    """Modal screen comparing completed archived runs."""

    DEFAULT_CSS = HelpScreen.DEFAULT_CSS
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, summary_text: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._summary_text = summary_text

    def compose(self) -> ComposeResult:
        with DialogFrame(
            id="run-archive-dialog",
            classes="dialog-frame dialog-frame-scroll dialog-frame-accent",
        ):
            with Container(id="run-archive-content", classes="dialog-content"):
                yield Label("[b]Run Archive[/b]", id="run-archive-title", classes="dialog-title")
                yield Markdown(self._summary_text, id="run-archive-text")
            yield Button("Close [b](Esc)[/b]", id="btn-run-archive-close", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#btn-run-archive-close", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-run-archive-close":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class ReplayScreen(ModalScreen[None]):
    """Modal screen for stepping through a recorded playthrough."""

    DEFAULT_CSS = """
    ReplayScreen {
        align: center middle;
        background: $background 80%;
    }
    #replay-dialog {
        width: 78;
        height: 80%;
        max-width: 96%;
    }
    #replay-status {
        margin-bottom: 1;
    }
    #replay-text {
        height: 1fr;
    }
    #replay-buttons {
        width: 1fr;
        margin-top: 1;
    }
    #replay-buttons Button {
        width: 1fr;
    }
    """

    BINDINGS = [
        ("escape", "close", "Close"),
        ("left", "previous_step", "Previous"),
        ("right", "next_step", "Next"),
        ("p", "previous_step", "Previous"),
        ("n", "next_step", "Next"),
        ("home", "first_step", "First"),
        ("end", "last_step", "Last"),
    ]

    def __init__(self, steps: list[dict[str, object]], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._steps = steps or [
            {
                "title": "Replay",
                "label": "Empty",
                "text": "No recorded story turns are available yet.",
                "index": 1,
            }
        ]
        self._index = 0

    def compose(self) -> ComposeResult:
        with DialogFrame(
            id="replay-dialog", classes="dialog-frame dialog-frame-scroll dialog-frame-accent"
        ):
            yield Label("[b]Replay[/b]", id="replay-title", classes="dialog-title")
            yield Static("", id="replay-status", classes="dialog-entry")
            with Container(id="replay-content", classes="dialog-content"):
                yield Markdown("", id="replay-text")
            with DialogActions(id="replay-buttons", classes="dialog-actions"):
                yield Button("Previous", id="btn-replay-previous")
                yield Button("Next", id="btn-replay-next", variant="primary")
                yield Button("Close [b](Esc)[/b]", id="btn-replay-close", variant="error")

    def on_mount(self) -> None:
        self.call_after_refresh(self._render_step)
        self.call_after_refresh(lambda: self.query_one("#btn-replay-next", Button).focus())

    def _render_step(self) -> None:
        step = self._steps[self._index]
        title = str(step.get("title") or "Replay Step")
        label = str(step.get("label") or "Step")
        text = str(step.get("text") or "")
        self.query_one("#replay-status", Static).update(
            f"{label} {self._index + 1} of {len(self._steps)}: {title}"
        )
        self.query_one("#replay-text", Markdown).update(f"## {title}\n\n{text}")
        self.query_one("#btn-replay-previous", Button).disabled = self._index <= 0
        self.query_one("#btn-replay-next", Button).disabled = self._index >= len(self._steps) - 1

    def action_previous_step(self) -> None:
        if self._index <= 0:
            return
        self._index -= 1
        self._render_step()

    def action_next_step(self) -> None:
        if self._index >= len(self._steps) - 1:
            return
        self._index += 1
        self._render_step()

    def action_first_step(self) -> None:
        self._index = 0
        self._render_step()

    def action_last_step(self) -> None:
        self._index = len(self._steps) - 1
        self._render_step()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-replay-previous":
            self.action_previous_step()
        elif event.button.id == "btn-replay-next":
            self.action_next_step()
        elif event.button.id == "btn-replay-close":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class AccessibleSummaryScreen(ModalScreen[str | None]):
    """Modal screen for text-first journal and story-map summaries."""

    DEFAULT_CSS = HelpScreen.DEFAULT_CSS
    BINDINGS = [
        ("escape", "close", "Close"),
        ("[", "show_journal", "Journal Summary"),
        ("]", "show_story_map", "Map Summary"),
    ]

    def __init__(self, title: str, summary_text: str, *, active: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._title = title
        self._summary_text = summary_text
        self._active = active

    def compose(self) -> ComposeResult:
        with DialogFrame(
            id="accessible-summary-dialog",
            classes="dialog-frame dialog-frame-scroll dialog-frame-accent",
        ):
            yield Label(self._title, id="accessible-summary-title", classes="dialog-title")
            with Horizontal(classes="settings-row settings-section"):
                yield Button("Journal Summary", id="btn-accessible-summary-journal")
                yield Button("Map Summary", id="btn-accessible-summary-map")
            with Container(id="accessible-summary-content", classes="dialog-content"):
                yield Markdown(self._summary_text, id="accessible-summary-text")
            yield Button("Close [b](Esc)[/b]", id="btn-accessible-summary-close", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#btn-accessible-summary-journal", Button).variant = (
            "primary" if self._active == "journal" else "default"
        )
        self.query_one("#btn-accessible-summary-map", Button).variant = (
            "primary" if self._active == "story_map" else "default"
        )
        self.query_one("#btn-accessible-summary-close", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-accessible-summary-journal":
            self.dismiss("journal")
        elif event.button.id == "btn-accessible-summary-map":
            self.dismiss("story_map")
        elif event.button.id == "btn-accessible-summary-close":
            self.dismiss(None)

    def action_show_journal(self) -> None:
        self.dismiss("journal")

    def action_show_story_map(self) -> None:
        self.dismiss("story_map")

    def action_close(self) -> None:
        self.dismiss(None)
