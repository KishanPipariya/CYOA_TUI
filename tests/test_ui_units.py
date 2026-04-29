import asyncio
import os
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, call

import pytest
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.theme import Theme
from textual.widgets import Button, Label, ListView, ProgressBar

from cyoa.core import constants
from cyoa.core.models import (
    Choice,
    ChoiceCheck,
    ChoiceRequirement,
    LoreEntry,
    ResolvedChoiceCheck,
    StoryNode,
)
from cyoa.core.runtime import EnginePhase, EngineTransition
from cyoa.core.user_config import (
    StartupAccessibilityRecommendation,
    TerminalAccessibilityFallback,
    UserConfigSaveError,
)
from cyoa.ui.app import BufferedNotification, CYOAApp
from cyoa.ui.components import (
    AccessibleSummaryScreen,
    BranchScreen,
    CharacterSheetScreen,
    CommandPaletteScreen,
    EndingsDiscoveredScreen,
    FirstRunSetupScreen,
    InventoryInspectorScreen,
    LoadGameScreen,
    LoreCodexScreen,
    ModelDownloadScreen,
    NotificationHistoryScreen,
    RunArchiveScreen,
    SceneRecapScreen,
    SettingsScreen,
    StartupAccessibilityRecommendationScreen,
    StartupChoiceScreen,
    StatusDisplay,
    ThemeSpinner,
)
from cyoa.ui.keybindings import (
    build_command_palette_entries,
    effective_keybindings,
    search_command_palette,
)
from cyoa.ui.mixins.events import EventsMixin
from cyoa.ui.mixins.navigation import NavigationMixin
from cyoa.ui.mixins.persistence import PersistenceMixin
from cyoa.ui.mixins.rendering import RenderingMixin, _detect_scene_art
from cyoa.ui.mixins.theme import ThemeMixin, _build_surface_style
from cyoa.ui.mixins.typewriter import TypewriterMixin
from cyoa.ui.presenters import (
    build_accessible_export,
    build_choice_label,
    build_endings_discovered_summary,
    build_help_text,
    build_inventory_empty_summary,
    build_inventory_inspector_entries,
    build_inventory_item_summary,
    build_journal_summary,
    build_lore_codex_summary,
    build_run_archive_summary,
    build_scene_recap,
    build_story_map_summary,
    build_world_state_summary,
    classify_ending_type,
    format_status_message,
    loading_story_text,
)


class DummyTypewriterHost(TypewriterMixin):
    def __init__(self) -> None:
        self.runtime_active = True
        self.reduced_motion = False
        self._is_typing = True
        self._typewriter_active_chunk: list[str] = []
        self._typewriter_queue: asyncio.Queue[str] = asyncio.Queue()
        self._current_story = ""
        self._current_turn_text = ""
        self._current_turn_widget = MagicMock()
        self.typewriter_speed = "instant"
        self.typewriter_enabled = True
        self.segment_updates: list[str] = []
        self.notifications: list[str] = []
        self.scroll_calls: list[bool] = []

    def is_runtime_active(self) -> bool:
        return self.runtime_active

    def _is_at_bottom(self) -> bool:
        return True

    def _scroll_to_bottom(self, animate: bool = True) -> None:
        self.scroll_calls.append(animate)

    def _update_current_story_segment(self, text: str) -> None:
        self.segment_updates.append(text)

    def notify(self, message: str, **_: object) -> None:
        self.notifications.append(message)


class DummyThemeHost(ThemeMixin):
    def __init__(self) -> None:
        self.dark = True
        self.high_contrast_mode = False
        self.reduced_motion = False
        self.theme = "textual-dark"
        self.registered_theme_names: list[str] = []
        self._ui_theme: dict[str, str] = {}
        self.container = MagicMock()
        self.spinner = MagicMock()
        self.action_panel = MagicMock()
        self.status_display = MagicMock()
        self.side_panel = MagicMock()
        self.story_turn = MagicMock()
        self.archived_turn = MagicMock()
        self.player_choice = MagicMock()
        self.choice_card = MagicMock()
        self.locked_choice = MagicMock()

    def register_theme(self, theme: Theme) -> None:
        self.registered_theme_names.append(theme.name)

    def query_one(self, selector: str, *_args: object) -> object:
        if selector == "#main-container":
            return self.container
        if selector == "#action-panel":
            return self.action_panel
        if selector == "#status-display":
            return self.status_display
        if selector == "#loading":
            return self.spinner
        raise AssertionError(selector)

    def query(self, selector: str) -> list[object]:
        mapping = {
            ".side-panel-shell": [self.side_panel],
            ".story-turn.current-turn": [self.story_turn],
            ".story-turn.archived-turn": [self.archived_turn],
            ".player-choice": [self.player_choice],
            "#choices-container .choice-card-available": [self.choice_card],
            "#choices-container .choice-card-locked": [self.locked_choice],
        }
        if selector not in mapping:
            raise AssertionError(selector)
        return mapping[selector]

    def update(self, *_args: object, **_kwargs: object) -> None:
        return None


class DummyRenderingHost(RenderingMixin):
    def __init__(self) -> None:
        self.runtime_active = True
        self.reduced_motion = False
        self.screen_reader_mode = False
        self.typewriter_enabled = False
        self._loading_suffix_shown = True
        self._current_story = constants.LOADING_ART
        self._current_turn_text = constants.LOADING_ART
        self._current_turn_widget = MagicMock()
        self._typewriter_queue: asyncio.Queue[str] = asyncio.Queue()
        self.reset_calls: list[str] = []
        self.segment_updates: list[str] = []
        self.scroll_calls: list[bool] = []
        self.loading_widget = MagicMock()

    def is_runtime_active(self) -> bool:
        return self.runtime_active

    def query_one(self, selector: str, *_args: object) -> object:
        if selector == "#loading":
            return self.loading_widget
        raise AssertionError(selector)

    def _reset_story_segments(self, initial_text: str) -> None:
        self.reset_calls.append(initial_text)

    def _update_current_story_segment(self, text: str) -> None:
        self.segment_updates.append(text)

    def _is_at_bottom(self) -> bool:
        return True

    def _scroll_to_bottom(self, animate: bool = True) -> None:
        self.scroll_calls.append(animate)

    def action_skip_typewriter(self) -> None:
        self._current_story = "old prefix\n\n---\n\nstale"

    def apply_ui_theme(self) -> None:
        return None


class DummyChoiceContainer:
    def __init__(self) -> None:
        self.mounted: list[object] = []
        self.removed = False
        self.classes: set[str] = set()

    def mount(self, widget: object) -> None:
        self.mounted.append(widget)

    def remove_children(self) -> None:
        self.removed = True
        self.mounted.clear()

    def query(self, widget_type: type[object]) -> list[object]:
        if getattr(widget_type, "__name__", "") == "Button":
            return list(self.mounted)
        return [widget for widget in self.mounted if isinstance(widget, widget_type)]

    def add_class(self, name: str) -> None:
        self.classes.add(name)

    def remove_class(self, name: str) -> None:
        self.classes.discard(name)


class DummyChoiceButton:
    def __init__(self, button_id: str) -> None:
        self.id = button_id
        self.disabled = False
        self.variant = "primary"
        self.removed = False

    def remove(self) -> None:
        self.removed = True


class DummyRenderingChoiceHost(RenderingMixin):
    def __init__(self) -> None:
        self.turn_count = 7
        self.reduced_motion = False
        self.screen_reader_mode = False
        self._loading_suffix_shown = False
        self.engine = SimpleNamespace(
            state=SimpleNamespace(
                inventory=["torch"],
                player_stats={"health": 100, "gold": 0, "reputation": 0},
                story_flags={},
            )
        )
        self.pending_refresh_callbacks: list[object] = []

    def call_after_refresh(self, callback: object) -> None:
        self.pending_refresh_callbacks.append(callback)

    def apply_ui_theme(self) -> None:
        return None


class DummyPersistenceHost:
    def __init__(self) -> None:
        self.engine = SimpleNamespace(
            state=SimpleNamespace(
                story_title="",
                current_node=SimpleNamespace(title=""),
            )
        )
        self._story_segments = [{"kind": "story_turn", "text": "segment"}]
        self._current_story = "flattened story"
        self._is_typing = True
        self._typewriter_active_chunk = ["a"]
        self._typewriter_queue: asyncio.Queue[str] = asyncio.Queue()

    def apply_ui_theme(self) -> None:
        return None


class DummyPersistenceApp:
    def __init__(self) -> None:
        self.workers = SimpleNamespace(cancel_group=MagicMock())


class DummyEventsHost(EventsMixin):
    def __init__(self) -> None:
        self.runtime_active = True
        self.reduced_motion = False
        self._last_stats_snapshot = {"health": 100, "gold": 1, "reputation": 0}
        self.status_display = SimpleNamespace(health=0, gold=0, reputation=0, objectives=[])
        self.loading = MagicMock()
        self.notifications: list[tuple[str, str]] = []
        self.sync_calls = 0

    def is_runtime_active(self) -> bool:
        return self.runtime_active

    def query_one(self, selector: object, *_args: object) -> object:
        if selector == "#loading":
            return self.loading
        return self.status_display

    def _sync_runtime_status(self) -> None:
        self.sync_calls += 1

    def queue_notification(
        self,
        message: str,
        *,
        severity: str = "information",
        timeout: float = 3,
        batch: bool = True,
    ) -> None:
        self.notifications.append((message, severity))


class DummyTypewriterSettingsHost(DummyTypewriterHost):
    def __init__(self) -> None:
        super().__init__()
        self.saved_config: dict[str, object] = {}


class DummyAppTimer:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class StatusDisplayHarness(App[None]):
    def compose(self) -> ComposeResult:
        yield StatusDisplay()


class SpinnerHarness(App[None]):
    def compose(self) -> ComposeResult:
        yield ThemeSpinner(["[a]", "[b]"], id="spinner")


class BranchScreenHarness(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.screen_ref = BranchScreen(
            scenes=[
                {
                    "narrative": "A very long scene " * 20,
                    "available_choices": ["A"],
                    "inventory": ["Torch"],
                }
            ],
            choices=["Take torch"],
        )

    def compose(self) -> ComposeResult:
        yield Container()

    async def on_mount(self) -> None:
        self.push_screen(self.screen_ref)


class LoadScreenHarness(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.screen_ref = LoadGameScreen(["test_adventure_turn2.json"])

    def compose(self) -> ComposeResult:
        yield Container()

    async def on_mount(self) -> None:
        self.push_screen(self.screen_ref)


class FirstRunScreenHarness(App[None]):
    def __init__(
        self,
        *,
        general_notes: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        self.screen_ref = FirstRunSetupScreen(
            general_notes=general_notes,
        )

    def compose(self) -> ComposeResult:
        yield Container()

    async def on_mount(self) -> None:
        self.push_screen(self.screen_ref)


class StartupAccessibilityRecommendationHarness(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.screen_ref = StartupAccessibilityRecommendationScreen(
            StartupAccessibilityRecommendation(
                key="narrow_terminal_screen_reader",
                accessibility_preset="screen_reader_friendly",
                title="Screen Reader Friendly Startup Recommended",
                message="This terminal is tight enough that decorative output can slow reading.",
                reasons=("Current terminal size: 80x24.",),
                rescue_mode_active=True,
            )
        )

    def compose(self) -> ComposeResult:
        yield Container()

    async def on_mount(self) -> None:
        self.push_screen(self.screen_ref)


class SettingsScreenHarness(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.screen_ref = SettingsScreen(
            provider="mock",
            model_path="",
            theme="dark_dungeon",
            dark=True,
            high_contrast=False,
            reduced_motion=False,
            screen_reader_mode=False,
            cognitive_load_reduction_mode=False,
            text_scale="standard",
            line_width="standard",
            line_spacing="standard",
            notification_verbosity="standard",
            scene_recap_verbosity="standard",
            runtime_metadata_verbosity="standard",
            locked_choice_verbosity="standard",
            keybindings={"show_settings": "f2"},
            typewriter=True,
            typewriter_speed="normal",
            diagnostics_enabled=False,
            available_themes=["dark_dungeon", "space_explorer"],
        )

    def compose(self) -> ComposeResult:
        yield Container()

    async def on_mount(self) -> None:
        self.push_screen(self.screen_ref)


def test_typewriter_batch_catchup_consumes_active_chunk_and_queue():
    host = DummyTypewriterHost()
    host._typewriter_active_chunk = list("abc")
    for _ in range(constants.TYPEWRITER_CATCHUP_THRESHOLD + 1):
        host._typewriter_queue.put_nowait("x")

    host._handle_typewriter_batch()

    assert host._typewriter_active_chunk == []
    assert host._typewriter_queue.empty()
    assert host._current_story.startswith("abc")
    assert host._current_turn_text == host._current_story
    assert host.segment_updates


@pytest.fixture(autouse=True)
def _clear_terminal_fallback_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)


def _render_text(widget: Label | ThemeSpinner) -> str:
    return str(widget.render())


def test_skip_typewriter_flushes_pending_text_and_updates_widget():
    host = DummyTypewriterHost()
    host._typewriter_active_chunk = list("hi")
    host._typewriter_queue.put_nowait(" there")

    host.action_skip_typewriter()

    assert host._current_story == "hi there"
    assert host._current_turn_text == "hi there"
    assert host._is_typing is False
    host._current_turn_widget.update.assert_called_once_with("hi there")
    assert host.scroll_calls == [True]


def test_should_stop_typewriter_clears_state_when_runtime_is_inactive():
    host = DummyTypewriterHost()
    host.runtime_active = False
    host._typewriter_active_chunk = list("late")

    assert host._should_stop_typewriter() is True
    assert host._is_typing is False
    assert host._typewriter_active_chunk == []


def test_theme_toggle_dark_and_apply_custom_accent(monkeypatch: pytest.MonkeyPatch):
    host = DummyThemeHost()
    config: dict[str, object] = {}

    monkeypatch.setattr("cyoa.ui.mixins.theme.utils.load_config", lambda: config)
    monkeypatch.setattr(
        "cyoa.ui.mixins.theme.utils.save_config", lambda payload: config.update(payload)
    )

    host.action_toggle_dark()
    host._apply_custom_accent("#123456")

    assert host.dark is False
    assert config["dark"] is False
    assert "cyoa-custom" in host.registered_theme_names
    assert host.theme == "cyoa-custom"


def test_stream_narrative_replaces_loading_art_without_typewriter():
    host = DummyRenderingHost()

    host._stream_narrative("Hello")

    host.loading_widget.add_class.assert_called_once_with("hidden")
    assert host.reset_calls == [""]
    assert host._current_story == "Hello"
    assert host._current_turn_text == "Hello"
    host._current_turn_widget.update.assert_called_once_with("Hello")
    assert host.scroll_calls == [False]


def test_stream_narrative_honors_reduced_motion_even_when_typewriter_is_enabled():
    host = DummyRenderingHost()
    host.typewriter_enabled = True
    host.reduced_motion = True

    host._stream_narrative("Hello")

    assert host._current_story == "Hello"
    assert host._current_turn_text == "Hello"
    assert host._typewriter_queue.empty() is True


def test_presenters_return_plain_text_variants_for_screen_reader_mode():
    assert loading_story_text(screen_reader_mode=True) == "Loading story..."
    assert (
        format_status_message("⚡ Weaving possible futures...", screen_reader_mode=True)
        == "Weaving possible futures..."
    )
    assert (
        format_status_message(
            "⚡ Weaving possible futures...",
            screen_reader_mode=False,
            simplified_mode=True,
        )
        == "Weaving possible futures..."
    )
    assert build_choice_label(
        0,
        "Open the gate",
        "🔒 Missing item: key | Need reputation 3+ (current: 1)",
        screen_reader_mode=True,
    ) == ("1. Open the gate\nUnavailable:\n- Missing item: key\n- Need reputation 3+ (current: 1)")
    assert (
        build_choice_label(
            0,
            "Open the gate",
            "🔒 Missing item: key | Need reputation 3+ (current: 1)",
            screen_reader_mode=True,
            verbosity="minimal",
        )
        == "1. Open the gate\nUnavailable"
    )
    assert (
        build_choice_label(
            0,
            "Leap the gap",
            screen_reader_mode=True,
            hint_lines=["Check: agility vs difficulty 12", "Stakes: You fall into the ravine."],
        )
        == "1. Leap the gap\nCheck: agility vs difficulty 12\nStakes: You fall into the ravine."
    )


def test_build_scene_recap_includes_visible_choices_progress_and_recent_changes() -> None:
    node = StoryNode(
        narrative="The vault door trembles but does not open.",
        choices=[
            Choice(
                text="Open the sigil door",
                requirements=ChoiceRequirement(flags=["sigil_unlocked"]),
            ),
            Choice(
                text="Force the vault seal",
                check=ChoiceCheck(
                    stat="reputation",
                    difficulty=12,
                    stakes="The ward lashes back.",
                ),
            ),
        ],
        items_gained=["Ancient Coin"],
        stat_updates={"health": -10, "gold": 5},
        objectives_updated=[{"id": "escape", "text": "Escape the vault", "status": "active"}],
        faction_updates={"guild": 1},
        story_flags_set=["vault_seen"],
    )

    recap = build_scene_recap(
        narrative=node.narrative,
        choices=node.choices,
        inventory=["Torch", "Ancient Coin"],
        player_stats={"health": 90, "gold": 5, "reputation": 0},
        objectives=[{"id": "escape", "text": "Escape the vault", "status": "active"}],
        companions=[
            {
                "name": "Mira",
                "status": "active",
                "affinity": 3,
                "effect": "Warns you before wards trigger.",
            }
        ],
        screen_reader_mode=False,
        turn_count=2,
        scene_recap_verbosity="standard",
        locked_choice_verbosity="standard",
        story_title="Vault Run",
        story_flags=[],
        items_gained=node.items_gained,
        items_lost=node.items_lost,
        stat_updates=node.stat_updates,
        objectives_updated=node.objectives_updated,
        faction_updates=node.faction_updates,
        npc_affinity_updates=node.npc_affinity_updates,
        story_flags_set=node.story_flags_set,
        story_flags_cleared=node.story_flags_cleared,
        companions_updated=[
            {
                "name": "Mira",
                "status": "active",
                "affinity": 3,
                "effect": "Warns you before wards trigger.",
            }
        ],
    )

    assert "Vault Run | Turn 2" in recap
    assert "## Scene" in recap
    assert "1. Open the sigil door (Unavailable: Missing event: sigil_unlocked)" in recap
    assert "2. Force the vault seal (Check: reputation vs difficulty 12)" in recap
    assert "## Objectives" in recap
    assert "- Escape the vault" in recap
    assert "- Stats: Health 90 | Gold 5 | Reputation 0" in recap
    assert "- Inventory: Torch, Ancient Coin" in recap
    assert "- Active companions: Mira (Warns you before wards trigger.)" in recap
    assert "- Items gained: Ancient Coin" in recap
    assert "- Stats changed: Health -10; Gold +5" in recap
    assert "- Faction changes: guild +1" in recap
    assert "- Flags set: vault_seen" in recap


def test_build_scene_recap_is_more_explicit_in_screen_reader_mode() -> None:
    recap = build_scene_recap(
        narrative="A narrow bridge crosses the chasm.",
        choices=[Choice(text="Cross carefully")],
        inventory=[],
        player_stats={"health": 100, "gold": 0, "reputation": 2},
        objectives=[],
        companions=[],
        screen_reader_mode=True,
        turn_count=4,
        scene_recap_verbosity="detailed",
        locked_choice_verbosity="detailed",
        story_title="Bridge Watch",
        last_choice_text="Light the beacon",
        last_resolved_choice_check=ResolvedChoiceCheck(
            stat="reputation",
            stat_value=2,
            difficulty=12,
            roll=9,
            total=11,
            success=False,
            stakes="The beacon draws hostile eyes.",
        ),
        story_flags=[],
    )

    assert "Bridge Watch | Turn 4" in recap
    assert "Last choice: Light the beacon" in recap
    assert "Last check: reputation failed (9 + 2 = 11 vs 12)" in recap
    assert "Stakes: The beacon draws hostile eyes." in recap
    assert "- Health: 100" in recap
    assert "- Gold: 0" in recap
    assert "- Reputation: 2" in recap
    assert "- Inventory: Empty" in recap


def test_build_world_state_summary_groups_objectives_and_relationships() -> None:
    summary = build_world_state_summary(
        story_title="Vault Run",
        turn_count=4,
        player_stats={"health": 82, "gold": 7, "reputation": 3},
        inventory=["Torch", "Silver Key"],
        objectives=[
            {"id": "escape", "text": "Escape the vault", "status": "active"},
            {"id": "seal", "text": "Seal the breach", "status": "completed"},
            {"id": "warn", "text": "Warn the guild", "status": "failed"},
        ],
        companions=[
            {
                "name": "Steward Hale",
                "status": "active",
                "affinity": 2,
                "effect": "Can negotiate guild rites.",
                "summary": "The archive steward who owes you a favor.",
            }
        ],
        faction_reputation={"Guild": 2},
        npc_affinity={"Steward Hale": 1},
        story_flags={"vault_seen", "guild_trusted"},
        last_choice_text="Open the lower gate",
        last_resolved_choice_check=ResolvedChoiceCheck(
            stat="reputation",
            stat_value=3,
            difficulty=10,
            roll=8,
            total=11,
            success=True,
            stakes="The ward snaps shut.",
        ),
        current_scene_id="scene-4",
    )

    assert "## Overview" in summary
    assert "- Adventure: Vault Run" in summary
    assert "- Turn: 4" in summary
    assert "- Scene ID: scene-4" in summary
    assert "- Last choice: Open the lower gate" in summary
    assert "- Last check: reputation passed (8 + 3 = 11 vs 10)" in summary
    assert "- Stakes: The ward snaps shut." in summary
    assert "## Inventory" in summary
    assert "- Torch" in summary
    assert "- Silver Key" in summary
    assert "### Active" in summary
    assert "- Escape the vault" in summary
    assert "### Completed" in summary
    assert "- Seal the breach" in summary
    assert "### Failed" in summary
    assert "- Warn the guild" in summary
    assert "## Faction Reputation" in summary
    assert "- Guild: 2" in summary
    assert "## NPC Affinity" in summary
    assert "- Steward Hale: 1" in summary
    assert "## Companions" in summary
    assert "### Active" in summary
    assert "- Steward Hale (Affinity 2): Can negotiate guild rites." in summary
    assert "## Story Flags" in summary
    assert "- guild_trusted" in summary
    assert "- vault_seen" in summary


def test_build_lore_codex_summary_groups_entries_by_category() -> None:
    summary = build_lore_codex_summary(
        story_title="Vault Run",
        turn_count=4,
        lore_entries=[
            LoreEntry(
                category="npc",
                name="Steward Hale",
                summary="The gatekeeper of the lower archive.",
                discovered_turn=2,
            ),
            LoreEntry(
                category="location",
                name="Moonwell Vault",
                summary="A sealed chamber below the guild hall.",
                discovered_turn=1,
            ),
            LoreEntry(
                category="item",
                name="Silver Key",
                summary="A key etched with the guild crest.",
                discovered_turn=3,
            ),
        ],
    )

    assert "## Overview" in summary
    assert "- Adventure: Vault Run" in summary
    assert "- Entries discovered: 3" in summary
    assert "## NPCs" in summary
    assert "- Steward Hale (Turn 2): The gatekeeper of the lower archive." in summary
    assert "## Locations" in summary
    assert "- Moonwell Vault (Turn 1): A sealed chamber below the guild hall." in summary
    assert "## Factions" in summary
    assert "- None discovered" in summary
    assert "## Items" in summary
    assert "- Silver Key (Turn 3): A key etched with the guild crest." in summary


def test_build_inventory_inspector_entries_and_item_summary_surface_lore_and_choice_hooks() -> None:
    entries = build_inventory_inspector_entries(
        inventory=["Torch", "Silver Key"],
        lore_entries=[
            LoreEntry(
                category="item",
                name="Silver Key",
                summary="A key etched with the guild crest.",
                discovered_turn=3,
            )
        ],
        choices=[
            Choice(
                text="Open the warded archive",
                requirements=ChoiceRequirement(items=["Silver Key"]),
            ),
            Choice(text="Wait"),
        ],
        items_gained=["Silver Key"],
    )

    assert [entry["name"] for entry in entries] == ["Torch", "Silver Key"]
    assert entries[0]["has_lore"] is False
    assert entries[1]["has_lore"] is True
    assert entries[1]["related_choices"] == ["Open the warded archive"]
    assert entries[1]["recently_gained"] is True

    summary = build_inventory_item_summary(
        story_title="Vault Run",
        turn_count=4,
        item_name="Silver Key",
        item_summary=entries[1]["summary"],
        discovered_turn=entries[1]["discovered_turn"],
        related_choices=entries[1]["related_choices"],
        recently_gained=entries[1]["recently_gained"],
        has_lore=entries[1]["has_lore"],
    )

    assert "- Name: Silver Key" in summary
    assert "- Lore discovered: Yes" in summary
    assert "- First recorded: Turn 3" in summary
    assert "- Status: Newly acquired this turn" in summary
    assert "## Hidden Lore" in summary
    assert "A key etched with the guild crest." in summary
    assert "## Current Uses" in summary
    assert "- Open the warded archive" in summary


def test_build_inventory_empty_summary_reports_no_items() -> None:
    summary = build_inventory_empty_summary(story_title="Vault Run", turn_count=4)

    assert "- Items carried: 0" in summary
    assert "No items are currently in your inventory." in summary


def test_build_accessible_export_uses_plain_text_reading_order() -> None:
    transcript = build_accessible_export(
        story_title="Vault Run",
        turn_count=3,
        saved_at="2026-04-27T09:30:00Z",
        story_segments=[
            {"kind": "story_turn", "text": "The vault door trembles but does not open."},
            {"kind": "player_choice", "text": "**You chose:** Wait and listen"},
            {"kind": "branch_marker", "text": "**[Time fractures... you return to Turn 2]**"},
            {"kind": "story_turn", "text": "A hidden latch clicks behind the mural."},
        ],
        current_story_text=None,
        directives=["Avoid combat", "Stay quiet"],
        inventory=["Torch", "Ancient Coin"],
        player_stats={"health": 90, "gold": 5, "reputation": 1},
        objectives=[{"id": "escape", "text": "Escape the vault", "status": "active"}],
        last_choice_text="Force the vault seal",
        last_resolved_choice_check=ResolvedChoiceCheck(
            stat="reputation",
            stat_value=1,
            difficulty=12,
            roll=12,
            total=13,
            success=True,
            stakes="The ward detonates.",
        ),
    )

    assert "Title: Vault Run" in transcript
    assert "Turn Count: 3" in transcript
    assert "Active Directives:" in transcript
    assert "Transcript:" in transcript
    assert "Scene:\nThe vault door trembles but does not open." in transcript
    assert "Choice: Wait and listen" in transcript
    assert "Branch: Time fractures... you return to Turn 2" in transcript
    assert "Current Progress:" in transcript
    assert "- Inventory: Torch, Ancient Coin" in transcript
    assert "- Objectives: Escape the vault" in transcript
    assert "- Last choice: Force the vault seal" in transcript
    assert "- Last check: reputation passed (12 + 1 = 13 vs 12)" in transcript
    assert "---" not in transcript


def test_build_scene_recap_minimal_and_export_detailed_respect_verbosity() -> None:
    recap = build_scene_recap(
        narrative="The atrium hums with trapped magic.",
        choices=[
            Choice(
                text="Break the seal",
                requirements=ChoiceRequirement(items=["Seal Key"]),
            )
        ],
        inventory=["Torch"],
        player_stats={"health": 80, "gold": 3, "reputation": 1},
        objectives=[{"id": "seal", "text": "Find the key", "status": "active"}],
        companions=[],
        screen_reader_mode=False,
        turn_count=5,
        scene_recap_verbosity="minimal",
        locked_choice_verbosity="minimal",
        story_title="Atrium Run",
        story_flags=[],
        items_gained=["Rune"],
    )

    transcript = build_accessible_export(
        story_title="Atrium Run",
        turn_count=5,
        saved_at="2026-04-28T09:30:00Z",
        story_segments=[{"kind": "story_turn", "text": "The atrium hums with trapped magic."}],
        current_story_text=None,
        directives=["Move quietly"],
        inventory=["Torch"],
        player_stats={"health": 80, "gold": 3, "reputation": 1},
        objectives=[{"id": "seal", "text": "Find the key", "status": "active"}],
        last_choice_text="Break the seal",
        last_resolved_choice_check=ResolvedChoiceCheck(
            stat="reputation",
            stat_value=1,
            difficulty=8,
            roll=6,
            total=7,
            success=False,
            stakes="The atrium alarms flare.",
        ),
        verbosity="detailed",
    )

    assert "## Recent Changes" not in recap
    assert "1. Break the seal (Unavailable)" in recap
    assert "- Inventory: 1 item(s)" in recap
    assert "Saved At: 2026-04-28T09:30:00Z" in transcript
    assert "- Last choice: Break the seal" in transcript
    assert "- Last check: reputation failed (6 + 1 = 7 vs 8)" in transcript
    assert "Objective Details:" in transcript
    assert "- Find the key" in transcript


def test_build_journal_and_story_map_summaries_are_linear_and_ordered() -> None:
    journal_summary = build_journal_summary(
        [
            {"label": "Turn 1: Wake up", "scene_index": 0, "entry_kind": "choice"},
            {
                "label": "Timeline fracture → resumed from Turn 1",
                "scene_index": 1,
                "entry_kind": "branch",
            },
        ],
        screen_reader_mode=True,
    )
    story_map_summary = build_story_map_summary(
        {
            "root_id": "scene-1",
            "nodes": {
                "scene-1": {
                    "narrative": "You wake in a cell.",
                    "available_choices": ["Go North"],
                },
                "scene-2": {
                    "narrative": "A corridor opens into moonlight.",
                    "available_choices": [],
                },
            },
            "edges": {"scene-1": [{"target_id": "scene-2", "choice": "Go North"}], "scene-2": []},
        },
        current_scene_id="scene-2",
        timeline_metadata=[
            {"kind": "branch_restore", "target_scene_id": "scene-1", "restored_turn": 1}
        ],
        screen_reader_mode=True,
    )

    assert "## Timeline" in journal_summary
    assert "- Turn 1: Turn 1: Wake up" in journal_summary
    assert "## Branch Restores" in journal_summary
    assert "Timeline fracture" in journal_summary
    assert "## Structure" in story_map_summary
    assert "Turn 1 | Depth 0 | Restored from Turn 1" in story_map_summary
    assert "Choice: Go North" in story_map_summary
    assert "Turn 2 | Depth 1 | Current | Ending" in story_map_summary


def test_accessible_summary_screen_switches_and_closes() -> None:
    screen = AccessibleSummaryScreen(
        "Journal Summary",
        "# Journal Summary\n\n## Timeline\n- Turn 1: Wake up",
        active="journal",
    )
    screen.dismiss = MagicMock()

    screen.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-accessible-summary-map"))
    )
    screen.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-accessible-summary-close"))
    )
    screen.action_show_journal()
    screen.action_show_story_map()
    screen.action_close()

    assert screen.dismiss.call_args_list == [
        call("story_map"),
        call(None),
        call("journal"),
        call("story_map"),
        call(None),
    ]


def test_lore_codex_screen_closes() -> None:
    screen = LoreCodexScreen("# Lore Codex\n\n## NPCs\n- Mira: Scout")
    screen.dismiss = MagicMock()

    screen.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-lore-codex-close")))
    screen.action_close()

    assert screen.dismiss.call_args_list == [call(None), call(None)]


def test_endings_discovered_screen_closes() -> None:
    screen = EndingsDiscoveredScreen("## Endings\n- Escape")
    screen.dismiss = MagicMock()

    screen.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-endings-discovered-close"))
    )
    screen.action_close()

    assert screen.dismiss.call_args_list == [call(None), call(None)]


def test_run_archive_screen_closes() -> None:
    screen = RunArchiveScreen("## Run Archive\n- Test Adventure")
    screen.dismiss = MagicMock()

    screen.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-run-archive-close")))
    screen.action_close()

    assert screen.dismiss.call_args_list == [call(None), call(None)]


def test_classify_ending_type_prefers_health_and_escape_keywords() -> None:
    assert classify_ending_type("You collapse as the cavern seals.", health=0) == "death"
    assert classify_ending_type("You opened the gate and escaped into dawn.") == "escape"


def test_list_manual_save_files_excludes_internal_archive_and_autosave(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(constants, "SAVES_DIR", str(tmp_path))
    (tmp_path / "autosave_latest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "run_archive.json").write_text("[]", encoding="utf-8")
    (tmp_path / "chapter_one.json").write_text("{}", encoding="utf-8")

    assert PersistenceMixin._list_manual_save_files() == ["chapter_one.json"]


def test_archive_presenters_surface_endings_flags_and_divergence_points() -> None:
    archive_entries = [
        {
            "story_title": "Test Adventure",
            "completed_at": "2026-04-29T12:00:00Z",
            "turn_count": 3,
            "ending_type": "escape",
            "ending_label": "Escape",
            "ending_narrative": "You opened the door and escaped!",
            "last_choice_text": "Open Door",
            "last_resolved_choice_check": {
                "stat": "reputation",
                "stat_value": 2,
                "difficulty": 9,
                "roll": 8,
                "total": 10,
                "success": True,
                "stakes": "The gate slams shut.",
            },
            "story_flags": ["saw_signal", "trusted_mira"],
            "divergence_points": [2],
            "inventory": ["Broken Sword", "Health Potion"],
            "objective_status_counts": {"active": 1, "completed": 2, "failed": 0},
        }
    ]

    endings_summary = build_endings_discovered_summary(archive_entries)
    archive_summary = build_run_archive_summary(archive_entries)

    assert "Escape" in endings_summary
    assert "Completed runs: 1" in endings_summary
    assert "Latest divergence points: Turn 2" in endings_summary
    assert "Final choice: Open Door" in archive_summary
    assert "Last check: reputation passed (8 + 2 = 10 vs 9)" in archive_summary
    assert "Flags: saw_signal, trusted_mira" in archive_summary


def test_app_effective_keybindings_merge_defaults_and_overrides() -> None:
    merged = effective_keybindings({"show_settings": "f2", "repeat_latest_status": "f3"})

    assert merged["show_settings"] == "f2"
    assert merged["repeat_latest_status"] == "f3"
    assert merged["toggle_journal"] == "j"


def test_command_palette_entries_reflect_saved_bindings() -> None:
    entries = build_command_palette_entries(
        {"show_settings": "f2", "show_help": "f1", "show_command_palette": "f4"}
    )
    entries_by_id = {entry.id: entry for entry in entries}

    assert "show_command_palette" not in entries_by_id
    assert entries_by_id["show_settings"].keybinding == "F2"
    assert entries_by_id["show_help"].keybinding == "F1"


def test_command_palette_search_matches_labels_actions_and_fuzzy_queries() -> None:
    entries = build_command_palette_entries({"show_settings": "f2"})

    settings_results = search_command_palette(entries, "settings")
    assert settings_results
    assert settings_results[0].id == "show_settings"

    help_results = search_command_palette(entries, "open help")
    assert help_results
    assert help_results[0].id == "show_help"

    fuzzy_results = search_command_palette(entries, "stngs")
    assert fuzzy_results
    assert fuzzy_results[0].id == "show_settings"


def test_help_text_covers_branching_exports_and_review_panels() -> None:
    help_text = build_help_text(
        screen_reader_mode=False,
        current_bindings={"show_help": "f1", "show_settings": "f2"},
    )
    screen_reader_help = build_help_text(
        screen_reader_mode=True,
        current_bindings={"show_help": "f1", "show_settings": "f2"},
    )

    assert "Adventure Flow" in help_text
    assert "Branch lets you revisit an earlier scene" in help_text
    assert "Export writes markdown, accessible markdown, and JSON copies" in help_text
    assert "Journal Summary and Story Map Summary" in help_text
    assert "Play Loop" in screen_reader_help
    assert "Repeat Status and notification history" in screen_reader_help
    assert "Key bindings can be customized in Settings." in screen_reader_help


def test_sync_narrative_replaces_finalized_streamed_turn():
    host = DummyRenderingHost()
    host._loading_suffix_shown = False

    host._sync_narrative("Fresh ending")

    assert host._current_story == "old prefix\n\n---\n\nFresh ending"
    assert host._current_turn_text == "Fresh ending"
    assert host.segment_updates[-1] == "Fresh ending"


def test_detect_scene_art_matches_known_keywords():
    assert _detect_scene_art("A dragon looms over the cavern.") is not None
    assert _detect_scene_art("A plain hallway with no obvious cues.") is None


def test_persistence_helpers_resolve_fallback_title_and_snapshot_story():
    host = DummyPersistenceHost()
    persistence = PersistenceMixin()

    resolved = persistence._resolve_save_title(host)
    snapshot = persistence._snapshot_story_segments(host)

    assert resolved == "Untitled Adventure"
    assert host.engine.state.story_title == "Untitled Adventure"
    assert snapshot == [{"kind": "story_turn", "text": "flattened story"}]


def test_persistence_clear_restore_runtime_state_drains_queue():
    host = DummyPersistenceHost()
    app = DummyPersistenceApp()
    host._typewriter_queue.put_nowait("pending")

    PersistenceMixin._clear_restore_runtime_state(host, app)

    app.workers.cancel_group.assert_called_once_with(app, "speculation")
    assert host._is_typing is False
    assert host._typewriter_active_chunk == []
    assert host._typewriter_queue.empty()


def test_navigation_helpers_collect_branch_targets_and_trim_story_segments():
    timeline_metadata = [
        {"kind": "branch_restore", "target_scene_id": "scene-2", "restored_turn": 3},
        {"kind": "branch_restore", "target_scene_id": "scene-2", "restored_turn": 1},
        {"kind": "other"},
    ]
    branch_targets = NavigationMixin._collect_branch_targets(timeline_metadata)
    label = NavigationMixin._format_story_map_label(
        scene_id="scene-2",
        narrative="A restored scene",
        mood="heroic",
        current_scene_id="scene-2",
        branch_targets=branch_targets,
        turn=3,
        depth=1,
        is_ending=False,
    )
    host = SimpleNamespace(
        _story_segments=[
            {"kind": "player_choice", "text": "Go north"},
            {"kind": "story_turn", "text": "Result"},
        ]
    )

    NavigationMixin._trim_story_segments_for_undo(host)

    assert branch_targets == {"scene-2": [3, 1]}
    assert "⟲ T1, 3" in label
    assert "T3·D1" in label
    assert host._story_segments == []


def test_events_stats_and_world_state_handlers_emit_notifications():
    host = DummyEventsHost()

    host._handle_stats_updated({"health": 85, "gold": 4, "reputation": 2})
    host._handle_world_state_updated(
        {
            "objectives": [
                {"text": "Escape", "status": "active"},
                {"text": "Ignore", "status": "completed"},
            ]
        }
    )

    assert host.status_display.health == 85
    assert host.status_display.gold == 4
    assert host.status_display.reputation == 2
    assert host.status_display.objectives == ["Escape"]
    assert host.notifications == [("-15 HP | +3 Gold | +2 Rep", "warning")]


def test_events_phase_handler_updates_runtime_status_and_spinner():
    host = DummyEventsHost()

    host._handle_engine_phase_changed(
        EngineTransition(
            from_phase=EnginePhase.IDLE,
            to_phase=EnginePhase.GENERATING,
            reason="generate_next",
        )
    )
    host._handle_engine_phase_changed(
        EngineTransition(
            from_phase=EnginePhase.GENERATING,
            to_phase=EnginePhase.READY,
            reason="generation_completed",
        )
    )

    assert host.sync_calls == 2
    host.loading.remove_class.assert_called_once_with("hidden")
    host.loading.add_class.assert_called_once_with("hidden")


@pytest.mark.asyncio
async def test_branch_screen_mount_populates_scene_list_and_cancel_dismisses() -> None:
    app = BranchScreenHarness()
    preview = app.screen_ref._build_scene_preview(app.screen_ref.scenes[0], 0, "Take torch")

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        list_view = app.screen.query_one("#branch-list", ListView)
        assert len(list_view.children) == 1
        await pilot.click("#cancel-branch")
        await pilot.pause(0.1)
        assert app.screen is not app.screen_ref

    assert "Turn 1" in preview
    assert "1 future path(s)" in preview
    assert "1 item(s) carried" in preview


@pytest.mark.asyncio
async def test_load_game_screen_mount_formats_save_names_and_selection_dismisses() -> None:
    app = LoadScreenHarness()

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        list_view = app.screen.query_one("#load-list", ListView)
        assert len(list_view.children) == 1
        item = list_view.children[0]
        assert _render_text(item.query_one(Label)) == "test adventure turn2"
        await pilot.click("#btn-load-cancel")
        await pilot.pause(0.1)
        assert app.screen is not app.screen_ref


@pytest.mark.asyncio
async def test_first_run_screen_exposes_mock_and_download_actions() -> None:
    app = FirstRunScreenHarness()

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        default_preset = app.screen.query_one("#btn-first-run-preset-default", Button)
        high_contrast_preset = app.screen.query_one("#btn-first-run-preset-high_contrast", Button)
        reduced_motion_preset = app.screen.query_one("#btn-first-run-preset-reduced_motion", Button)
        screen_reader_preset = app.screen.query_one(
            "#btn-first-run-preset-screen_reader_friendly", Button
        )
        mock_button = app.screen.query_one("#btn-first-run-mock", Button)
        download_button = app.screen.query_one("#btn-first-run-download", Button)
        assert default_preset.variant == "primary"
        assert high_contrast_preset.disabled is False
        assert reduced_motion_preset.disabled is False
        assert screen_reader_preset.disabled is False
        assert mock_button.disabled is False
        assert download_button.disabled is False


@pytest.mark.asyncio
async def test_first_run_screen_renders_general_notes() -> None:
    app = FirstRunScreenHarness(general_notes=("Resize the terminal if panels feel cramped.",))

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        labels = [label.render().plain for label in app.screen.query(Label)]
        assert any("Resize the terminal" in text for text in labels)
        assert any("No accessibility overrides enabled." in text for text in labels)


@pytest.mark.asyncio
async def test_first_run_screen_updates_selected_accessibility_preset_summary() -> None:
    app = FirstRunScreenHarness()

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await pilot.click("#btn-first-run-preset-screen_reader_friendly")
        await pilot.pause(0.1)

        selected = app.screen.query_one("#btn-first-run-preset-screen_reader_friendly", Button)
        summary = _render_text(app.screen.query_one("#first-run-preset-summary", Label))

        assert selected.variant == "primary"
        assert "Reduced Motion" in summary
        assert "Screen Reader Friendly" in summary


@pytest.mark.asyncio
async def test_startup_accessibility_recommendation_screen_exposes_actions() -> None:
    app = StartupAccessibilityRecommendationHarness()

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        accept = app.screen.query_one("#btn-startup-accessibility-accept", Button)
        dismiss = app.screen.query_one("#btn-startup-accessibility-dismiss", Button)
        later = app.screen.query_one("#btn-startup-accessibility-later", Button)
        labels = [label.render().plain for label in app.screen.query(Label)]

        assert accept.disabled is False
        assert dismiss.disabled is False
        assert later.disabled is False
        assert any("Current terminal size: 80x24." in text for text in labels)
        assert any("rescue mode" in text.lower() for text in labels)


@pytest.mark.asyncio
async def test_status_display_watchers_and_spinner_tick() -> None:
    app = StatusDisplayHarness()
    async with app.run_test() as pilot:
        display = app.query_one(StatusDisplay)
        display.health = 25
        display.gold = 9
        display.reputation = 3
        display.inventory = ["Torch", "Key"]
        display.objectives = ["Escape", "Survive", "Ignore"]
        display.directives = ["No combat", "Stay hidden", "Ignore"]
        display.generation_preset = "fast"
        display.runtime_profile = "balanced-runtime-profile"
        display.provider_label = "provider-with-a-very-long-name"
        display.engine_phase = "ready"
        display.latest_status = "Information: Quiet winds."
        display.screen_reader_mode = True
        display._update_stats_text()
        await pilot.pause(0.1)

        assert display.query_one("#health-bar", ProgressBar).progress == 25
        assert "25%" in _render_text(display.query_one("#health-value", Label))
        assert "Critical" in _render_text(display.query_one("#health-value", Label))
        assert "Gold 9" in _render_text(display.query_one("#stats-text", Label))
        assert (
            _render_text(display.query_one("#runtime-text", Label)) == "Preset fast | Phase ready"
        )
        assert "Inventory: Torch, Key" in _render_text(display.query_one("#inventory-label", Label))
        assert "Objectives: Escape | Survive" in _render_text(
            display.query_one("#objectives-label", Label)
        )
        assert "Directives: No combat | Stay hidden" in _render_text(
            display.query_one("#directives-label", Label)
        )
        assert "Information: Quiet winds." in _render_text(
            display.query_one("#latest-status-label", Label)
        )
        assert display.has_class("health-low")

        display.cognitive_load_reduction_mode = True
        await pilot.pause(0.1)

        assert display.query_one("#runtime-text", Label).has_class("hidden")
        assert display.query_one("#directives-label", Label).has_class("hidden")
        assert "Focus: Escape" in _render_text(display.query_one("#objectives-label", Label))

        display.cognitive_load_reduction_mode = False
        display.runtime_metadata_verbosity = "detailed"
        await pilot.pause(0.1)
        assert "Provider provider-with-a-very-long-name" in _render_text(
            display.query_one("#runtime-text", Label)
        )

    spinner_app = SpinnerHarness()
    async with spinner_app.run_test() as pilot:
        await pilot.pause(0.1)
        spinner = spinner_app.query_one("#spinner", ThemeSpinner)
        first_frame = _render_text(spinner)
        assert first_frame in {"[a]", "[b]"}
        spinner.tick()
        advanced_frame = _render_text(spinner)
        assert advanced_frame in {"[a]", "[b]"}
        spinner.add_class("hidden")
        spinner.tick()
        assert _render_text(spinner) == advanced_frame


def test_branch_and_load_screens_handle_selection_events():
    branch = BranchScreen([{"narrative": "scene"}], ["Choose"])
    branch.dismiss = MagicMock()
    scene_item = branch._build_scene_preview({"narrative": "scene"}, 0, "Choose")
    assert "Turn 1" in scene_item
    branch.on_list_view_selected(SimpleNamespace(item=SimpleNamespace(scene_index=2)))
    branch.dismiss.assert_not_called()
    branch.on_list_view_selected(SimpleNamespace(item=branch.compose))
    branch.dismiss.assert_not_called()

    from cyoa.ui.components import SaveListItem, SceneListItem

    branch.on_list_view_selected(SimpleNamespace(item=SceneListItem(Label("x"), scene_index=2)))
    branch.dismiss.assert_called_once_with(2)

    load = LoadGameScreen(["save_one.json"])
    load.dismiss = MagicMock()
    load.on_list_view_selected(SimpleNamespace(item=SimpleNamespace(save_filename="ignored")))
    load.dismiss.assert_not_called()
    load.on_list_view_selected(
        SimpleNamespace(item=SaveListItem(Label("x"), save_filename="save_one.json"))
    )
    load.dismiss.assert_called_once_with("save_one.json")


def test_confirm_and_help_screens_dismiss_expected_values():
    from cyoa.ui.components import ConfirmScreen

    confirm = ConfirmScreen("Proceed?")
    confirm.dismiss = MagicMock()
    confirm.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-confirm-yes")))
    confirm.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-confirm-no")))
    confirm.action_confirm()
    confirm.action_cancel()

    assert confirm.dismiss.call_args_list == [
        call(True),
        call(False),
        call(True),
        call(False),
    ]


def test_startup_choice_screen_dismisses_expected_values():
    from cyoa.ui.components import HelpScreen

    startup = StartupChoiceScreen("Resume or start over?")
    startup.dismiss = MagicMock()
    startup.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-startup-resume")))
    startup.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-startup-new")))
    startup.action_resume()
    startup.action_new_game()

    assert startup.dismiss.call_args_list == [
        call("resume"),
        call("new"),
        call("resume"),
        call("new"),
    ]

    help_screen = HelpScreen()
    help_screen.dismiss = MagicMock()
    help_screen.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-help-close")))
    help_screen.action_close()
    assert help_screen.dismiss.call_args_list == [call(None), call(None)]

    palette = CommandPaletteScreen(build_command_palette_entries({}))
    palette.dismiss = MagicMock()
    palette.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-command-palette-close"))
    )
    palette.action_close()
    assert palette.dismiss.call_args_list == [call(None), call(None)]

    history_screen = NotificationHistoryScreen(["Information: A path opens."])
    history_screen.dismiss = MagicMock()
    history_screen.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-notification-history-close"))
    )
    history_screen.action_close()
    assert history_screen.dismiss.call_args_list == [call(None), call(None)]

    recap_screen = SceneRecapScreen("## Scene\nA path opens.")
    recap_screen.dismiss = MagicMock()
    recap_screen.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-scene-recap-close"))
    )
    recap_screen.action_close()
    assert recap_screen.dismiss.call_args_list == [call(None), call(None)]

    inventory_screen = InventoryInspectorScreen(
        story_title="Vault Run",
        turn_count=4,
        inventory=["Silver Key"],
        lore_entries=[],
        choices=[],
    )
    inventory_screen.dismiss = MagicMock()
    inventory_screen.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-inventory-inspector-close"))
    )
    inventory_screen.action_close()
    assert inventory_screen.dismiss.call_args_list == [call(None), call(None)]

    character_sheet = CharacterSheetScreen("## Stats\n- Health: 100")
    character_sheet.dismiss = MagicMock()
    character_sheet.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-character-sheet-close"))
    )
    character_sheet.action_close()
    assert character_sheet.dismiss.call_args_list == [call(None), call(None)]


def test_first_run_setup_screen_dismisses_expected_values():
    first_run = FirstRunSetupScreen()
    first_run.dismiss = MagicMock()
    first_run.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-first-run-preset-high_contrast"))
    )
    first_run.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-first-run-mock")))
    first_run.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-first-run-download"))
    )
    first_run.action_select_screen_reader_preset()
    first_run.action_quick_demo()
    first_run.action_download_model()

    assert first_run.dismiss.call_args_list == [
        call({"runtime": "mock", "accessibility_preset": "high_contrast"}),
        call({"runtime": "download", "accessibility_preset": "high_contrast"}),
        call({"runtime": "mock", "accessibility_preset": "screen_reader_friendly"}),
        call({"runtime": "download", "accessibility_preset": "screen_reader_friendly"}),
    ]


def test_startup_accessibility_recommendation_screen_dismisses_expected_values():
    screen = StartupAccessibilityRecommendationScreen(
        StartupAccessibilityRecommendation(
            key="limited_color_high_contrast",
            accessibility_preset="high_contrast",
            title="High Contrast Startup Recommended",
            message="Color support is limited.",
        )
    )
    screen.dismiss = MagicMock()
    screen.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-startup-accessibility-accept"))
    )
    screen.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-startup-accessibility-dismiss"))
    )
    screen.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-startup-accessibility-later"))
    )
    screen.action_accept()
    screen.action_dismiss_recommendation()
    screen.action_later()

    assert screen.dismiss.call_args_list == [
        call("accept"),
        call("dismiss"),
        call("later"),
        call("accept"),
        call("dismiss"),
        call("later"),
    ]


def test_settings_screen_dismisses_saved_payload(tmp_path) -> None:
    model_path = tmp_path / "demo.gguf"
    model_path.write_text("stub", encoding="utf-8")
    settings = SettingsScreen(
        provider="mock",
        model_path="",
        theme="dark_dungeon",
        dark=True,
        high_contrast=False,
        reduced_motion=False,
        screen_reader_mode=False,
        cognitive_load_reduction_mode=False,
        text_scale="standard",
        line_width="standard",
        line_spacing="standard",
        notification_verbosity="standard",
        scene_recap_verbosity="standard",
        runtime_metadata_verbosity="standard",
        locked_choice_verbosity="standard",
        keybindings={},
        typewriter=True,
        typewriter_speed="normal",
        diagnostics_enabled=False,
        available_themes=["dark_dungeon", "space_explorer"],
    )
    settings.dismiss = MagicMock()
    settings._refresh_state = MagicMock()
    settings._set_keybinding_feedback = MagicMock()
    settings._collect_keybinding_values = MagicMock(
        return_value={"show_settings": "f2", "toggle_journal": "f3"}
    )
    field_feedback = MagicMock()
    field_feedback.set_class = MagicMock()
    settings_feedback = MagicMock()
    settings_feedback.set_class = MagicMock()
    settings.query_one = lambda selector, *_args: (
        SimpleNamespace(value=str(model_path))
        if selector == "#settings-model-path"
        else field_feedback
        if selector == "#settings-model-path-feedback"
        else settings_feedback
        if selector == "#settings-feedback"
        else None
    )

    settings.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-settings-provider-llama"))
    )
    settings.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-settings-theme-next"))
    )
    settings.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-settings-contrast-high"))
    )
    settings.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-settings-motion-reduced"))
    )
    settings.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-settings-screen-reader-on"))
    )
    settings.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-settings-cognitive-reduced"))
    )
    settings.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-settings-scale-xlarge"))
    )
    settings.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-settings-width-focused"))
    )
    settings.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-settings-spacing-relaxed"))
    )
    settings.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-settings-notification-verbosity-minimal"))
    )
    settings.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-settings-recap-verbosity-detailed"))
    )
    settings.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-settings-runtime-verbosity-minimal"))
    )
    settings.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-settings-locked-choice-verbosity-detailed"))
    )
    settings.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-settings-typewriter-off"))
    )
    settings.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-settings-speed-fast"))
    )
    settings.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-settings-diagnostics-on"))
    )
    settings.action_save()

    saved_payload = settings.dismiss.call_args_list[-1].args[0]
    assert saved_payload == {
        "provider": "llama_cpp",
        "model_path": str(model_path),
        "theme": "space_explorer",
        "dark": True,
        "high_contrast": True,
        "reduced_motion": True,
        "screen_reader_mode": True,
        "cognitive_load_reduction_mode": True,
        "text_scale": "xlarge",
        "line_width": "focused",
        "line_spacing": "relaxed",
        "notification_verbosity": "minimal",
        "scene_recap_verbosity": "detailed",
        "runtime_metadata_verbosity": "minimal",
        "locked_choice_verbosity": "detailed",
        "keybindings": {"show_settings": "f2", "toggle_journal": "f3"},
        "typewriter": False,
        "typewriter_speed": "fast",
        "diagnostics_enabled": True,
    }


def test_settings_screen_support_actions_dismiss_expected_payloads():
    settings = SettingsScreen(
        provider="mock",
        model_path="",
        theme="dark_dungeon",
        dark=True,
        high_contrast=False,
        reduced_motion=False,
        screen_reader_mode=False,
        cognitive_load_reduction_mode=False,
        text_scale="standard",
        line_width="standard",
        line_spacing="standard",
        notification_verbosity="standard",
        scene_recap_verbosity="standard",
        runtime_metadata_verbosity="standard",
        locked_choice_verbosity="standard",
        keybindings={},
        typewriter=True,
        typewriter_speed="normal",
        diagnostics_enabled=False,
        available_themes=["dark_dungeon"],
    )
    settings.dismiss = MagicMock()
    path_feedback = MagicMock()
    path_feedback.set_class = MagicMock()
    settings_feedback = MagicMock()
    settings_feedback.set_class = MagicMock()
    settings._collect_keybinding_values = MagicMock(return_value={"show_settings": "f2"})
    settings.query_one = lambda selector, *_args: (
        SimpleNamespace(value="")
        if selector == "#settings-model-path"
        else path_feedback
        if selector == "#settings-model-path-feedback"
        else settings_feedback
        if selector == "#settings-feedback"
        else None
    )

    settings.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-settings-test-backend"))
    )
    settings.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-settings-capture-snapshot"))
    )
    settings.on_button_pressed(
        SimpleNamespace(button=SimpleNamespace(id="btn-settings-reveal-saves"))
    )
    settings.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-settings-reset")))

    test_backend_payload = settings.dismiss.call_args_list[0].args[0]
    assert test_backend_payload["action"] == "test_backend"
    assert test_backend_payload["draft_settings"]["provider"] == "mock"
    assert test_backend_payload["draft_settings"]["model_path"] is None
    assert settings.dismiss.call_args_list[1:] == [
        call({"action": "capture_accessibility_snapshot"}),
        call({"action": "reveal_saves"}),
        call({"action": "reset_settings"}),
    ]


def test_settings_screen_blocks_save_when_keybindings_conflict() -> None:
    settings = SettingsScreen(
        provider="mock",
        model_path="",
        theme="dark_dungeon",
        dark=True,
        high_contrast=False,
        reduced_motion=False,
        screen_reader_mode=False,
        cognitive_load_reduction_mode=False,
        text_scale="standard",
        line_width="standard",
        line_spacing="standard",
        notification_verbosity="standard",
        scene_recap_verbosity="standard",
        runtime_metadata_verbosity="standard",
        locked_choice_verbosity="standard",
        keybindings={},
        typewriter=True,
        typewriter_speed="normal",
        diagnostics_enabled=False,
        available_themes=["dark_dungeon"],
    )
    feedback = MagicMock()
    feedback.set_class = MagicMock()
    settings.dismiss = MagicMock()
    settings.query_one = lambda selector, *_args: (
        SimpleNamespace(value="")
        if selector == "#settings-model-path"
        else feedback
        if selector == "#settings-keybindings-feedback"
        else None
    )
    settings._collect_keybinding_values = MagicMock(
        return_value={"show_settings": "f2", "repeat_latest_status": "f2"}
    )

    settings.action_save()

    settings.dismiss.assert_not_called()
    feedback.update.assert_called_once()
    assert "F2 is assigned to" in feedback.update.call_args.args[0]


def test_settings_screen_blocks_save_when_local_model_path_is_invalid() -> None:
    settings = SettingsScreen(
        provider="llama_cpp",
        model_path="",
        theme="dark_dungeon",
        dark=True,
        high_contrast=False,
        reduced_motion=False,
        screen_reader_mode=False,
        cognitive_load_reduction_mode=False,
        text_scale="standard",
        line_width="standard",
        line_spacing="standard",
        notification_verbosity="standard",
        scene_recap_verbosity="standard",
        runtime_metadata_verbosity="standard",
        locked_choice_verbosity="standard",
        keybindings={},
        typewriter=True,
        typewriter_speed="normal",
        diagnostics_enabled=False,
        available_themes=["dark_dungeon"],
    )
    path_feedback = MagicMock()
    path_feedback.set_class = MagicMock()
    settings_feedback = MagicMock()
    settings_feedback.set_class = MagicMock()
    keybinding_feedback = MagicMock()
    keybinding_feedback.set_class = MagicMock()
    path_input = SimpleNamespace(value="/tmp/not-a-model.txt", focus=MagicMock())
    settings.dismiss = MagicMock()
    settings.query_one = lambda selector, *_args: (
        path_input
        if selector == "#settings-model-path"
        else path_feedback
        if selector == "#settings-model-path-feedback"
        else keybinding_feedback
        if selector == "#settings-keybindings-feedback"
        else settings_feedback
        if selector == "#settings-feedback"
        else None
    )
    settings._collect_keybinding_values = MagicMock(return_value={"show_settings": "f2"})

    settings.action_save()

    settings.dismiss.assert_not_called()
    path_feedback.update.assert_called_once_with("Local Model requires a `.gguf` file.")
    path_input.focus.assert_called_once_with()


def test_cyoa_app_first_run_selection_updates_runtime_and_config(monkeypatch: pytest.MonkeyPatch):
    app = CYOAApp(model_path="")
    saved: dict[str, object] = {}
    app._sync_runtime_status = MagicMock()
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_PRESET", raising=False)
    monkeypatch.delenv("LLM_MODEL_PATH", raising=False)

    monkeypatch.setattr(
        "cyoa.ui.app.update_user_config",
        lambda **changes: saved.update(changes) or SimpleNamespace(**changes),
    )

    app._apply_first_run_selection("mock", accessibility_preset="high_contrast")

    assert os.environ["LLM_PROVIDER"] == "mock"
    assert os.environ["LLM_PRESET"] == "precise"
    assert app._runtime_diagnostics["runtime_preset"] == "mock-smoke"
    assert app._runtime_diagnostics["provider"] == "mock"
    assert app._runtime_diagnostics["model"] == "mock"
    assert app.high_contrast_mode is True
    assert app.reduced_motion is False
    assert app.screen_reader_mode is False
    assert saved["accessibility_preset"] == "high_contrast"
    assert saved["high_contrast"] is True
    assert saved["reduced_motion"] is False
    assert saved["screen_reader_mode"] is False
    assert saved["setup_completed"] is True
    assert saved["setup_choice"] == "mock"
    assert saved["runtime_preset"] == "mock-smoke"
    app._sync_runtime_status.assert_called_once_with()


def test_cyoa_app_accepts_startup_accessibility_recommendation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = CYOAApp(model_path="")
    app._continue_startup_sequence = MagicMock()
    saved: dict[str, object] = {}
    monkeypatch.setattr(
        "cyoa.ui.app.update_user_config",
        lambda **changes: saved.update(changes) or SimpleNamespace(**changes),
    )
    recommendation = StartupAccessibilityRecommendation(
        key="narrow_terminal_screen_reader",
        accessibility_preset="screen_reader_friendly",
        title="Screen Reader Friendly Startup Recommended",
        message="Use screen reader friendly mode.",
    )

    app._handle_startup_accessibility_recommendation_response(recommendation, "accept")

    assert app.screen_reader_mode is True
    assert app.reduced_motion is True
    assert app.high_contrast_mode is False
    assert saved["accessibility_preset"] == "screen_reader_friendly"
    assert saved["screen_reader_mode"] is True
    assert saved["reduced_motion"] is True
    assert saved["dismissed_startup_recommendations"] == []
    app._continue_startup_sequence.assert_called_once_with()


def test_cyoa_app_dismisses_startup_accessibility_recommendation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = CYOAApp(model_path="")
    app._continue_startup_sequence = MagicMock()
    saved: dict[str, object] = {}
    app._user_config = SimpleNamespace(dismissed_startup_recommendations=[])
    monkeypatch.setattr(
        "cyoa.ui.app.update_user_config",
        lambda **changes: (
            saved.update(changes)
            or SimpleNamespace(
                dismissed_startup_recommendations=changes["dismissed_startup_recommendations"]
            )
        ),
    )
    recommendation = StartupAccessibilityRecommendation(
        key="limited_color_high_contrast",
        accessibility_preset="high_contrast",
        title="High Contrast Startup Recommended",
        message="Use high contrast mode.",
    )

    app._handle_startup_accessibility_recommendation_response(recommendation, "dismiss")

    assert saved["dismissed_startup_recommendations"] == ["limited_color_high_contrast"]
    app._continue_startup_sequence.assert_called_once_with()


def test_cyoa_app_downloaded_model_selection_updates_runtime_and_config(
    monkeypatch: pytest.MonkeyPatch,
):
    app = CYOAApp(model_path="")
    saved: dict[str, object] = {}
    app._sync_runtime_status = MagicMock()
    previous_provider = os.environ.get("LLM_PROVIDER")
    previous_model_path = os.environ.get("LLM_MODEL_PATH")
    previous_preset = os.environ.get("LLM_PRESET")
    monkeypatch.setattr(
        "cyoa.ui.app.update_user_config",
        lambda **changes: saved.update(changes) or SimpleNamespace(**changes),
    )

    try:
        result = SimpleNamespace(path="/tmp/models/demo.gguf")
        app._pending_accessibility_preset = "screen_reader_friendly"
        app._apply_downloaded_model_selection(result)

        assert os.environ["LLM_PROVIDER"] == "llama_cpp"
        assert os.environ["LLM_MODEL_PATH"] == "/tmp/models/demo.gguf"
        assert os.environ["LLM_PRESET"] == "balanced"
        assert app.model_path == "/tmp/models/demo.gguf"
        assert app.high_contrast_mode is False
        assert app.reduced_motion is True
        assert app.screen_reader_mode is True
        assert saved["accessibility_preset"] == "screen_reader_friendly"
        assert saved["high_contrast"] is False
        assert saved["reduced_motion"] is True
        assert saved["screen_reader_mode"] is True
        assert saved["setup_completed"] is True
        assert saved["setup_choice"] == "download"
        assert saved["runtime_preset"] == "local-fast"
        app._sync_runtime_status.assert_called_once_with()
    finally:
        if previous_provider is None:
            os.environ.pop("LLM_PROVIDER", None)
        else:
            os.environ["LLM_PROVIDER"] = previous_provider
        if previous_model_path is None:
            os.environ.pop("LLM_MODEL_PATH", None)
        else:
            os.environ["LLM_MODEL_PATH"] = previous_model_path
        if previous_preset is None:
            os.environ.pop("LLM_PRESET", None)
        else:
            os.environ["LLM_PRESET"] = previous_preset


def test_cyoa_app_apply_settings_updates_runtime_and_config(monkeypatch: pytest.MonkeyPatch):
    app = CYOAApp(model_path="/tmp/current.gguf")
    app._user_config = SimpleNamespace(
        provider="mock",
        model_path=None,
        theme="dark_dungeon",
        dark=True,
        high_contrast=False,
        reduced_motion=False,
        screen_reader_mode=False,
        cognitive_load_reduction_mode=False,
        text_scale="standard",
        line_width="standard",
        line_spacing="standard",
        keybindings={},
        typewriter=True,
        typewriter_speed="normal",
        diagnostics_enabled=False,
    )
    app._runtime_diagnostics["provider"] = "mock"
    app.notify = MagicMock()
    app.action_skip_typewriter = MagicMock()
    app.set_keymap = MagicMock()
    saved: dict[str, object] = {}
    previous_env = os.environ.get("CYOA_ENABLE_RAG")
    monkeypatch.setattr(
        "cyoa.ui.app.update_user_config",
        lambda **changes: saved.update(changes) or SimpleNamespace(**changes),
    )

    try:
        app._apply_settings(
            {
                "provider": "llama_cpp",
                "model_path": "/tmp/models/demo.gguf",
                "theme": "space_explorer",
                "dark": False,
                "high_contrast": True,
                "screen_reader_mode": True,
                "cognitive_load_reduction_mode": True,
                "text_scale": "xlarge",
                "line_width": "focused",
                "line_spacing": "relaxed",
                "notification_verbosity": "minimal",
                "scene_recap_verbosity": "detailed",
                "runtime_metadata_verbosity": "minimal",
                "locked_choice_verbosity": "detailed",
                "keybindings": {"show_settings": "f2", "toggle_journal": "f3"},
                "typewriter": False,
                "typewriter_speed": "fast",
                "diagnostics_enabled": True,
            }
        )

        assert app.dark is False
        assert app.high_contrast_mode is True
        assert app.screen_reader_mode is True
        assert app.cognitive_load_reduction_mode is True
        assert app.text_scale == "xlarge"
        assert app.line_width == "focused"
        assert app.line_spacing == "relaxed"
        assert app.notification_verbosity == "minimal"
        assert app.scene_recap_verbosity == "detailed"
        assert app.runtime_metadata_verbosity == "minimal"
        assert app.locked_choice_verbosity == "detailed"
        assert app.typewriter_enabled is False
        assert app.typewriter_speed == "fast"
        assert os.environ["CYOA_ENABLE_RAG"] == "1"
        assert saved["provider"] == "llama_cpp"
        assert saved["model_path"] == "/tmp/models/demo.gguf"
        assert saved["theme"] == "space_explorer"
        assert saved["high_contrast"] is True
        assert saved["screen_reader_mode"] is True
        assert saved["cognitive_load_reduction_mode"] is True
        assert saved["accessibility_preset"] == "custom"
        assert saved["text_scale"] == "xlarge"
        assert saved["line_width"] == "focused"
        assert saved["line_spacing"] == "relaxed"
        assert saved["notification_verbosity"] == "minimal"
        assert saved["scene_recap_verbosity"] == "detailed"
        assert saved["runtime_metadata_verbosity"] == "minimal"
        assert saved["locked_choice_verbosity"] == "detailed"
        assert saved["keybindings"] == {"show_settings": "f2", "toggle_journal": "f3"}
        assert saved["diagnostics_enabled"] is True
        assert app._pending_accessibility_preset == "custom"
        app.set_keymap.assert_called_once_with({"show_settings": "f2", "toggle_journal": "f3"})
        app.action_skip_typewriter.assert_called_once_with()
        app.notify.assert_called_once()
        assert "Restart to apply: theme, provider, model path." in app.notify.call_args.args[0]
    finally:
        if previous_env is None:
            os.environ.pop("CYOA_ENABLE_RAG", None)
        else:
            os.environ["CYOA_ENABLE_RAG"] = previous_env


def test_cyoa_app_handle_settings_action_routes_requests() -> None:
    app = CYOAApp(model_path="")
    app.run_worker = MagicMock(side_effect=lambda coro, **_kwargs: coro.close())
    app._reveal_save_folder = MagicMock()
    app.export_accessibility_diagnostics_snapshot = MagicMock(return_value="/tmp/a11y.json")
    app.notify = MagicMock()
    app.push_screen = MagicMock()

    app._handle_settings_action("test_backend", {"draft_settings": {"provider": "mock"}})
    app._handle_settings_action("capture_accessibility_snapshot")
    app._handle_settings_action("reveal_saves")
    app._handle_settings_action("reset_settings")

    app.run_worker.assert_called_once()
    app._reveal_save_folder.assert_called_once_with()
    app.export_accessibility_diagnostics_snapshot.assert_called_once_with()
    app.notify.assert_called_once()
    app.push_screen.assert_called_once()


def test_cyoa_app_reset_settings_restores_safe_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    app = CYOAApp(model_path="/tmp/current.gguf")
    app.notify = MagicMock()
    app._user_config = SimpleNamespace(
        dark=False,
        reduced_motion=True,
        screen_reader_mode=True,
        cognitive_load_reduction_mode=True,
        text_scale="xlarge",
        line_width="focused",
        line_spacing="relaxed",
        notification_verbosity="minimal",
        scene_recap_verbosity="detailed",
        runtime_metadata_verbosity="minimal",
        locked_choice_verbosity="detailed",
        keybindings={"show_help": "f1"},
        typewriter=False,
        typewriter_speed="fast",
    )
    app.set_keymap = MagicMock()
    monkeypatch.setenv("CYOA_ENABLE_RAG", "1")
    monkeypatch.setenv("LLM_MODEL_PATH", "/tmp/current.gguf")
    monkeypatch.setattr(
        "cyoa.ui.app.reset_user_config",
        lambda preserve_setup=True: SimpleNamespace(
            dark=True,
            reduced_motion=False,
            screen_reader_mode=False,
            cognitive_load_reduction_mode=False,
            text_scale="standard",
            line_width="standard",
            line_spacing="standard",
            notification_verbosity="standard",
            scene_recap_verbosity="standard",
            runtime_metadata_verbosity="standard",
            locked_choice_verbosity="standard",
            keybindings={},
            typewriter=True,
            typewriter_speed="normal",
        ),
    )

    app._reset_settings_to_safe_defaults()

    assert app.dark is True
    assert app.screen_reader_mode is False
    assert app.cognitive_load_reduction_mode is False
    assert app.text_scale == "standard"
    assert app.line_width == "standard"
    assert app.line_spacing == "standard"
    assert app.notification_verbosity == "standard"
    assert app.scene_recap_verbosity == "standard"
    assert app.runtime_metadata_verbosity == "standard"
    assert app.locked_choice_verbosity == "standard"
    assert app.typewriter_enabled is True
    assert app.typewriter_speed == "normal"
    app.set_keymap.assert_called_once_with({})
    assert "CYOA_ENABLE_RAG" not in os.environ
    assert "LLM_MODEL_PATH" not in os.environ
    app.notify.assert_called_once()


@pytest.mark.asyncio
async def test_cyoa_app_backend_test_reports_missing_model_path() -> None:
    app = CYOAApp(model_path="")
    app.notify = MagicMock()
    app._user_config = SimpleNamespace(provider="llama_cpp", model_path=None)

    await app._run_backend_connection_test()

    assert "no GGUF path is saved" in app.notify.call_args.args[0]


@pytest.mark.asyncio
async def test_cyoa_app_backend_test_reports_success_for_mock() -> None:
    app = CYOAApp(model_path="")
    app.notify = MagicMock()
    app._user_config = SimpleNamespace(provider="mock", model_path=None)

    await app._run_backend_connection_test()

    assert app.notify.call_args.args[0] == "Quick Demo backend is ready."


def test_cyoa_app_reopens_settings_after_save_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    app = CYOAApp(model_path="")
    app._user_config = SimpleNamespace(
        provider="mock",
        model_path=None,
        theme="dark_dungeon",
        dark=True,
        high_contrast=False,
        reduced_motion=False,
        screen_reader_mode=False,
        cognitive_load_reduction_mode=False,
        text_scale="standard",
        line_width="standard",
        line_spacing="standard",
        notification_verbosity="standard",
        scene_recap_verbosity="standard",
        runtime_metadata_verbosity="standard",
        locked_choice_verbosity="standard",
        keybindings={},
        typewriter=True,
        typewriter_speed="normal",
        diagnostics_enabled=False,
    )
    screens: list[SettingsScreen] = []
    callbacks: list[Any] = []
    app.push_screen = MagicMock(
        side_effect=lambda screen, callback: (screens.append(screen), callbacks.append(callback))
    )
    monkeypatch.setattr("cyoa.ui.app.list_themes", lambda: ["dark_dungeon", "space_explorer"])
    monkeypatch.setattr(
        "cyoa.ui.app.update_user_config",
        lambda **_changes: (_ for _ in ()).throw(UserConfigSaveError("Unable to save settings.")),
    )

    app.action_show_settings()

    callbacks[0](
        {
            "provider": "mock",
            "model_path": None,
            "theme": "space_explorer",
            "dark": False,
            "high_contrast": True,
            "reduced_motion": False,
            "screen_reader_mode": False,
            "cognitive_load_reduction_mode": True,
            "text_scale": "large",
            "line_width": "focused",
            "line_spacing": "relaxed",
            "notification_verbosity": "minimal",
            "scene_recap_verbosity": "detailed",
            "runtime_metadata_verbosity": "minimal",
            "locked_choice_verbosity": "detailed",
            "keybindings": {"show_settings": "f2"},
            "typewriter": True,
            "typewriter_speed": "fast",
            "diagnostics_enabled": False,
        }
    )

    assert len(screens) == 2
    assert screens[1]._initial_feedback == "Unable to save settings."
    assert screens[1]._model_path == ""
    assert screens[1]._provider == "mock"
    assert screens[1]._cognitive_load_reduction_mode is True
    assert screens[1]._notification_verbosity == "minimal"
    assert screens[1]._scene_recap_verbosity == "detailed"
    assert screens[1]._runtime_metadata_verbosity == "minimal"
    assert screens[1]._locked_choice_verbosity == "detailed"
    assert screens[1]._theme_names == ["dark_dungeon", "space_explorer"]
    assert screens[1]._current_theme == "space_explorer"


class ModelDownloadHarness(App[None]):
    def __init__(self, *, blocked_reason: str | None = None) -> None:
        super().__init__()
        self.begin_first_run_model_download = MagicMock()
        self.cancel_first_run_model_download = MagicMock()
        self.screen_ref = ModelDownloadScreen(
            SimpleNamespace(
                label="7B (Balanced - Q5_K_M)",
                filename="demo.gguf",
                repo_id="Qwen/demo",
            ),
            models_dir="/tmp/cyoa-models",
            preflight_notes=("Machine check passed.",),
            blocked_reason=blocked_reason,
        )

    def compose(self) -> ComposeResult:
        yield Container()

    async def on_mount(self) -> None:
        self.push_screen(self.screen_ref)


@pytest.mark.asyncio
async def test_model_download_screen_wires_start_cancel_and_progress() -> None:
    app = ModelDownloadHarness()

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await pilot.click("#btn-model-download-start")
        await pilot.pause(0.1)
        app.begin_first_run_model_download.assert_called_once()

        app.screen_ref.update_progress(
            SimpleNamespace(percent=40, stage="Downloading", detail="Pulling model weights.")
        )
        await pilot.pause(0.1)
        progress = app.screen.query_one("#model-download-progress", ProgressBar)
        detail = app.screen.query_one("#model-download-detail", Label)
        assert progress.progress == 40
        assert "Pulling model weights." in detail.render().plain

        await pilot.click("#btn-model-download-cancel")
        await pilot.pause(0.1)
        app.cancel_first_run_model_download.assert_called_once_with()


@pytest.mark.asyncio
async def test_model_download_screen_blocks_start_when_preflight_fails() -> None:
    app = ModelDownloadHarness(blocked_reason="Only 1.0 GB free disk space is available.")

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        start_button = app.screen.query_one("#btn-model-download-start", Button)
        detail = app.screen.query_one("#model-download-detail", Label)
        assert start_button.disabled is True
        assert "Only 1.0 GB free disk space is available." in detail.render().plain
        await pilot.click("#btn-model-download-start")
        await pilot.pause(0.1)
        app.begin_first_run_model_download.assert_not_called()


@pytest.mark.asyncio
async def test_settings_screen_cycles_and_saves_choices() -> None:
    app = SettingsScreenHarness()

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        app.screen_ref.on_button_pressed(
            SimpleNamespace(button=SimpleNamespace(id="btn-settings-provider-llama"))
        )
        app.screen_ref.on_button_pressed(
            SimpleNamespace(button=SimpleNamespace(id="btn-settings-theme-next"))
        )
        app.screen_ref.on_button_pressed(
            SimpleNamespace(button=SimpleNamespace(id="btn-settings-dark-off"))
        )
        app.screen_ref.on_button_pressed(
            SimpleNamespace(button=SimpleNamespace(id="btn-settings-typewriter-off"))
        )
        app.screen_ref.on_button_pressed(
            SimpleNamespace(button=SimpleNamespace(id="btn-settings-speed-instant"))
        )
        app.screen_ref.on_button_pressed(
            SimpleNamespace(button=SimpleNamespace(id="btn-settings-diagnostics-on"))
        )
        await pilot.pause(0.1)

        provider_label = app.screen.query_one("#settings-provider-value", Label)
        theme_label = app.screen.query_one("#settings-theme-value", Label)
        assert "saved GGUF" in provider_label.render().plain
        assert "space_explorer" in theme_label.render().plain


@pytest.mark.asyncio
async def test_settings_screen_shows_terminal_fallback_profile_and_advisories() -> None:
    class FallbackSettingsHarness(App[None]):
        def __init__(self) -> None:
            super().__init__()
            self.screen_ref = SettingsScreen(
                provider="mock",
                model_path="",
                theme="dark_dungeon",
                dark=True,
                high_contrast=False,
                reduced_motion=False,
                screen_reader_mode=False,
                cognitive_load_reduction_mode=False,
                text_scale="standard",
                line_width="standard",
                line_spacing="standard",
                notification_verbosity="standard",
                scene_recap_verbosity="standard",
                runtime_metadata_verbosity="detailed",
                locked_choice_verbosity="standard",
                keybindings={},
                typewriter=True,
                typewriter_speed="normal",
                diagnostics_enabled=False,
                available_themes=["dark_dungeon"],
                terminal_accessibility_fallback=TerminalAccessibilityFallback(
                    key="limited_terminal_capability_plaintext",
                    accessibility_preset="screen_reader_friendly",
                    title="Terminal Capability Fallback Active",
                    message="Fallback is forcing plain-text rendering and reduced motion.",
                ),
            )

        def compose(self) -> ComposeResult:
            yield Container()

        async def on_mount(self) -> None:
            self.push_screen(self.screen_ref)

    app = FallbackSettingsHarness()

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        summary = _render_text(app.screen.query_one("#settings-accessibility-summary", Label))
        advisories = _render_text(app.screen.query_one("#settings-accessibility-advisories", Label))

        assert "Active profile: Screen Reader Friendly." in summary
        assert (
            "Terminal fallback for this launch forces: Reduced Motion, Screen Reader Friendly."
            in summary
        )
        assert "forcing plain-text rendering and reduced motion" in advisories
        assert "Typewriter remains enabled" in advisories


def test_cyoa_app_requires_first_run_until_setup_completed() -> None:
    assert CYOAApp._requires_first_run_setup(SimpleNamespace(setup_completed=False)) is True
    assert CYOAApp._requires_first_run_setup(SimpleNamespace(setup_completed=True)) is False


def test_theme_watch_mood_updates_container_spinner_and_theme(monkeypatch: pytest.MonkeyPatch):
    host = DummyThemeHost()
    monkeypatch.setattr(
        "cyoa.ui.mixins.theme.theme_loader.get_config_for_mood",
        lambda mood: (
            {"spinner_frames": ["{", "}"], "accent_color": "#abcdef"} if mood == "heroic" else None
        ),
    )

    host.watch_mood("default", "heroic")

    host.container.remove_class.assert_called_once_with("mood-default")
    host.container.add_class.assert_called_once_with("mood-heroic")
    assert host.spinner.frames == ["{", "}"]
    host.spinner.update.assert_called_once_with("{")
    assert "mood-heroic" in host.registered_theme_names
    assert host.theme == "mood-heroic"


def test_theme_watch_mood_preserves_high_contrast_preset(monkeypatch: pytest.MonkeyPatch):
    host = DummyThemeHost()
    host.high_contrast_mode = True
    host.theme = "cyoa-custom"
    host._apply_ui_theme_to_dynamic_content = MagicMock()
    monkeypatch.setattr(
        "cyoa.ui.mixins.theme.theme_loader.get_config_for_mood",
        lambda mood: (
            {"spinner_frames": ["{", "}"], "accent_color": "#abcdef"} if mood == "heroic" else None
        ),
    )

    host.watch_mood("default", "heroic")

    assert "mood-heroic" not in host.registered_theme_names
    assert host.theme == "cyoa-custom"
    host._apply_ui_theme_to_dynamic_content.assert_called_once_with()


def test_apply_ui_theme_uses_high_contrast_surfaces_when_enabled() -> None:
    host = DummyThemeHost()
    host.high_contrast_mode = True
    host._ui_theme = {
        "main_surface": "#101010",
        "action_dock_surface": "#111111",
        "status_surface": "#121212",
        "side_panel_surface": "#131313",
        "story_card_surface": "#141414",
        "story_card_muted_surface": "#151515",
        "player_choice_surface": "#161616",
        "choice_surface": "#171717",
        "choice_locked_surface": "#181818",
    }

    host.apply_ui_theme()

    host.container.set_styles.assert_called_once_with("background: #000000;")
    host.action_panel.set_styles.assert_called_once_with("background: #050505;")
    host.status_display.set_styles.assert_called_once_with("background: #050505;")


def test_apply_ui_theme_styles_primary_surfaces_and_dynamic_widgets() -> None:
    host = DummyThemeHost()
    host._ui_theme = {
        "main_surface": "#101820",
        "action_dock_surface": "#111827",
        "status_surface": "#17212b",
        "side_panel_surface": "#131a22",
        "story_card_surface": "#18242d",
        "story_card_muted_surface": "#0f161d",
        "player_choice_surface": "#1b3140",
        "choice_surface": "#213646",
        "choice_locked_surface": "#15191d",
    }

    host.apply_ui_theme()

    host.container.set_styles.assert_called_once_with("background: #101820;")
    host.action_panel.set_styles.assert_called_once_with("background: #111827;")
    host.status_display.set_styles.assert_called_once_with("background: #17212b;")
    host.side_panel.set_styles.assert_called_once_with("background: #131a22;")
    host.story_turn.set_styles.assert_called_once_with(
        _build_surface_style("#18242d", accent="#6EA8FF")
    )
    host.archived_turn.set_styles.assert_called_once_with(
        _build_surface_style("#0f161d", accent="#18242d", muted=True)
    )
    host.player_choice.set_styles.assert_called_once_with(
        _build_surface_style("#1b3140", accent="#6EA8FF")
    )
    host.choice_card.set_styles.assert_called_once_with(
        _build_surface_style("#213646", accent="#6EA8FF")
    )
    host.locked_choice.set_styles.assert_called_once_with(
        _build_surface_style("#15191d", accent="#D0A85C", muted=True)
    )


def test_rendering_show_loading_and_mount_choice_buttons_cover_states():
    host = DummyRenderingChoiceHost()
    choices_container = DummyChoiceContainer()
    story_container = MagicMock()
    keep = DummyChoiceButton("choice-keep")
    drop = DummyChoiceButton("choice-drop")
    loading_widget = MagicMock()
    host.query_one = lambda selector, *_args: (
        choices_container
        if selector == "#choices-container"
        else story_container
        if selector == "#story-container"
        else loading_widget
    )
    host.is_runtime_active = lambda: True

    choices_container.mounted = [keep, drop]
    host.show_loading(selected_button_id="choice-keep")

    assert drop.removed is True
    assert keep.disabled is True
    assert keep.variant == "default"
    story_container.add_class.assert_called_once_with("loading-state")
    assert "loading-state" not in choices_container.classes
    loading_widget.remove_class.assert_called_once_with("hidden")
    assert host._loading_suffix_shown is True

    error_container = DummyChoiceContainer()
    error_node = StoryNode(
        narrative=f"{constants.ERROR_NARRATIVE_PREFIX} backend timeout",
        choices=[Choice(text="Fallback option"), Choice(text="Retreat")],
    )
    host._mount_choice_buttons(error_node, error_container, is_error=True)
    assert [getattr(widget, "id", None) for widget in error_container.mounted][:1] == ["btn-retry"]

    ending_container = DummyChoiceContainer()
    host._mount_choice_buttons(
        StoryNode(narrative="The end.", choices=[], is_ending=True),
        ending_container,
        is_error=False,
    )
    assert [getattr(widget, "id", None) for widget in ending_container.mounted] == [
        "btn-new-adventure"
    ]

    locked_container = DummyChoiceContainer()
    locked_choice = Choice(
        text="Open gate",
        requirements=ChoiceRequirement(items=["key"]),
    )
    host.engine.state.inventory = []
    host._mount_choice_buttons(
        StoryNode(
            narrative="A locked gate blocks the path.",
            choices=[locked_choice, Choice(text="Walk away")],
        ),
        locked_container,
        is_error=False,
    )
    mounted_button = locked_container.mounted[0]
    assert mounted_button.disabled is True
    assert "Unavailable:" in str(mounted_button.label)
    assert "Missing item: key" in str(mounted_button.label)

    checked_container = DummyChoiceContainer()
    host._mount_choice_buttons(
        StoryNode(
            narrative="The ravine howls below.",
            choices=[
                Choice(
                    text="Leap the gap",
                    check=ChoiceCheck(
                        stat="reputation",
                        difficulty=12,
                        stakes="You plunge into the ravine.",
                    ),
                ),
                Choice(text="Retreat"),
            ],
        ),
        checked_container,
        is_error=False,
    )
    checked_button = checked_container.mounted[0]
    assert checked_button.disabled is False
    assert "Check: reputation vs difficulty 12" in str(checked_button.label)
    assert "Stakes: You plunge into the ravine." in str(checked_button.label)


def test_typewriter_settings_actions_persist_preferences(monkeypatch: pytest.MonkeyPatch):
    host = DummyTypewriterSettingsHost()
    config: dict[str, object] = {}
    monkeypatch.setattr("cyoa.ui.mixins.typewriter.utils.load_config", lambda: config)
    monkeypatch.setattr(
        "cyoa.ui.mixins.typewriter.utils.save_config", lambda payload: config.update(payload)
    )

    host._typewriter_active_chunk = list("Hi")
    host.action_toggle_typewriter()
    host.action_cycle_typewriter_speed()

    assert host.notifications[0] == "Typewriter Narrator: Disabled"
    assert host.notifications[1].startswith("Typewriter Speed:")
    assert config["typewriter"] is False
    assert config["typewriter_speed"] == "slow"
    assert host._current_story == "Hi"


def test_app_notification_and_cache_helpers_cover_ui_shell(monkeypatch: pytest.MonkeyPatch):
    app = CYOAApp(model_path="dummy.gguf")
    notified: list[tuple[str, str, float]] = []
    timer = DummyAppTimer()

    monkeypatch.setattr(
        app,
        "_dispatch_notification",
        lambda message, *, title, severity, timeout, markup, update_latest: notified.append(
            (message, severity, timeout)
        ),
    )
    monkeypatch.setattr(app, "set_timer", lambda *_args, **_kwargs: timer)

    app._running = False
    app.queue_notification("ignored", severity="information", timeout=2)
    assert app._notification_buffer == []
    assert app._notification_timer is None

    app._running = True
    app.queue_notification("alpha", severity="information", timeout=2)
    app.queue_notification("alpha", severity="information", timeout=2)
    app.queue_notification("beta", severity="warning", timeout=4)
    assert len(app._notification_buffer) == 2
    assert app._notification_timer is timer
    assert app.get_notification_history_lines() == [
        "Information: alpha",
        "Information: alpha",
        "Warning: beta",
    ]

    app._flush_buffered_notifications()
    assert notified == [("Warning: alpha | beta", "warning", 4)]
    assert app._notification_buffer == []

    app._notification_buffer = [
        BufferedNotification("one", "information", 1),
        BufferedNotification("two", "warning", 2),
        BufferedNotification("three", "error", 3),
        BufferedNotification("four", "information", 1),
    ]
    app._flush_buffered_notifications()
    assert notified[-1] == ("Error: one | two | three | +1 more", "error", 3)

    app.cache_story_history("a", {"turn": 1})
    app.cache_story_history("b", {"turn": 2})
    app.cache_story_map("a", {"map": 1})
    app.cache_story_map("b", {"map": 2})
    assert app.get_cached_story_history("a") == {"turn": 1}
    assert app.get_cached_story_map("b") == {"map": 2}

    app.invalidate_scene_caches(keep_scene_id="b")
    assert app.get_cached_story_history("a") is None
    assert app.get_cached_story_history("b") == {"turn": 2}
    assert app.get_cached_story_map("a") is None
    assert app.get_cached_story_map("b") == {"map": 2}

    app.invalidate_scene_caches()
    assert app._story_history_cache == {}
    assert app._story_map_cache == {}


def test_app_notify_tracks_latest_status_and_repeat_action(monkeypatch: pytest.MonkeyPatch) -> None:
    app = CYOAApp(model_path="dummy.gguf")
    app.screen_reader_mode = True
    app.notification_verbosity = "minimal"
    repeated: list[tuple[str, str, str, bool]] = []

    monkeypatch.setattr(
        "textual.app.App.notify",
        lambda _self, message, *, title, severity, timeout, markup: repeated.append(
            (message, title, severity, markup)
        ),
    )

    app.notify("⚡ Weaving possible futures...", severity="information", timeout=3)
    app.action_repeat_latest_status()

    assert app._latest_status_message == "Weaving possible futures..."
    assert app.get_notification_history_lines() == ["Weaving possible futures..."]
    assert repeated[0] == (
        "Weaving possible futures...",
        "Information",
        "information",
        False,
    )
    assert repeated[1] == (
        "Weaving possible futures...",
        "Latest Status",
        "information",
        False,
    )


def test_notification_history_screen_renders_entries_in_order() -> None:
    class NotificationHistoryHarness(App[None]):
        def compose(self) -> ComposeResult:
            yield NotificationHistoryScreen(
                [
                    "Information: First clue.",
                    "Warning: Lantern fading.",
                    "Error: Bridge collapsed.",
                ]
            )

    app = NotificationHistoryHarness()

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            screen = cast(NotificationHistoryScreen, app.screen)
            labels = [
                label.render().plain for label in screen.query("#notification-history-list Label")
            ]
            assert labels == [
                "1. Information: First clue.",
                "2. Warning: Lantern fading.",
                "3. Error: Bridge collapsed.",
            ]

    asyncio.run(run())


def test_watch_screen_reader_mode_updates_loading_text_scene_art_and_choices() -> None:
    app = CYOAApp(model_path="dummy.gguf")
    status_display = SimpleNamespace(screen_reader_mode=False)
    scene_art = MagicMock()
    choices_container = SimpleNamespace(remove_children=MagicMock())
    current_node = SimpleNamespace(narrative="A calm corridor.")
    app.engine = SimpleNamespace(state=SimpleNamespace(current_node=current_node))
    app._loading_suffix_shown = False
    app._current_story = constants.LOADING_ART
    app._current_turn_text = constants.LOADING_ART
    app._current_turn_widget = MagicMock()
    app.set_class = MagicMock()
    app._reset_story_segments = MagicMock()
    app._mount_choice_buttons = MagicMock()
    app._update_scene_art = MagicMock()
    app.query_one = lambda selector, *_args: (
        status_display
        if selector is StatusDisplay
        else scene_art
        if selector == "#scene-art"
        else choices_container
    )

    app.watch_screen_reader_mode(True)

    assert status_display.screen_reader_mode is True
    assert app._current_story == "Loading story..."
    assert app._current_turn_text == "Loading story..."
    app._reset_story_segments.assert_called_once_with("Loading story...")
    app._current_turn_widget.update.assert_called_once_with("Loading story...")
    scene_art.add_class.assert_called_once_with("hidden")
    choices_container.remove_children.assert_called_once_with()
    app._mount_choice_buttons.assert_called_once_with(current_node, choices_container, False)

    app.watch_screen_reader_mode(False)

    assert status_display.screen_reader_mode is False
    assert app._update_scene_art.call_args_list[-1].args == ("A calm corridor.", False)


def test_app_cache_helpers_isolate_mutable_payloads() -> None:
    app = CYOAApp(model_path="dummy.gguf")
    history_payload = {"scenes": [{"id": "scene-1"}], "choices": ["Wait"]}
    map_payload = {"nodes": [{"id": "scene-1", "children": []}]}

    app.cache_story_history("scene-1", history_payload)
    app.cache_story_map("scene-1", map_payload)

    history_payload["scenes"][0]["id"] = "mutated-source"
    map_payload["nodes"][0]["children"].append("mutated-source")

    cached_history = app.get_cached_story_history("scene-1")
    cached_map = app.get_cached_story_map("scene-1")

    assert cached_history == {"scenes": [{"id": "scene-1"}], "choices": ["Wait"]}
    assert cached_map == {"nodes": [{"id": "scene-1", "children": []}]}

    assert cached_history is not None
    assert cached_map is not None
    cached_history["scenes"][0]["id"] = "mutated-copy"
    cached_map["nodes"][0]["children"].append("mutated-copy")

    assert app.get_cached_story_history("scene-1") == {
        "scenes": [{"id": "scene-1"}],
        "choices": ["Wait"],
    }
    assert app.get_cached_story_map("scene-1") == {"nodes": [{"id": "scene-1", "children": []}]}


def test_app_marks_first_scene_and_tears_down_runtime(monkeypatch: pytest.MonkeyPatch):
    app = CYOAApp(model_path="dummy.gguf")
    app._running = True
    startup_timer = DummyAppTimer()
    warmup_timer = DummyAppTimer()
    notification_timer = DummyAppTimer()

    app._startup_timer = cast(Any, startup_timer)
    app._post_render_warmup_timer = cast(Any, warmup_timer)
    app._notification_timer = cast(Any, notification_timer)
    cancel_background_workers = MagicMock()
    close_runtime_resources = MagicMock()
    unsubscribe_engine_events = MagicMock()
    monkeypatch.setattr(cast(Any, app), "_cancel_background_workers", cancel_background_workers)
    monkeypatch.setattr(cast(Any, app), "_close_runtime_resources", close_runtime_resources)
    monkeypatch.setattr(cast(Any, app), "_unsubscribe_engine_events", unsubscribe_engine_events)
    scheduled_timer = DummyAppTimer()
    monkeypatch.setattr(app, "set_timer", lambda *_args, **_kwargs: scheduled_timer)

    app.mark_first_scene_rendered()
    assert app._has_rendered_first_scene is True
    assert app._post_render_warmup_timer is scheduled_timer

    app.on_unmount()
    assert startup_timer.stopped is True
    assert scheduled_timer.stopped is True
    assert notification_timer.stopped is True
    cancel_background_workers.assert_called_once()
    close_runtime_resources.assert_called_once()
    unsubscribe_engine_events.assert_called_once()
