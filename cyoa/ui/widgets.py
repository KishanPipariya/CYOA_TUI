from typing import Any

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.markup import escape
from textual.reactive import reactive
from textual.widgets import Button, Label, ListItem, ListView, Markdown, ProgressBar, Static, Tree

from cyoa.ui.presenters import (
    format_directives_label,
    format_inventory_label,
    format_objectives_label,
    format_runtime_text,
    format_stats_text,
    loading_story_text,
)

__all__ = [
    "SceneListItem",
    "SaveListItem",
    "OptionListItem",
    "CommandPaletteListItem",
    "InventoryListItem",
    "JournalListItem",
    "StoryViewport",
    "StoryPane",
    "ThemeSpinner",
    "StatusDisplay",
    "StatusBar",
    "ChoicePanel",
    "ActionPanel",
    "JournalPanel",
    "StoryMapPanel",
    "MainGamePanel",
    "GameWorkspace",
]


class SceneListItem(ListItem):
    """ListItem that carries a scene index for branch selection."""

    def __init__(self, *args: Any, scene_index: int = 0, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.scene_index = scene_index


class SaveListItem(ListItem):
    """ListItem that carries a save filename for loading."""

    def __init__(self, *args: Any, save_filename: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.save_filename = save_filename


class OptionListItem(ListItem):
    """List item that carries an arbitrary string value."""

    def __init__(self, *args: Any, option_value: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.option_value = option_value


class CommandPaletteListItem(ListItem):
    """List item that carries a command palette action string."""

    def __init__(self, *args: Any, action_value: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.action_value = action_value


class InventoryListItem(ListItem):
    """List item that carries an inventory entry payload."""

    def __init__(
        self,
        *args: Any,
        item_name: str = "",
        item_summary: str = "",
        discovered_turn: int | None = None,
        related_choices: list[str] | None = None,
        recently_gained: bool = False,
        has_lore: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.item_name = item_name
        self.item_summary = item_summary
        self.discovered_turn = discovered_turn
        self.related_choices = list(related_choices or [])
        self.recently_gained = recently_gained
        self.has_lore = has_lore


class JournalListItem(ListItem):
    """ListItem that points to a narrative turn in the story pane."""

    def __init__(
        self,
        *args: Any,
        scene_index: int = 0,
        entry_kind: str = "choice",
        label_text: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.scene_index = scene_index
        self.entry_kind = entry_kind
        self.label_text = label_text


class StoryViewport(VerticalScroll):
    """Scrollable story viewport that can receive structural focus jumps."""

    can_focus = True


class StoryPane(Container):
    """Organism for the story stream and contextual ASCII art."""

    def __init__(self, *, screen_reader_mode: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._screen_reader_mode = screen_reader_mode

    def compose(self) -> ComposeResult:
        with StoryViewport(id="story-container"):
            yield Markdown(
                loading_story_text(screen_reader_mode=self._screen_reader_mode),
                classes="story-turn",
                id="initial-turn",
            )
            yield Static("", id="scene-art", classes="hidden" if self._screen_reader_mode else "")


class ThemeSpinner(Static):
    """Custom spinner that cycles through configured ASCII frames."""

    def __init__(self, frames: list[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.frames = frames
        self._frame_idx = 0

    def on_mount(self) -> None:
        self.update(escape(self.frames[0]))
        self.set_interval(0.1, self.tick)

    def tick(self) -> None:
        try:
            reduced_motion = bool(getattr(self.app, "reduced_motion", False))
        except Exception:
            reduced_motion = False
        if "hidden" in self.classes or reduced_motion:
            return
        self._frame_idx = (self._frame_idx + 1) % len(self.frames)
        self.update(escape(self.frames[self._frame_idx]))


class StatusDisplay(Static):
    """A reactive status area that groups player state and runtime metadata."""

    can_focus = True
    health = reactive(100)
    gold = reactive(0)
    reputation = reactive(0)
    inventory: reactive[list[str]] = reactive([])
    objectives: reactive[list[str]] = reactive([])
    directives: reactive[list[str]] = reactive([])
    generation_preset = reactive("balanced")
    runtime_profile = reactive("custom")
    provider_label = reactive("llama_cpp")
    engine_phase = reactive("idle")
    latest_status = reactive("Status: Waiting for adventure updates.")
    screen_reader_mode = reactive(False)
    cognitive_load_reduction_mode = reactive(False)
    runtime_metadata_verbosity = reactive("standard")

    def compose(self) -> ComposeResult:
        with Horizontal(id="stats-row"):
            yield Label("Health", id="health-label")
            yield ProgressBar(total=100, show_percentage=False, show_eta=False, id="health-bar")
            yield Label("100% Stable", id="health-value")
        with Container(id="status-meta-row"):
            yield Label("", id="stats-text")
            yield Label("", id="runtime-text")
        yield Label("", id="inventory-label")
        yield Label("", id="objectives-label")
        yield Label("", id="directives-label")
        yield Label("", id="latest-status-label")

    def on_mount(self) -> None:
        self._refresh_accessibility_labels()

    def watch_health(self, health: int) -> None:
        self.query_one("#health-bar", ProgressBar).progress = health
        self.query_one("#health-value", Label).update(
            f"{health}% {self._health_status_text(health)}"
        )
        self._update_stats_text()
        self._set_health_class(health)

    def watch_gold(self, gold: int) -> None:
        self._update_stats_text()

    def watch_reputation(self, reputation: int) -> None:
        self._update_stats_text()

    def watch_inventory(self, inventory: list[str]) -> None:
        self.query_one("#inventory-label", Label).update(
            format_inventory_label(
                inventory,
                screen_reader_mode=self.screen_reader_mode,
                simplified_mode=self.cognitive_load_reduction_mode,
            )
        )

    def watch_objectives(self, objectives: list[str]) -> None:
        self.query_one("#objectives-label", Label).update(
            format_objectives_label(
                objectives,
                screen_reader_mode=self.screen_reader_mode,
                simplified_mode=self.cognitive_load_reduction_mode,
            )
        )

    def _update_stats_text(self) -> None:
        self.query_one(
            "#stats-text",
            Label,
        ).update(
            format_stats_text(
                gold=self.gold,
                reputation=self.reputation,
                screen_reader_mode=self.screen_reader_mode,
                simplified_mode=self.cognitive_load_reduction_mode,
            )
        )
        self.query_one(
            "#runtime-text",
            Label,
        ).update(
            format_runtime_text(
                generation_preset=self.generation_preset,
                engine_phase=self.engine_phase,
                provider_label=self.provider_label,
                runtime_profile=self.runtime_profile,
                screen_reader_mode=self.screen_reader_mode,
                simplified_mode=self.cognitive_load_reduction_mode,
                verbosity=self.runtime_metadata_verbosity,
            )
        )

    def watch_directives(self, directives: list[str]) -> None:
        self.query_one("#directives-label", Label).update(
            format_directives_label(
                directives,
                screen_reader_mode=self.screen_reader_mode,
                simplified_mode=self.cognitive_load_reduction_mode,
            )
        )

    def watch_latest_status(self, latest_status: str) -> None:
        self.query_one("#latest-status-label", Label).update(latest_status)

    def watch_screen_reader_mode(self, _enabled: bool) -> None:
        self._refresh_accessibility_labels()

    def watch_cognitive_load_reduction_mode(self, enabled: bool) -> None:
        self.query_one("#runtime-text", Label).set_class(enabled, "hidden")
        self.query_one("#directives-label", Label).set_class(enabled, "hidden")
        self._refresh_accessibility_labels()

    def watch_generation_preset(self, _preset: str) -> None:
        self._update_stats_text()

    def watch_runtime_profile(self, _profile: str) -> None:
        self._update_stats_text()

    def watch_provider_label(self, _provider: str) -> None:
        self._update_stats_text()

    def watch_engine_phase(self, _phase: str) -> None:
        self._update_stats_text()

    def watch_runtime_metadata_verbosity(self, _verbosity: str) -> None:
        self._update_stats_text()

    def _set_health_class(self, health: int) -> None:
        self.remove_class("health-high", "health-mid", "health-low")
        if health < 30:
            self.add_class("health-low")
        elif health < 70:
            self.add_class("health-mid")
        else:
            self.add_class("health-high")

    @staticmethod
    def _health_status_text(health: int) -> str:
        if health < 30:
            return "Critical"
        if health < 70:
            return "Watch"
        return "Stable"

    def _refresh_accessibility_labels(self) -> None:
        self.watch_inventory(self.inventory)
        self.watch_objectives(self.objectives)
        self.watch_directives(self.directives)
        self.watch_latest_status(self.latest_status)
        self._update_stats_text()


class StatusBar(Container):
    """Organism for loading state and runtime/player status."""

    def __init__(self, *, spinner_frames: list[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._spinner_frames = spinner_frames

    def compose(self) -> ComposeResult:
        yield ThemeSpinner(frames=self._spinner_frames, id="loading", classes="hidden")
        yield StatusDisplay(id="status-display")


class ChoicePanel(Container):
    """Organism that hosts the current turn's available actions."""


class ActionPanel(Container):
    """Shared lower dock for runtime status and available actions."""

    def __init__(self, *, spinner_frames: list[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._spinner_frames = spinner_frames

    def compose(self) -> ComposeResult:
        yield StatusBar(spinner_frames=self._spinner_frames, id="status-bar")
        with Vertical(id="action-dock"):
            with Horizontal(classes="action-dock-row"):
                yield Button("Journal", id="btn-compact-journal")
                yield Button("Map", id="btn-compact-map")
                yield Button("Notices", id="btn-compact-messages")
                yield Button("Recap", id="btn-compact-recap")
        yield ChoicePanel(id="choices-container")


class JournalPanel(Container):
    """Organism for the in-game journal side panel."""

    def compose(self) -> ComposeResult:
        with Container(classes="side-panel-shell side-panel-journal"):
            yield Label("In-Game Journal", id="journal-title")
            yield Label("Recent decisions and fractures", classes="side-panel-kicker")
            with Container(classes="side-panel-body"):
                yield Label("Timeline Log", classes="side-panel-section-title")
                yield ListView(id="journal-list")


class StoryMapPanel(Container):
    """Organism for the branching story-map side panel."""

    def compose(self) -> ComposeResult:
        with Container(classes="side-panel-shell side-panel-map"):
            yield Label("Story Map", id="story-map-title")
            yield Label("Branch structure and current route", classes="side-panel-kicker")
            with Container(classes="side-panel-body"):
                yield Label("Adventure Topology", classes="side-panel-section-title")
                yield Tree("Story", id="story-map-tree")


class MainGamePanel(Vertical):
    """Organism for the main play area within the workspace template."""

    def __init__(
        self, *, spinner_frames: list[str], screen_reader_mode: bool = False, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self._spinner_frames = spinner_frames
        self._screen_reader_mode = screen_reader_mode

    def compose(self) -> ComposeResult:
        yield StoryPane(screen_reader_mode=self._screen_reader_mode)
        yield ActionPanel(spinner_frames=self._spinner_frames, id="action-panel")


class GameWorkspace(Horizontal):
    """Template for the primary in-game workspace."""

    def __init__(
        self, *, spinner_frames: list[str], screen_reader_mode: bool = False, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self._spinner_frames = spinner_frames
        self._screen_reader_mode = screen_reader_mode

    def compose(self) -> ComposeResult:
        yield MainGamePanel(
            spinner_frames=self._spinner_frames,
            screen_reader_mode=self._screen_reader_mode,
            id="main-container",
        )
        yield JournalPanel(id="journal-panel", classes="panel-collapsed")
        yield StoryMapPanel(id="story-map-panel", classes="panel-collapsed")
