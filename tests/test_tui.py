import asyncio
import json
import os
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from textual.containers import Container, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Button, Input, Label, ListView, Markdown, Static
from textual.widgets._toast import Toast
from textual.worker import WorkerFailed

from cyoa.core.events import EventBus, EventDispatchError, Events, bus
from cyoa.core.models import Choice, ChoiceRequirement, StoryNode
from cyoa.core.theme_loader import load_theme
from cyoa.core.user_config import UserConfig
from cyoa.ui.app import CYOAApp
from cyoa.ui.components import (
    AccessibleSummaryScreen,
    BranchScreen,
    CommandPaletteScreen,
    ConfirmScreen,
    HelpScreen,
    LoadGameScreen,
    NotificationHistoryScreen,
    SceneRecapScreen,
    SettingsScreen,
    StartupAccessibilityRecommendationScreen,
    StartupChoiceScreen,
    TextPromptScreen,
)
from cyoa.ui.mixins.navigation import NavigationMixin
from cyoa.ui.mixins.persistence import PersistenceMixin

# ── Helpers ──────────────────────────────────────────────────────────────────


def _mock_generator(*args, **kwargs):
    """Return a mock generator that yields predefined StoryNodes."""
    mock_gen = MagicMock()
    # First generated node (startup)
    node1 = StoryNode(
        narrative="You awaken in a test dungeon.",
        choices=[Choice(text="Go North"), Choice(text="Go South")],
        items_gained=["Broken Sword"],
        title="Test Adventure",
    )
    # Second generated node (after choice)
    node2 = StoryNode(
        narrative="You went North.",
        choices=[Choice(text="Open Door"), Choice(text="Go Back")],
        items_gained=["Health Potion"],
        stat_updates={"health": -10, "gold": 50},
        title="Test Adventure",
    )
    # Third generated node (ending)
    node3 = StoryNode(
        narrative="You opened the door and escaped!",
        choices=[],
        is_ending=True,
        title="Test Adventure",
    )

    async def side_effect_func_async(context, *args, **kwargs):
        history_len = len(context.history)
        if history_len <= 1:
            return node1  # new adventure / restart
        elif history_len == 3:
            return node2  # first choice made
        else:
            return node3  # second choice made / ending

    mock_gen.generate_next_node_async = AsyncMock(side_effect=side_effect_func_async)
    mock_gen.update_story_summaries_async = AsyncMock()
    mock_gen.save_state_async = AsyncMock(return_value=b"state")
    mock_gen.load_state_async = AsyncMock()
    mock_gen.token_budget = 2048
    mock_gen.provider = MagicMock()
    mock_gen.provider.count_tokens = MagicMock(return_value=10)
    return mock_gen


def _app_with_accessibility_config(**config_overrides: Any) -> CYOAApp:
    config = UserConfig(setup_completed=True, **config_overrides)
    with patch("cyoa.ui.app.load_user_config", return_value=config):
        return CYOAApp(model_path="dummy_path.gguf")


async def _wait_for(
    predicate: Any,
    *,
    timeout: float = 3.0,
    interval: float = 0.05,
) -> None:
    """Poll until a condition becomes true to avoid fixed-delay UI test races."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("Timed out waiting for test condition")


async def _wait_for_pilot(
    pilot: Any,
    predicate: Any,
    *,
    timeout: float = 3.0,
    interval: float = 0.05,
) -> None:
    """Poll a UI condition while advancing Textual's test pilot loop."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await pilot.pause(interval)
    raise AssertionError("Timed out waiting for test condition")


def _assert_region_within_screen(widget: Any, screen_size: Any) -> None:
    """Assert that a widget's full box, including borders, remains on screen."""
    region = widget.region
    assert region.x >= 0
    assert region.y >= 0
    assert region.right <= screen_size.width
    assert region.bottom <= screen_size.height


def _assert_region_within_parent(widget: Any, parent: Any) -> None:
    """Assert that a widget's full box, including borders, remains inside its parent."""
    region = widget.region
    parent_region = parent.region
    assert region.x >= parent_region.x
    assert region.y >= parent_region.y
    assert region.right <= parent_region.right
    assert region.bottom <= parent_region.bottom


def _assert_horizontal_region_within_parent(widget: Any, parent: Any) -> None:
    """Assert that a widget's horizontal border box remains inside its parent."""
    region = widget.region
    parent_region = parent.region
    assert region.x >= parent_region.x
    assert region.right <= parent_region.right


@pytest.fixture(autouse=True)
def _clear_terminal_fallback_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)


@pytest.fixture
def mock_app_dependencies():
    """Mock the LLM Generator and DB to be fast and deterministic in UI tests."""
    with (
        patch("cyoa.ui.app.ModelBroker", new=_mock_generator),
        patch("cyoa.ui.app.CYOAGraphDB") as mock_db,
        patch(
            "cyoa.ui.app.load_user_config",
            return_value=UserConfig(
                setup_completed=True,
                dismissed_startup_recommendations=["narrow_terminal_screen_reader"],
            ),
        ),
    ):
        # Configure the mock DB to not fail async DB operations
        db_instance = mock_db.return_value
        db_instance.verify_connectivity_async = AsyncMock(return_value=True)
        db_instance.create_story_node_and_get_title.return_value = "Test Adventure"
        db_instance.get_story_tree.return_value = None  # Just empty for story map test initially

        db_instance.save_scene_async = AsyncMock(return_value="dummy-scene-id")

        yield


# ── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_app_startup_and_loading_state(mock_app_dependencies):
    """Test that the app starts up, shows loading art, and renders the initial generated scene."""
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        # Give the background workers a moment to process initial generation
        await pilot.pause(1.5)
        app.action_skip_typewriter()

        # Verify the story text container updated with the mock narrative
        assert "You awaken in a test dungeon." in app._current_story

        # Verify choices were generated
        choices_container = app.query_one("#choices-container", Container)
        buttons = list(choices_container.query(Button))
        assert len(buttons) == 2
        assert str(buttons[0].label) == "1. Go North"
        assert str(buttons[1].label) == "2. Go South"

        # Verify inventory was updated
        inventory_label = app.query_one("#inventory-label", Label)
        inventory_text = str(inventory_label.render())
        assert "Broken Sword" in inventory_text

        runtime_text = app.query_one("#runtime-text", Label).render().plain
        assert "ready" in runtime_text


def test_event_bus_prevents_duplicate_subscriptions() -> None:
    event_bus = EventBus()
    received: list[str] = []

    def callback(message: str) -> None:
        received.append(message)

    unsubscribe_first = event_bus.subscribe("status", callback)
    unsubscribe_second = event_bus.subscribe("status", callback)

    assert event_bus.subscriber_count("status") == 1

    event_bus.emit("status", message="ready")
    assert received == ["ready"]

    unsubscribe_first()
    unsubscribe_second()
    assert event_bus.subscriber_count("status") == 0


def test_event_bus_removes_failing_callbacks_after_dispatch_error() -> None:
    event_bus = EventBus()
    received: list[int] = []

    def broken(value: int) -> None:
        raise RuntimeError("boom")

    def healthy(value: int) -> None:
        received.append(value)

    event_bus.subscribe("tick", broken)
    event_bus.subscribe("tick", healthy)

    with pytest.raises(EventDispatchError, match="tick"):
        event_bus.emit("tick", value=1)

    assert event_bus.subscriber_count("tick") == 1

    event_bus.emit("tick", value=2)
    assert received == [1, 2]


def test_event_bus_emit_runtime_logs_failures_without_unsubscribing() -> None:
    event_bus = EventBus()
    received: list[int] = []

    def broken(value: int) -> None:
        raise RuntimeError("boom")

    def healthy(value: int) -> None:
        received.append(value)

    event_bus.subscribe("tick", broken)
    event_bus.subscribe("tick", healthy)

    event_bus.emit_runtime("tick", value=1)
    event_bus.emit_runtime("tick", value=2)

    assert event_bus.subscriber_count("tick") == 2
    assert received == [1, 2]


@pytest.mark.asyncio
async def test_stats_display_reflects_player_stats(mock_app_dependencies):
    """Test that the stats display updates with different color codes depending on health."""
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)
        status_display = app.query_one("#status-display")
        health_value = app.query_one("#health-value", Label)

        # Initial stats: Health 100 (high)
        assert "100%" in str(health_value.render())
        assert status_display.has_class("health-high")

        # Update stats to mid-health
        app.query_one("StatusDisplay").health = 50
        await pilot.pause(0.1)  # Wait for reactive update
        assert "50%" in str(health_value.render())
        assert status_display.has_class("health-mid")

        # Update stats to low-health
        app.query_one("StatusDisplay").health = 20
        await pilot.pause(0.1)
        assert "20%" in str(health_value.render())
        assert status_display.has_class("health-low")

        # Update stats to dead
        app.query_one("StatusDisplay").health = 0
        await pilot.pause(0.1)
        # Use .plain to get the text without markup/formatting
        rendered_text = health_value.render().plain
        assert "0%" in rendered_text
        assert status_display.has_class("health-low")


@pytest.mark.asyncio
async def test_screen_reader_mode_uses_plain_status_shell_on_startup(mock_app_dependencies):
    with patch(
        "cyoa.ui.app.load_user_config",
        return_value=UserConfig(setup_completed=True, screen_reader_mode=True),
    ):
        app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await _wait_for_pilot(
            pilot,
            lambda: app.engine is not None and app.engine.state.current_node is not None,
        )
        app.action_skip_typewriter()

        inventory_label = app.query_one("#inventory-label", Label).render().plain
        latest_status = app.query_one("#latest-status-label", Label).render().plain
        scene_art = app.query_one("#scene-art", Static)

        assert app.screen_reader_mode is True
        assert inventory_label.startswith("Inventory:")
        assert latest_status.startswith("Information:")
        assert scene_art.has_class("hidden")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "attribute", "enabled_class"),
    [
        ({"screen_reader_mode": True}, "screen_reader_mode", "screen-reader-mode"),
        ({"high_contrast": True}, "high_contrast_mode", "high-contrast-mode"),
        ({"reduced_motion": True}, "reduced_motion", "reduced-motion"),
    ],
)
async def test_startup_accessibility_overrides_apply_before_first_paint(
    mock_app_dependencies,
    overrides: dict[str, bool],
    attribute: str,
    enabled_class: str,
):
    with patch(
        "cyoa.ui.app.load_user_config",
        return_value=UserConfig(setup_completed=True),
    ):
        app = CYOAApp(
            model_path="dummy_path.gguf",
            startup_accessibility_overrides=overrides,
        )

    async with app.run_test() as pilot:
        await _wait_for_pilot(
            pilot,
            lambda: app.engine is not None and app.engine.state.current_node is not None,
        )
        assert getattr(app, attribute) is True
        assert app.has_class(enabled_class)
        if attribute == "screen_reader_mode":
            assert app.query_one("#scene-art", Static).has_class("hidden")


@pytest.mark.asyncio
async def test_startup_accessibility_overrides_win_over_saved_config(
    mock_app_dependencies,
):
    with patch(
        "cyoa.ui.app.load_user_config",
        return_value=UserConfig(
            setup_completed=True,
            high_contrast=False,
            reduced_motion=False,
            screen_reader_mode=False,
        ),
    ):
        app = CYOAApp(
            model_path="dummy_path.gguf",
            startup_accessibility_overrides={
                "screen_reader_mode": True,
                "high_contrast": True,
                "reduced_motion": True,
            },
        )

    assert app._user_config.screen_reader_mode is False
    assert app._user_config.high_contrast is False
    assert app._user_config.reduced_motion is False

    async with app.run_test() as pilot:
        await _wait_for_pilot(
            pilot,
            lambda: app.engine is not None and app.engine.state.current_node is not None,
        )
        assert app.screen_reader_mode is True
        assert app.high_contrast_mode is True
        assert app.reduced_motion is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config_overrides", "expected_classes", "expected_status_line", "expect_scene_art_hidden"),
    [
        (
            {
                "screen_reader_mode": True,
                "reduced_motion": True,
                "typewriter": False,
            },
            ("screen-reader-mode", "reduced-motion"),
            "Information: Weaving possible futures...",
            True,
        ),
        (
            {
                "high_contrast": True,
                "text_scale": "xlarge",
                "line_width": "focused",
                "line_spacing": "relaxed",
                "typewriter": False,
            },
            (
                "high-contrast-mode",
                "text-scale-xlarge",
                "line-width-focused",
                "line-spacing-relaxed",
            ),
            "Information: ⚡ Weaving possible futures...",
            False,
        ),
        (
            {
                "screen_reader_mode": True,
                "reduced_motion": True,
                "cognitive_load_reduction_mode": True,
                "typewriter": False,
            },
            ("screen-reader-mode", "reduced-motion", "cognitive-load-mode"),
            "Update: Weaving possible futures...",
            True,
        ),
    ],
)
async def test_accessibility_matrix_covers_story_notifications_and_settings(
    mock_app_dependencies,
    config_overrides: dict[str, Any],
    expected_classes: tuple[str, ...],
    expected_status_line: str,
    expect_scene_art_hidden: bool,
) -> None:
    app = _app_with_accessibility_config(**config_overrides)

    async with app.run_test(size=(100, 34)) as pilot:
        await _wait_for_pilot(
            pilot,
            lambda: app.engine is not None and app.engine.state.current_node is not None,
        )

        for class_name in expected_classes:
            assert app.has_class(class_name)

        assert app.query_one("#scene-art", Static).has_class("hidden") is expect_scene_art_hidden

        app.notify("⚡ Weaving possible futures...", severity="information", timeout=1)
        await pilot.pause(0.2)
        assert app.get_notification_history_lines()[-1] == expected_status_line

        app.action_show_settings()
        await pilot.pause(0.2)
        assert app.screen.query_one("#settings-dialog")
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert app.screen.id == "_default"


@pytest.mark.asyncio
async def test_inventory_updates_on_item_gain_and_loss(mock_app_dependencies):
    """Test that the inventory display and app state update correctly when items are gained or lost."""
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)

        # Initial inventory should have Broken Sword (from node1)
        assert "Broken Sword" in app.engine.state.inventory

        # Gain an item via a choice that returns node2 (which has Health Potion)
        await pilot.press("1")
        await pilot.pause(1.0)
        assert "Health Potion" in app.engine.state.inventory
        assert "Broken Sword" in app.engine.state.inventory

        # Mock item loss: Manually trigger a display update for a hypothetical node that loses an item
        from cyoa.core.models import Choice, StoryNode

        loss_node = StoryNode(
            narrative="You used the potion.",
            choices=[Choice(text="Continue"), Choice(text="Wait")],
            items_gained=[],
            items_lost=["Health Potion"],
            title="Test Adventure",
        )

        # We can't easily force the generator to return this without more complex patching,
        # but we can test the display_node logic which handles the updates.
        # colocate unique turn_count to avoid ID collisions in tests
        app.turn_count = 99
        app.display_node(loss_node)

        app.query_one("StatusDisplay").inventory = []
        await pilot.pause(0.1)
        inv_label = app.query_one("#inventory-label", Label)
        assert "Health Potion" not in inv_label.render().plain
        assert "Broken Sword" not in inv_label.render().plain


@pytest.mark.asyncio
async def test_status_display_shows_active_objectives(mock_app_dependencies):
    app = CYOAApp(
        model_path="dummy_path.gguf",
        initial_world_state={
            "objectives": [{"id": "escape", "text": "Escape the dungeon", "status": "active"}]
        },
    )

    async with app.run_test() as pilot:
        await pilot.pause(1.0)
        objectives_text = app.query_one("#objectives-label", Label).render().plain
        assert "Escape the dungeon" in objectives_text


@pytest.mark.asyncio
async def test_locked_choices_render_disabled_and_block_selection(mock_app_dependencies):
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)
        assert app.engine is not None
        app.engine.state.story_flags.clear()
        locked_node = StoryNode(
            narrative="A sealed door bars the way.",
            choices=[
                Choice(
                    text="Open the sigil door",
                    requirements=ChoiceRequirement(flags=["sigil_unlocked"]),
                ),
                Choice(text="Wait"),
            ],
        )

        app.turn_count = 42
        app.engine.state.current_node = locked_node
        app.display_node(locked_node)
        await pilot.pause(0.1)

        buttons = list(app.query_one("#choices-container", Container).query(Button))
        assert buttons[0].disabled is True
        assert "Missing event: sigil_unlocked" in str(buttons[0].label)

        current_story_before = app._current_story
        await app._trigger_choice(0)
        assert app._current_story == current_story_before


@pytest.mark.asyncio
async def test_ui_panels_toggle(mock_app_dependencies):
    """Test pressing hotkeys toggles the visibility of the side panels and dark mode."""
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        journal_panel = app.query_one("#journal-panel", Container)
        map_panel = app.query_one("#story-map-panel", Container)

        # Both panels should be hidden by default
        assert journal_panel.has_class("panel-collapsed")
        assert map_panel.has_class("panel-collapsed")

        # Press 'j' to toggle Journal
        await pilot.press("j")
        assert not journal_panel.has_class("panel-collapsed")

        # Press 'm' to toggle Story Map
        await pilot.press("m")
        assert not map_panel.has_class("panel-collapsed")

        # Test dark mode toggle (starts as whatever config is, just verify toggle changes it)
        initial_dark = app.dark
        await pilot.press("d")
        assert app.dark is not initial_dark


@pytest.mark.asyncio
async def test_startup_does_not_wait_for_optional_runtime_checks() -> None:
    release_connectivity = asyncio.Event()

    def slow_db_factory(*args, **kwargs):
        db = MagicMock()

        async def verify_connectivity_async():
            await release_connectivity.wait()
            return True

        db.verify_connectivity_async = AsyncMock(side_effect=verify_connectivity_async)
        db.create_story_node_and_get_title.return_value = "Test Adventure"
        db.get_story_tree.return_value = None
        db.save_scene_async = AsyncMock(return_value="dummy-scene-id")
        return db

    with (
        patch("cyoa.ui.app.ModelBroker", new=_mock_generator),
        patch("cyoa.ui.app.CYOAGraphDB", side_effect=slow_db_factory),
    ):
        app = CYOAApp(model_path="dummy_path.gguf")

        async with app.run_test() as pilot:
            await pilot.pause(0.6)
            app.action_skip_typewriter()

            assert "You awaken in a test dungeon." in app._current_story

            release_connectivity.set()
            await pilot.pause(0.2)


@pytest.mark.asyncio
async def test_choice_selection_via_keyboard(mock_app_dependencies):
    """Test selecting a choice updates the narrative and inventory correctly."""
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        # Wait for initial load
        await pilot.pause(1.0)
        app.action_skip_typewriter()

        # Press '1' to select the first choice ("Go North")
        await pilot.press("1")

        # Pause to let the worker thread process the next mock node
        await pilot.pause(1.0)
        app.action_skip_typewriter()

        # Verify the story text appended the new narrative
        assert "You went North." in app._current_story

        # Check that the UI choice buttons updated to the new choices
        choices_container = app.query_one("#choices-container", Container)
        buttons = list(choices_container.query(Button))
        assert len(buttons) == 2
        assert str(buttons[0].label) == "1. Open Door"
        assert str(buttons[1].label) == "2. Go Back"

        # Verify inventory accumulated the new item
        inventory_label = app.query_one("#inventory-label", Label)
        inventory_text = str(inventory_label.render())
        assert "Broken Sword" in inventory_text
        assert "Health Potion" in inventory_text

        # Verify stats updated (now in separate #stats-text label)
        stats_label = app.query_one("#stats-text", Label)
        stats_text = str(stats_label.render())
        assert "Gold 50" in stats_text
        assert "Reputation" in stats_text

        # Verify journal updated
        journal_list = app.query_one("#journal-list", ListView)
        journal_labels = journal_list.query(Label)
        journal_text = "".join(str(label.render()) for label in journal_labels)
        assert "Go North" in journal_text


@pytest.mark.asyncio
async def test_choice_selection_via_click(mock_app_dependencies):
    """Test clicking a choice button triggers the next step as expected."""
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)

        # Click the first choice button
        choices_container = app.query_one("#choices-container", Container)
        first_btn = list(choices_container.query(Button))[0]
        first_btn.focus()
        await pilot.press("enter")

        await pilot.pause(1.0)
        assert "You went North." in app._current_story

        choices_container = app.query_one("#choices-container", Container)
        buttons = list(choices_container.query(Button))
        assert len(buttons) == 2
        assert str(buttons[0].label) == "1. Open Door"
        assert str(buttons[1].label) == "2. Go Back"


@pytest.mark.asyncio
async def test_choice_focus_moves_with_arrow_keys(mock_app_dependencies):
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)

        choices_container = app.query_one("#choices-container", Container)
        buttons = list(choices_container.query(Button))
        assert len(buttons) == 2
        assert app.focused is buttons[0]

        await pilot.press("down")
        await pilot.pause(0.1)
        assert app.focused is buttons[1]

        await pilot.press("up")
        await pilot.pause(0.1)
        assert app.focused is buttons[0]

        await pilot.press("down", "enter")
        await pilot.pause(1.0)
        app.action_skip_typewriter()

        journal_list = app.query_one("#journal-list", ListView)
        journal_text = "".join(str(label.render()) for label in journal_list.query(Label))
        assert "Go South" in journal_text


@pytest.mark.asyncio
async def test_structural_navigation_shortcuts_jump_to_major_regions(mock_app_dependencies):
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test(size=(100, 34)) as pilot:
        await pilot.pause(1.0)

        story = app.query_one("#story-container", VerticalScroll)
        status_display = app.query_one("#status-display")
        journal_panel = app.query_one("#journal-panel", Container)
        journal_list = app.query_one("#journal-list", ListView)
        map_panel = app.query_one("#story-map-panel", Container)
        story_map_tree = app.query_one("#story-map-tree")
        choices = list(app.query_one("#choices-container", Container).query(Button))

        await pilot.press("shift+s")
        await pilot.pause(0.1)
        assert app.focused is story

        await pilot.press("shift+i")
        await pilot.pause(0.1)
        assert app.focused is status_display

        await pilot.press("shift+c")
        await pilot.pause(0.1)
        assert app.focused is choices[0]

        await pilot.press("shift+j")
        await pilot.pause(0.2)
        assert not journal_panel.has_class("panel-collapsed")
        assert app.focused is journal_list

        await pilot.press("shift+m")
        await pilot.pause(0.2)
        assert journal_panel.has_class("panel-collapsed")
        assert not map_panel.has_class("panel-collapsed")
        assert app.focused is story_map_tree

        await pilot.press("shift+n")
        await pilot.pause(0.2)
        assert isinstance(app.screen, NotificationHistoryScreen)


@pytest.mark.asyncio
async def test_accessible_journal_and_story_map_summaries_open_switch_and_restore_focus(
    mock_app_dependencies,
):
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test(size=(100, 34)) as pilot:
        await _wait_for_pilot(
            pilot,
            lambda: app.engine is not None and app.engine.state.current_node is not None,
        )
        assert app.engine is not None
        assert app.engine.db is not None
        app.engine.db.get_story_tree.return_value = {
            "root_id": "scene-1",
            "nodes": {
                "scene-1": {
                    "narrative": "You awaken in a test dungeon.",
                    "available_choices": ["Go North", "Go South"],
                },
                "scene-2": {
                    "narrative": "You went North.",
                    "available_choices": [],
                },
            },
            "edges": {"scene-1": [{"target_id": "scene-2", "choice": "Go North"}], "scene-2": []},
        }

        await _wait_for_pilot(
            pilot,
            lambda: len(list(app.query_one("#choices-container", Container).query(Button))) >= 2,
        )
        choices = list(app.query_one("#choices-container", Container).query(Button))
        assert app.focused is choices[0]

        await pilot.press("1")
        await pilot.pause(1.0)
        app.action_skip_typewriter()
        await _wait_for_pilot(
            pilot,
            lambda: len(list(app.query_one("#choices-container", Container).query(Button))) >= 1,
        )
        choices = list(app.query_one("#choices-container", Container).query(Button))
        assert app.focused is choices[0]

        await pilot.press("[")
        await pilot.pause(0.2)
        assert isinstance(app.screen, AccessibleSummaryScreen)
        assert (
            "Journal Summary"
            in app.screen.query_one("#accessible-summary-title", Label).render().plain
        )
        assert "Go North" in app.screen._summary_text

        await pilot.press("]")
        await pilot.pause(0.3)
        assert isinstance(app.screen, AccessibleSummaryScreen)
        assert (
            "Story Map Summary"
            in app.screen.query_one("#accessible-summary-title", Label).render().plain
        )
        assert "Choice: Go North" in app.screen._summary_text

        await pilot.press("escape")
        await pilot.pause(0.2)
        assert app.screen.id == "_default"
        assert app.focused is choices[0]


@pytest.mark.asyncio
async def test_game_over_state_and_restart(mock_app_dependencies):
    """Test the game over state ends the choices and 'r' restarts the app."""
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)  # Node 1
        await pilot.press("1")
        await pilot.pause(1.0)  # Node 2
        await pilot.press("1")
        await pilot.pause(1.0)  # Node 3 (Ending)

        assert "You opened the door and escaped!" in app._current_story

        # Verify the choices are replaced with the restart button
        choices_container = app.query_one("#choices-container", Container)
        buttons = list(choices_container.query(Button))
        assert len(buttons) == 1
        assert str(buttons[0].label) == "✦ Start a New Adventure"
        assert buttons[0].id == "btn-new-adventure"

        # Test clicking the restart button
        await pilot.click("#btn-new-adventure")
        await pilot.pause(1.0)  # Back to Node 1

        # Verify reset
        assert app.engine.state.turn_count == 1
        assert "You awaken in a test dungeon." in app._current_story
        assert app.engine.state.inventory == ["Broken Sword"]
        assert app.engine.state.player_stats["health"] == 100
        assert app.engine.state.player_stats["gold"] == 0


@pytest.mark.asyncio
async def test_app_restart_via_keyboard(mock_app_dependencies):
    """Test pressing 'r' forcefully restarts the app at any point."""
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)
        await pilot.press("1")
        await pilot.pause(1.0)  # Node 2 (turn 2)

        assert app.turn_count == 2

        # Press R — now shows confirmation dialog
        await pilot.press("r")
        await pilot.pause(0.2)

        # Confirm the restart
        await pilot.press("y")
        await pilot.pause(1.0)  # Node 1 again

        assert app.engine.state.turn_count == 1
        assert "You awaken in a test dungeon." in app._current_story
        assert app.engine.state.inventory == ["Broken Sword"]
        assert app.engine.state.player_stats["health"] == 100
        assert app.engine.state.player_stats["gold"] == 0

        journal_list = app.query_one("#journal-list", ListView)
        assert len(list(journal_list.children)) == 0


@pytest.mark.asyncio
async def test_app_mount_does_not_duplicate_event_subscriptions(mock_app_dependencies):
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(0.2)

        initial_unsubscribers = len(app._unsubscribers)
        app._subscribe_engine_events()

        assert len(app._unsubscribers) == initial_unsubscribers
        assert bus.subscriber_count(Events.ENGINE_STARTED) == 1
        assert bus.subscriber_count(Events.NODE_COMPLETED) == 1


@pytest.mark.asyncio
async def test_startup_failure_closes_runtime_resources() -> None:
    app = CYOAApp(model_path="dummy_path.gguf")

    generator = MagicMock()
    db = MagicMock()
    db.verify_connectivity_async = AsyncMock(return_value=True)
    engine = MagicMock()
    engine.db = db
    engine.rag.memory.is_online = True
    engine.initialize = AsyncMock(side_effect=RuntimeError("boom"))

    with (
        patch.object(app, "set_timer"),
        patch.object(app, "notify") as notify_mock,
        patch("cyoa.ui.app.ModelBroker", return_value=generator),
        patch("cyoa.ui.app.StoryEngine", return_value=engine),
    ):
        with pytest.raises(WorkerFailed, match="boom"):
            async with app.run_test() as pilot:
                await pilot.pause(0.1)
                app.initialize_and_start("dummy_path.gguf")
                await pilot.pause(0.3)

    notify_mock.assert_called()
    engine.shutdown.assert_called_once_with()
    generator.close.assert_called_once_with()
    assert app.generator is None
    assert app.engine is None


@pytest.mark.asyncio
async def test_quit_during_initial_generation_ignores_late_node() -> None:
    app = CYOAApp(model_path="dummy_path.gguf")

    release_generation = asyncio.Event()
    generator = MagicMock()
    generator.token_budget = 2048
    generator.provider = MagicMock()
    generator.provider.count_tokens = MagicMock(return_value=10)
    generator.update_story_summaries_async = AsyncMock()
    generator.save_state_async = AsyncMock(return_value=None)
    generator.load_state_async = AsyncMock()

    async def delayed_generate(context, *args, **kwargs):
        await release_generation.wait()
        return StoryNode(
            narrative="Late startup node",
            choices=[Choice(text="Continue"), Choice(text="Wait")],
            title="Test Adventure",
        )

    generator.generate_next_node_async = AsyncMock(side_effect=delayed_generate)

    db = MagicMock()
    db.verify_connectivity_async = AsyncMock(return_value=True)
    db.create_story_node_and_get_title.return_value = "Test Adventure"
    db.save_scene_async = AsyncMock(return_value="scene-1")

    with (
        patch("cyoa.ui.app.ModelBroker", return_value=generator),
        patch("cyoa.ui.app.CYOAGraphDB", return_value=db),
    ):
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            app.on_unmount()
            release_generation.set()
            await pilot.pause(0.2)

    assert "Late startup node" not in app._current_story
    generator.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_quit_before_startup_timer_fires_skips_model_creation() -> None:
    app = CYOAApp(model_path="dummy_path.gguf")

    with (
        patch("cyoa.ui.app.ModelBroker") as broker_cls,
        patch("cyoa.ui.app.CYOAGraphDB") as db_cls,
    ):
        async with app.run_test() as pilot:
            await pilot.pause(0.02)
            app.on_unmount()
            await pilot.pause(0.2)

    broker_cls.assert_not_called()
    db_cls.assert_not_called()


@pytest.mark.asyncio
async def test_quit_during_scene_persistence_ignores_late_turn(mock_app_dependencies) -> None:
    app = CYOAApp(model_path="dummy_path.gguf")

    save_reached = asyncio.Event()
    release_save = asyncio.Event()

    with patch.object(app, "speculate_all_choices", MagicMock()):
        async with app.run_test() as pilot:
            await pilot.pause(1.0)
            assert app.engine is not None
            assert app.engine.db is not None

            original_save = app.engine.db.save_scene_async

            async def delayed_save_scene_async(*args: Any, **kwargs: Any) -> str:
                if kwargs.get("choice_text") == "Go North":
                    save_reached.set()
                    await release_save.wait()
                return await original_save(*args, **kwargs)

            with patch.object(
                app.engine.db,
                "save_scene_async",
                AsyncMock(side_effect=delayed_save_scene_async),
            ):
                choice_task = asyncio.create_task(app._trigger_choice(0))
                await asyncio.wait_for(save_reached.wait(), timeout=1.0)

                app.on_unmount()
                release_save.set()
                await asyncio.wait_for(choice_task, timeout=1.0)
                await pilot.pause(0.1)

    assert "You went North." not in app._current_story


@pytest.mark.asyncio
async def test_restart_confirmation_dialog(mock_app_dependencies):
    """Test that pressing 'r' shows a confirmation dialog instead of immediately restarting."""
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)

        # Press 'r' — should show confirm dialog, NOT restart
        await pilot.press("r")
        await pilot.pause(0.2)

        # The ConfirmScreen should be pushed
        assert isinstance(app.screen, ConfirmScreen)

        # Dismiss with 'n' (No) — should return to the game unchanged
        await pilot.press("n")
        await pilot.pause(0.2)
        assert not isinstance(app.screen, ConfirmScreen)
        # Story should still be the original
        assert "You awaken in a test dungeon." in app._current_story


@pytest.mark.asyncio
async def test_quit_confirmation_dialog(mock_app_dependencies):
    """Test that pressing 'q' shows a confirmation dialog instead of immediately quitting."""
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)

        # Press 'q' — should show confirm dialog
        await pilot.press("q")
        await pilot.pause(0.2)

        assert isinstance(app.screen, ConfirmScreen)

        # Cancel via Escape
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert not isinstance(app.screen, ConfirmScreen)


@pytest.mark.asyncio
async def test_help_screen(mock_app_dependencies):
    """Test that pressing 'h' opens the help screen and Escape closes it."""
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)

        await pilot.press("h")
        await pilot.pause(0.2)

        assert isinstance(app.screen, HelpScreen)

        await pilot.press("escape")
        await pilot.pause(0.2)
        assert not isinstance(app.screen, HelpScreen)


@pytest.mark.asyncio
async def test_command_palette_opens_and_launches_settings(mock_app_dependencies):
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)

        await pilot.press("ctrl+shift+p")
        await pilot.pause(0.2)

        assert isinstance(app.screen, CommandPaletteScreen)

        await pilot.press(*tuple("settings"))
        await pilot.press("enter")
        await pilot.pause(0.2)

        assert isinstance(app.screen, SettingsScreen)


@pytest.mark.asyncio
async def test_remapped_help_binding_uses_saved_keymap(mock_app_dependencies):
    with patch(
        "cyoa.ui.app.load_user_config",
        return_value=UserConfig(setup_completed=True, keybindings={"show_help": "f1"}),
    ):
        app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)

        await pilot.press("h")
        await pilot.pause(0.2)
        assert not isinstance(app.screen, HelpScreen)

        await pilot.press("f1")
        await pilot.pause(0.2)
        assert isinstance(app.screen, HelpScreen)


@pytest.mark.asyncio
async def test_notification_history_screen_opens_from_action(mock_app_dependencies):
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)
        app._notification_history = []
        app.notify("A distant bell rings.", severity="information", timeout=2)
        app.action_show_notification_history()
        await pilot.pause(0.2)

        assert isinstance(app.screen, NotificationHistoryScreen)
        entries = [
            label.render().plain for label in app.screen.query("#notification-history-list Label")
        ]
        assert entries == ["1. Information: A distant bell rings."]

        await pilot.press("escape")
        await pilot.pause(0.2)
        assert not isinstance(app.screen, NotificationHistoryScreen)


@pytest.mark.asyncio
async def test_scene_recap_screen_opens_during_live_play(mock_app_dependencies):
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await _wait_for_pilot(
            pilot,
            lambda: app.engine is not None and app.engine.state.current_node is not None,
        )

        await pilot.press("i")
        await pilot.pause(0.2)

        assert isinstance(app.screen, SceneRecapScreen)
        assert "## Scene" in app.screen._recap_text
        assert "You awaken in a test dungeon." in app.screen._recap_text
        assert "1. Go North" in app.screen._recap_text
        assert "2. Go South" in app.screen._recap_text

        await pilot.press("escape")
        await pilot.pause(0.2)
        assert not isinstance(app.screen, SceneRecapScreen)


@pytest.mark.asyncio
async def test_on_unmount_cancels_workers_and_unsubscribes(mock_app_dependencies):
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(0.2)

        app.workers.cancel_group = MagicMock()
        app.workers.cancel_all = MagicMock()
        startup_timer = MagicMock()
        app._startup_timer = startup_timer
        generator = MagicMock()
        engine = MagicMock()
        app.generator = generator
        app.engine = engine

        app.on_unmount()

        assert app._is_shutting_down is True
        startup_timer.stop.assert_called_once_with()
        app.workers.cancel_group.assert_any_call(app, "speculation")
        app.workers.cancel_group.assert_any_call(app, "typewriter")
        app.workers.cancel_all.assert_called_once_with()
        engine.shutdown.assert_called_once_with()
        generator.close.assert_called_once_with()
        assert app.generator is None
        assert app.engine is None
        assert app._unsubscribers == []
        assert bus.subscriber_count(Events.ENGINE_STARTED) == 0


@pytest.mark.asyncio
async def test_late_engine_events_are_ignored_during_shutdown(mock_app_dependencies):
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(0.5)
        app._is_shutting_down = True

        bus.emit(Events.ENGINE_STARTED)
        bus.emit(Events.STATUS_MESSAGE, message="late status")
        bus.emit(Events.ERROR_OCCURRED, error="late error")
        bus.emit(
            Events.NODE_COMPLETED,
            node=StoryNode(
                narrative="Late node",
                choices=[Choice(text="Ignore"), Choice(text="Wait")],
            ),
        )

        await pilot.pause(0.1)

        assert app._current_story != "Late node"


@pytest.mark.asyncio
async def test_typewriter_worker_stops_cleanly_on_shutdown(mock_app_dependencies):
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(0.5)
        app._current_turn_text = ""
        app._current_story = ""
        app._typewriter_queue.put_nowait("queued after shutdown")
        app._is_shutting_down = True

        await pilot.pause(0.2)

        assert app._is_typing is False
        assert app._typewriter_active_chunk == []


@pytest.mark.asyncio
async def test_choice_buttons_have_number_labels(mock_app_dependencies):
    """Test that choice buttons display numbered labels like 1. and 2.."""
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)

        choices_container = app.query_one("#choices-container", Container)
        buttons = list(choices_container.query(Button))

        assert len(buttons) == 2
        assert str(buttons[0].label).startswith("1.")
        assert str(buttons[1].label).startswith("2.")


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(160, 42), (100, 34)])
async def test_choice_buttons_keep_top_and_left_borders(
    size: tuple[int, int], mock_app_dependencies
) -> None:
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test(size=size) as pilot:
        await pilot.pause(1.0)

        buttons = list(app.query_one("#choices-container", Container).query(Button))
        assert buttons

        for button in buttons:
            assert button.styles.border_top[0] != ""
            assert button.styles.border_left[0] != ""


@pytest.mark.asyncio
async def test_undo_restores_previous_state(mock_app_dependencies):
    """Test that pressing 'u' after a choice restores the previous turn state."""
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)  # Node 1
        app.action_skip_typewriter()

        assert app.turn_count == 1
        original_story = app._current_story

        # Make a choice
        await pilot.press("1")
        await pilot.pause(1.0)  # Node 2
        app.action_skip_typewriter()
        assert app.turn_count == 2
        assert "You went North." in app._current_story

        # Undo
        await pilot.press("u")
        await pilot.pause(0.2)

        assert app.turn_count == 1
        assert app._current_story == original_story


@pytest.mark.asyncio
async def test_redo_restores_undone_turn(mock_app_dependencies, tmp_path, monkeypatch):
    from cyoa.core import constants

    monkeypatch.setattr(constants, "SAVES_DIR", str(tmp_path))
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)
        app.action_skip_typewriter()
        await pilot.press("1")
        await pilot.pause(1.0)
        app.action_skip_typewriter()
        redone_story = app._current_story

        await pilot.press("u")
        await pilot.pause(0.2)
        assert app.turn_count == 1

        await pilot.press("y")
        await pilot.pause(0.3)
        app.action_skip_typewriter()
        assert app.turn_count == 2
        assert app._current_story == redone_story


@pytest.mark.asyncio
async def test_undo_with_no_history(mock_app_dependencies):
    """Test that undoing with nothing to undo shows a warning notification."""
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)

        # Press undo with no previous state
        await pilot.press("u")
        await pilot.pause(0.2)

        # Should still be on turn 1
        assert app.turn_count == 1


@pytest.mark.asyncio
async def test_notifications_use_solid_left_border(mock_app_dependencies) -> None:
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test(notifications=True) as pilot:
        await pilot.pause(1.0)
        app.notify("Border regression check", severity="warning", timeout=10)
        await pilot.pause(0.2)

        toast = app.query_one(Toast)
        assert toast.styles.border_left == ("solid", toast.styles.border_left[1])


@pytest.mark.asyncio
async def test_notifications_remain_fully_visible_on_small_terminals(mock_app_dependencies) -> None:
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test(size=(40, 14), notifications=True) as pilot:
        await pilot.pause(1.0)
        app.notify(
            "This is a fairly long notification message to test clipping at small widths",
            severity="warning",
            timeout=10,
        )
        await pilot.pause(0.2)

        toast = app.query_one(Toast)
        _assert_region_within_screen(toast, app.size)


@pytest.mark.asyncio
async def test_notifications_anchor_to_top_right(mock_app_dependencies) -> None:
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test(size=(80, 24), notifications=True) as pilot:
        await pilot.pause(1.0)
        app.notify("Top-right placement check", severity="information", timeout=10)
        await pilot.pause(0.2)

        toast = app.query_one(Toast)
        assert toast.region.y <= 2
        assert toast.region.right >= app.size.width - 2


@pytest.mark.asyncio
async def test_story_container_defaults_to_borderless_surface(mock_app_dependencies) -> None:
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)

        story = app.query_one("#story-container", VerticalScroll)
        assert story.styles.border_top[0] == ""
        assert story.styles.border_right[0] == ""
        assert story.styles.border_bottom[0] == ""
        assert story.styles.border_left[0] == ""


@pytest.mark.asyncio
async def test_story_container_remains_scrollable_in_loading_state(mock_app_dependencies) -> None:
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)

        story = app.query_one("#story-container", VerticalScroll)
        story.add_class("loading-state")
        await pilot.pause(0.1)

        assert str(story.styles.overflow_y) == "auto"


@pytest.mark.asyncio
async def test_startup_accessibility_recommendation_appears_on_narrow_terminal(
    mock_app_dependencies,
) -> None:
    saved_config = UserConfig(setup_completed=True).to_dict()

    def fake_update_user_config(**changes: Any) -> UserConfig:
        saved_config.update(changes)
        return UserConfig.from_dict(saved_config)

    with (
        patch("cyoa.ui.app.load_user_config", return_value=UserConfig(setup_completed=True)),
        patch("cyoa.ui.app.update_user_config", side_effect=fake_update_user_config),
        patch.object(CYOAApp, "_autosave_path", return_value=None),
    ):
        app = CYOAApp(
            model_path="dummy_path.gguf",
            allow_headless_startup_recovery=True,
        )

    async with app.run_test(size=(80, 24)) as pilot:
        await _wait_for_pilot(
            pilot,
            lambda: isinstance(app.screen, StartupAccessibilityRecommendationScreen),
        )

        assert isinstance(app.screen, StartupAccessibilityRecommendationScreen)
        await pilot.click("#btn-startup-accessibility-accept")
        await _wait_for_pilot(
            pilot,
            lambda: (
                not isinstance(app.screen, StartupAccessibilityRecommendationScreen)
                and app.screen_reader_mode
                and app.reduced_motion
            ),
        )

        assert app.screen_reader_mode is True
        assert app.reduced_motion is True


@pytest.mark.asyncio
async def test_startup_accessibility_recommendation_yields_to_explicit_cli_overrides(
    mock_app_dependencies,
) -> None:
    with (
        patch("cyoa.ui.app.load_user_config", return_value=UserConfig(setup_completed=True)),
        patch.object(CYOAApp, "_autosave_path", return_value=None),
    ):
        app = CYOAApp(
            model_path="dummy_path.gguf",
            startup_accessibility_overrides={"high_contrast": True},
        )

    async with app.run_test(size=(80, 24)) as pilot:
        await _wait_for_pilot(
            pilot,
            lambda: app.engine is not None and app.engine.state.current_node is not None,
        )

        assert not isinstance(app.screen, StartupAccessibilityRecommendationScreen)
        assert app.high_contrast_mode is True


@pytest.mark.asyncio
async def test_terminal_capability_fallback_forces_plaintext_accessibility_modes(
    mock_app_dependencies,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    with (
        patch("cyoa.ui.app.load_user_config", return_value=UserConfig(setup_completed=True)),
        patch.object(CYOAApp, "_autosave_path", return_value=None),
    ):
        app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test(size=(140, 38)) as pilot:
        await _wait_for_pilot(
            pilot,
            lambda: app.engine is not None and app.engine.state.current_node is not None,
        )

        assert app._terminal_accessibility_fallback is not None
        assert app.screen_reader_mode is True
        assert app.reduced_motion is True
        assert app.high_contrast_mode is False


@pytest.mark.asyncio
async def test_main_game_layout_fits_standard_terminal(mock_app_dependencies) -> None:
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test(size=(160, 42)) as pilot:
        await pilot.pause(1.0)

        assert app.compact_layout is False

        main_container = app.query_one("#main-container")
        story_container = app.query_one("#story-container", VerticalScroll)
        action_panel = app.query_one("#action-panel", Container)
        status_bar = app.query_one("#status-bar", Container)
        choices_container = app.query_one("#choices-container", Container)

        for widget in (
            main_container,
            story_container,
            action_panel,
            status_bar,
            choices_container,
        ):
            _assert_region_within_screen(widget, app.size)

        assert status_bar.region.x >= action_panel.region.x
        assert status_bar.region.right <= action_panel.region.right
        assert status_bar.region.y >= action_panel.region.y
        assert status_bar.region.bottom <= action_panel.region.bottom
        assert choices_container.region.x >= action_panel.region.x
        assert choices_container.region.right <= action_panel.region.right
        assert choices_container.region.y >= status_bar.region.bottom
        assert choices_container.region.bottom <= action_panel.region.bottom

        for button in choices_container.query(Button):
            _assert_region_within_screen(button, app.size)
            _assert_region_within_parent(button, action_panel)

        status_display = app.query_one("#status-display")
        _assert_region_within_screen(status_display, app.size)
        _assert_region_within_parent(status_display, status_bar)

        for widget in (
            app.query_one("#stats-text"),
            app.query_one("#runtime-text"),
            app.query_one("#inventory-label"),
            app.query_one("#objectives-label"),
            app.query_one("#directives-label"),
        ):
            _assert_region_within_screen(widget, app.size)
            _assert_region_within_parent(widget, status_bar)

        for story_widget in story_container.query(".story-turn"):
            _assert_horizontal_region_within_parent(story_widget, story_container)


@pytest.mark.asyncio
async def test_main_game_layout_fits_compact_terminal(mock_app_dependencies) -> None:
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test(size=(100, 34)) as pilot:
        await pilot.pause(1.0)

        assert app.compact_layout is True

        main_container = app.query_one("#main-container")
        story_container = app.query_one("#story-container", VerticalScroll)
        action_panel = app.query_one("#action-panel", Container)
        status_bar = app.query_one("#status-bar", Container)
        choices_container = app.query_one("#choices-container", Container)

        for widget in (
            main_container,
            story_container,
            action_panel,
            status_bar,
        ):
            _assert_region_within_screen(widget, app.size)

        assert action_panel.region.y >= story_container.region.y
        assert action_panel.region.bottom <= main_container.region.bottom + 1
        assert status_bar.region.right <= main_container.region.right
        assert choices_container.region.right <= main_container.region.right

        buttons = list(choices_container.query(Button))
        assert buttons
        _assert_region_within_screen(buttons[0], app.size)

        for story_widget in story_container.query(".story-turn"):
            _assert_horizontal_region_within_parent(story_widget, story_container)


@pytest.mark.asyncio
async def test_narrow_terminal_rescue_mode_uses_single_column_panel_drawers(
    mock_app_dependencies,
) -> None:
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test(size=(72, 24)) as pilot:
        await _wait_for_pilot(
            pilot,
            lambda: app.engine is not None and app.engine.state.current_node is not None,
        )
        app.action_skip_typewriter()

        assert app.compact_layout is True

        main_container = app.query_one("#main-container")
        journal_panel = app.query_one("#journal-panel", Container)
        map_panel = app.query_one("#story-map-panel", Container)
        action_dock = app.query_one("#action-dock")

        _assert_region_within_screen(action_dock, app.size)
        assert action_dock.region.height > 0
        assert journal_panel.has_class("panel-collapsed")
        assert map_panel.has_class("panel-collapsed")

        app.action_toggle_journal()
        await pilot.pause(0.2)

        assert not journal_panel.has_class("panel-collapsed")
        assert map_panel.has_class("panel-collapsed")
        _assert_region_within_screen(journal_panel, app.size)
        assert journal_panel.region.x <= main_container.region.x + 1
        assert journal_panel.region.right >= main_container.region.right - 1

        app.action_toggle_story_map()
        await pilot.pause(0.2)

        assert journal_panel.has_class("panel-collapsed")
        assert not map_panel.has_class("panel-collapsed")
        _assert_region_within_screen(map_panel, app.size)
        assert map_panel.region.x <= main_container.region.x + 1
        assert map_panel.region.right >= main_container.region.right - 1


@pytest.mark.asyncio
async def test_large_text_reading_preferences_keep_story_help_and_settings_in_bounds(
    mock_app_dependencies,
) -> None:
    with patch(
        "cyoa.ui.app.load_user_config",
        return_value=UserConfig(
            setup_completed=True,
            text_scale="xlarge",
            line_width="focused",
            line_spacing="relaxed",
            typewriter=False,
        ),
    ):
        app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test(size=(100, 34)) as pilot:
        await _wait_for_pilot(
            pilot,
            lambda: app.engine is not None and app.engine.state.current_node is not None,
        )
        app.action_skip_typewriter()

        assert app.has_class("text-scale-xlarge")
        assert app.has_class("line-width-focused")
        assert app.has_class("line-spacing-relaxed")

        story_container = app.query_one("#story-container", VerticalScroll)
        _assert_region_within_screen(story_container, app.size)

        await pilot.press("h")
        await pilot.pause(0.2)
        help_dialog = app.screen.query_one("#help-dialog")
        _assert_region_within_screen(help_dialog, app.size)
        await pilot.press("escape")
        await pilot.pause(0.2)

        await pilot.press("o")
        await pilot.pause(0.2)
        settings_dialog = app.screen.query_one("#settings-dialog")
        _assert_region_within_screen(settings_dialog, app.size)


@pytest.mark.asyncio
async def test_large_text_narrow_terminal_rescue_mode_keeps_core_widgets_in_bounds(
    mock_app_dependencies,
) -> None:
    with patch(
        "cyoa.ui.app.load_user_config",
        return_value=UserConfig(
            setup_completed=True,
            text_scale="xlarge",
            line_width="focused",
            line_spacing="relaxed",
            screen_reader_mode=True,
            typewriter=False,
        ),
    ):
        app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test(size=(72, 24)) as pilot:
        await _wait_for_pilot(
            pilot,
            lambda: app.engine is not None and app.engine.state.current_node is not None,
        )

        assert app.compact_layout is True
        assert app.has_class("text-scale-xlarge")
        assert app.has_class("screen-reader-mode")

        for widget in (
            app.query_one("#main-container"),
            app.query_one("#story-container", VerticalScroll),
            app.query_one("#action-panel", Container),
            app.query_one("#status-bar", Container),
            app.query_one("#choices-container", Container),
            app.query_one("#action-dock"),
        ):
            _assert_region_within_screen(widget, app.size)

        await pilot.click("#btn-compact-journal")
        await pilot.pause(0.2)
        _assert_region_within_screen(app.query_one("#journal-panel", Container), app.size)


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(160, 42), (100, 34)])
async def test_story_entries_and_player_choice_borders_stay_inside_story_pane(
    size: tuple[int, int], mock_app_dependencies
) -> None:
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test(size=size) as pilot:
        await pilot.pause(1.0)
        app.action_skip_typewriter()
        await pilot.press("1")
        await pilot.pause(1.0)
        app.action_skip_typewriter()

        story_container = app.query_one("#story-container", VerticalScroll)

        for story_widget in story_container.query(".story-turn, .player-choice"):
            _assert_horizontal_region_within_parent(story_widget, story_container)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "theme_name",
    ["dark_dungeon", "space_explorer", "haunted_observatory"],
)
async def test_shipped_themes_render_stable_layouts(theme_name: str, mock_app_dependencies) -> None:
    theme = load_theme(theme_name)
    app = CYOAApp(
        model_path="dummy_path.gguf",
        accent_color=theme["accent_color"],
        spinner_frames=theme["spinner_frames"],
        ui_theme=theme["ui"],
    )

    async with app.run_test(size=(140, 38)) as pilot:
        await _wait_for_pilot(
            pilot,
            lambda: "You awaken in a test dungeon" in app._current_story,
            timeout=3.0,
        )
        app.action_skip_typewriter()

        assert "You awaken in a test dungeon." in app._current_story

        main_container = app.query_one("#main-container")
        story_container = app.query_one("#story-container", VerticalScroll)
        action_panel = app.query_one("#action-panel", Container)

        for widget in (main_container, story_container, action_panel):
            _assert_region_within_screen(widget, app.size)

        buttons = list(app.query_one("#choices-container", Container).query(Button))
        assert len(buttons) == 2
        assert app._ui_theme == theme["ui"]


@pytest.mark.asyncio
async def test_modal_dialog_borders_do_not_clip_on_small_terminals(mock_app_dependencies) -> None:
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause(1.0)

        dialogs = [
            (HelpScreen(), "#help-dialog"),
            (ConfirmScreen("Confirm a risky action?"), "#confirm-dialog"),
            (LoadGameScreen(["autosave_slot_1.json"]), "#load-dialog"),
            (StartupChoiceScreen("Resume your last session?"), "#startup-dialog"),
            (TextPromptScreen("Rename bookmark", value="turn-3"), "#text-prompt-dialog"),
            (
                BranchScreen(
                    scenes=[
                        {
                            "narrative": "Opening scene",
                            "available_choices": ["Go North"],
                            "inventory": [],
                        }
                    ],
                    choices=["Go North"],
                ),
                "#branch-dialog",
            ),
        ]

        for screen, selector in dialogs:
            app.push_screen(screen)
            await pilot.pause(0.1)
            dialog = app.screen.query_one(selector)
            _assert_region_within_screen(dialog, app.size)

            for widget in dialog.query("Button, Input, ListView, Tree"):
                _assert_region_within_screen(widget, app.size)
                _assert_region_within_parent(widget, dialog)

            app.pop_screen()
            await pilot.pause(0.1)


@pytest.mark.asyncio
async def test_confirm_screen_supports_keyboard_focus_and_enter(mock_app_dependencies) -> None:
    app = CYOAApp(model_path="dummy_path.gguf")
    result: list[bool] = []

    async with app.run_test() as pilot:
        await pilot.pause(1.0)
        app.push_screen(ConfirmScreen("Quit the current run?"), result.append)
        await pilot.pause(0.2)

        yes_button = app.screen.query_one("#btn-confirm-yes", Button)
        no_button = app.screen.query_one("#btn-confirm-no", Button)
        assert app.focused is yes_button

        await pilot.press("right")
        await pilot.pause(0.1)
        assert app.focused is no_button

        await pilot.press("enter")
        await pilot.pause(0.2)
        assert result == [False]


@pytest.mark.asyncio
async def test_startup_choice_screen_supports_keyboard_focus_and_enter(
    mock_app_dependencies,
) -> None:
    app = CYOAApp(model_path="dummy_path.gguf")
    result: list[str] = []

    async with app.run_test() as pilot:
        await pilot.pause(1.0)
        app.push_screen(StartupChoiceScreen("Resume your last session?"), result.append)
        await pilot.pause(0.2)

        resume_button = app.screen.query_one("#btn-startup-resume", Button)
        new_button = app.screen.query_one("#btn-startup-new", Button)
        assert app.focused is resume_button

        await pilot.press("tab")
        await pilot.pause(0.1)
        assert app.focused is new_button

        await pilot.press("enter")
        await pilot.pause(0.2)
        assert result == ["new"]


@pytest.mark.asyncio
async def test_modal_close_restores_previous_focus_for_help_settings_and_notifications(
    mock_app_dependencies,
) -> None:
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)

        status_display = app.query_one("#status-display", Static)
        story = app.query_one("#story-container", VerticalScroll)
        choice_buttons = list(app.query_one("#choices-container", Container).query(Button))

        status_display.focus()
        await pilot.pause(0.1)
        app.action_show_help()
        await pilot.pause(0.2)
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert app.focused is status_display

        story.focus()
        await pilot.pause(0.1)
        app.action_show_settings()
        await pilot.pause(0.2)
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert app.focused is story

        choice_buttons[1].focus()
        await pilot.pause(0.1)
        app.action_show_notification_history()
        await pilot.pause(0.2)
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert app.focused is choice_buttons[1]


@pytest.mark.asyncio
async def test_nested_modal_close_restores_focus_to_startup_dialog(mock_app_dependencies) -> None:
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)
        app._push_modal_screen(StartupChoiceScreen("Resume your last session?"))
        await pilot.pause(0.2)

        new_button = app.screen.query_one("#btn-startup-new", Button)
        new_button.focus()
        await pilot.pause(0.1)

        app._push_modal_screen(ConfirmScreen("Discard the recovered run?"))
        await _wait_for_pilot(pilot, lambda: isinstance(app.screen, ConfirmScreen))

        await pilot.press("escape")
        await pilot.pause(0.2)
        assert isinstance(app.screen, StartupChoiceScreen)
        assert app.focused is new_button


@pytest.mark.asyncio
async def test_choice_rerender_preserves_story_focus_and_recovers_removed_choice_target(
    mock_app_dependencies,
) -> None:
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)

        story = app.query_one("#story-container", VerticalScroll)
        story.focus()
        await pilot.pause(0.1)

        app.screen_reader_mode = True
        await pilot.pause(0.2)
        assert app.focused is story

        choices_container = app.query_one("#choices-container", Container)
        choice_buttons = list(choices_container.query(Button))
        choice_buttons[1].focus()
        await pilot.pause(0.1)

        focus_target = app._capture_focus_target()
        choices_container.remove_children()
        app._mount_choice_buttons(
            StoryNode(
                narrative="You opened the door and escaped!",
                choices=[],
                is_ending=True,
                title="Test Adventure",
            ),
            choices_container,
            False,
            focus_target=focus_target,
        )
        await pilot.pause(0.2)

        assert app.focused is app.query_one("#btn-new-adventure", Button)


@pytest.mark.asyncio
async def test_save_and_load_game(mock_app_dependencies, tmp_path, monkeypatch):
    """Test saving and loading a game state."""
    from cyoa.core import constants

    monkeypatch.setattr(constants, "SAVES_DIR", str(tmp_path))

    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)  # Node 1
        await pilot.press("1")
        await pilot.pause(1.0)  # Node 2

        assert app.turn_count == 2

        # Save the game
        await pilot.press("s")
        await pilot.pause(1.0)

        # Verify a save file was created
        save_files = [f for f in os.listdir(str(tmp_path)) if f.endswith(".json")]
        assert len(save_files) == 1
        with open(os.path.join(str(tmp_path), save_files[0]), encoding="utf-8") as f:
            payload = json.load(f)
        assert "version" not in payload
        assert "ui_state" in payload
        assert payload["ui_state"]["current_story_text"]
        assert payload["ui_state"]["story_segments"]
        assert len(payload["ui_state"]["journal_entries"]) == 1
        assert payload["ui_state"]["current_turn_text"]
        assert payload["ui_state"]["journal_entries"][0]["entry_kind"] == "choice"
        assert payload["ui_state"]["story_segments"][-1]["kind"] == "story_turn"
        assert payload["ui_state"]["active_turn"] == 2

        # Restart the game
        await pilot.press("r")
        await pilot.pause(0.2)
        await pilot.press("y")
        await pilot.pause(1.0)

        assert app.turn_count == 1


@pytest.mark.asyncio
async def test_initial_scene_does_not_create_autosave(mock_app_dependencies, tmp_path, monkeypatch):
    from cyoa.core import constants

    monkeypatch.setattr(constants, "SAVES_DIR", str(tmp_path))

    app = CYOAApp(model_path="dummy_path.gguf")
    async with app.run_test() as pilot:
        await _wait_for_pilot(
            pilot,
            lambda: app.engine is not None and app.engine.state.current_node is not None,
        )
        await pilot.pause(0.2)
        assert not (tmp_path / "autosave_latest.json").exists()


@pytest.mark.asyncio
async def test_autosave_payload_can_restore_last_session(
    mock_app_dependencies, tmp_path, monkeypatch
):
    from cyoa.core import constants

    monkeypatch.setattr(constants, "SAVES_DIR", str(tmp_path))

    app = CYOAApp(model_path="dummy_path.gguf")
    async with app.run_test() as pilot:
        await pilot.pause(1.0)
        await pilot.press("1")
        await _wait_for_pilot(
            pilot,
            lambda: (
                app.turn_count == 2
                and app.engine is not None
                and app.engine.state.current_node is not None
                and app.engine.state.current_node.narrative == "You went North."
            ),
            timeout=5.0,
        )
        app.action_skip_typewriter()
        autosave_path = tmp_path / "autosave_latest.json"
        assert autosave_path.exists()

    manual_restore_path = tmp_path / "manual-autosave.json"
    autosave_path.rename(manual_restore_path)

    app2 = CYOAApp(model_path="dummy_path.gguf")
    async with app2.run_test() as pilot2:
        await _wait_for_pilot(
            pilot2,
            lambda: app2.engine is not None and app2.engine.state.current_node is not None,
        )
        app2._restore_from_save(str(manual_restore_path))
        await pilot2.pause(0.2)
        app2.action_skip_typewriter()
        assert app2.turn_count == 2
        assert app2.engine is not None
        assert app2.engine.state.last_choice_text == "Go North"


@pytest.mark.asyncio
async def test_startup_offers_new_game_or_resume_when_autosave_exists(
    mock_app_dependencies, tmp_path, monkeypatch
):
    from cyoa.core import constants

    monkeypatch.setattr(constants, "SAVES_DIR", str(tmp_path))

    app = CYOAApp(model_path="dummy_path.gguf")
    async with app.run_test() as pilot:
        await pilot.pause(1.0)
        await pilot.press("1")
        await _wait_for_pilot(pilot, lambda: app.turn_count == 2, timeout=5.0)
        app.action_skip_typewriter()
        autosave_path = tmp_path / "autosave_latest.json"
        assert autosave_path.exists()

    restarted = CYOAApp(model_path="dummy_path.gguf", allow_headless_startup_recovery=True)
    async with restarted.run_test() as pilot3:
        await _wait_for_pilot(
            pilot3,
            lambda: restarted.screen_stack[-1].__class__ is StartupChoiceScreen,
            timeout=2.0,
        )
        restarted.pop_screen()
        await pilot3.pause(0.1)
        restarted._discard_autosave()
        restarted.initialize_and_start(restarted.model_path)
        await _wait_for_pilot(
            pilot3,
            lambda: (
                restarted.turn_count == 1
                and restarted.engine is not None
                and restarted.engine.state.current_node is not None
                and restarted.engine.state.current_node.narrative == "You awaken in a test dungeon."
            ),
            timeout=5.0,
        )
        assert not autosave_path.exists()


@pytest.mark.asyncio
async def test_startup_new_game_requires_confirmation_before_discarding_autosave(
    mock_app_dependencies, tmp_path, monkeypatch
):
    from cyoa.core import constants

    monkeypatch.setattr(constants, "SAVES_DIR", str(tmp_path))

    app = CYOAApp(model_path="dummy_path.gguf")
    async with app.run_test() as pilot:
        await pilot.pause(1.0)
        await pilot.press("1")
        await _wait_for_pilot(pilot, lambda: app.turn_count == 2, timeout=5.0)
        app.action_skip_typewriter()
        autosave_path = tmp_path / "autosave_latest.json"
        assert autosave_path.exists()

    restarted = CYOAApp(model_path="dummy_path.gguf", allow_headless_startup_recovery=True)
    async with restarted.run_test() as pilot2:
        await _wait_for_pilot(
            pilot2,
            lambda: restarted.screen_stack[-1].__class__ is StartupChoiceScreen,
            timeout=2.0,
        )

        await pilot2.press("n")
        await _wait_for_pilot(pilot2, lambda: isinstance(restarted.screen, ConfirmScreen))
        assert autosave_path.exists()

        await pilot2.press("y")
        await _wait_for_pilot(
            pilot2,
            lambda: (
                restarted.turn_count == 1
                and restarted.engine is not None
                and restarted.engine.state.current_node is not None
                and restarted.engine.state.current_node.narrative == "You awaken in a test dungeon."
            ),
            timeout=5.0,
        )
        assert not autosave_path.exists()


@pytest.mark.asyncio
async def test_export_story_writes_markdown_accessible_text_and_timeline_json(
    mock_app_dependencies, tmp_path, monkeypatch
):
    from cyoa.core import constants

    monkeypatch.setattr(constants, "SAVES_DIR", str(tmp_path))

    app = CYOAApp(model_path="dummy_path.gguf")
    async with app.run_test() as pilot:
        await pilot.pause(1.0)
        await pilot.press("1")
        await pilot.pause(1.0)
        await pilot.press("e")
        await _wait_for_pilot(pilot, lambda: len(list((tmp_path / "exports").glob("*.md"))) == 1)

        markdown_files = list((tmp_path / "exports").glob("*.md"))
        accessible_files = list((tmp_path / "exports").glob("*.accessible.txt"))
        json_files = list((tmp_path / "exports").glob("*.timeline.json"))
        assert len(markdown_files) == 1
        assert len(accessible_files) == 1
        assert len(json_files) == 1
        assert "## Story" in markdown_files[0].read_text(encoding="utf-8")
        accessible_text = accessible_files[0].read_text(encoding="utf-8")
        assert "Transcript:" in accessible_text
        assert "Choice: Go North" in accessible_text
        assert "---" not in accessible_text
        timeline = json.loads(json_files[0].read_text(encoding="utf-8"))
        assert timeline["turn_count"] == 2
        assert timeline["story_segments"]


@pytest.mark.asyncio
async def test_accessibility_matrix_export_supports_combined_mode(
    mock_app_dependencies, tmp_path, monkeypatch
) -> None:
    from cyoa.core import constants

    monkeypatch.setattr(constants, "SAVES_DIR", str(tmp_path))
    app = _app_with_accessibility_config(
        screen_reader_mode=True,
        reduced_motion=True,
        cognitive_load_reduction_mode=True,
        typewriter=False,
    )

    async with app.run_test() as pilot:
        await _wait_for_pilot(
            pilot,
            lambda: app.engine is not None and app.engine.state.current_node is not None,
        )
        await _wait_for_pilot(
            pilot,
            lambda: len(list(app.query_one("#choices-container", Container).query(Button))) > 0,
        )
        first_choice = list(app.query_one("#choices-container", Container).query(Button))[0]
        first_choice.focus()
        await pilot.pause(0.1)
        await pilot.press("enter")
        await _wait_for_pilot(pilot, lambda: app.turn_count == 2, timeout=5.0)
        await pilot.press("e")
        await _wait_for_pilot(
            pilot,
            lambda: len(list((tmp_path / "exports").glob("*.accessible.txt"))) == 1,
        )

        accessible_file = next((tmp_path / "exports").glob("*.accessible.txt"))
        transcript = accessible_file.read_text(encoding="utf-8")
        assert "Transcript:" in transcript
        assert "Choice: Go North" in transcript
        assert "Current Progress:" in transcript
        assert "**" not in transcript
        assert "---" not in transcript


@pytest.mark.asyncio
async def test_accessibility_diagnostics_snapshot_exports_redacted_runtime_state(
    mock_app_dependencies,
    tmp_path,
) -> None:
    with (
        patch(
            "cyoa.ui.app.load_user_config",
            return_value=UserConfig(
                setup_completed=True,
                screen_reader_mode=True,
                reduced_motion=True,
                high_contrast=True,
                text_scale="xlarge",
                diagnostics_enabled=True,
            ),
        ),
        patch.object(CYOAApp, "_autosave_path", return_value=None),
    ):
        app = CYOAApp(model_path="dummy_path.gguf")

    target = tmp_path / "accessibility_snapshot.json"
    async with app.run_test(size=(72, 24)) as pilot:
        await _wait_for_pilot(
            pilot,
            lambda: app.engine is not None and app.engine.state.current_node is not None,
        )
        await _wait_for_pilot(
            pilot,
            lambda: len(list(app.query_one("#choices-container", Container).query(Button))) > 0,
        )
        first_choice = list(app.query_one("#choices-container", Container).query(Button))[0]
        first_choice.focus()
        await pilot.pause(0.1)

        snapshot_path = app.export_accessibility_diagnostics_snapshot(path=target)
        snapshot = json.loads(target.read_text(encoding="utf-8"))

        assert snapshot_path == target
        assert snapshot["settings"]["screen_reader_mode"] is True
        assert snapshot["settings"]["high_contrast"] is True
        assert snapshot["layout"]["compact_layout"] is True
        assert snapshot["focus"]["focused_widget"]["type"] == "Button"
        assert snapshot["bindings"]["show_help"] == "h"
        assert snapshot["story"]["included"] is False
        assert "current_story_text" not in snapshot["story"]


@pytest.mark.asyncio
async def test_directive_editor_updates_story_context_and_status(
    mock_app_dependencies, tmp_path, monkeypatch
):
    from cyoa.core import constants

    monkeypatch.setattr(constants, "SAVES_DIR", str(tmp_path))
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)
        await pilot.press("x")
        await pilot.pause(0.1)
        prompt_screen = cast(Any, app.screen)
        prompt_screen.query_one("#text-prompt-input", Input).value = "No combat, Stealth"
        prompt_screen.action_submit()
        await pilot.pause(0.2)

        assert app.engine is not None
        assert app.engine.story_context is not None
        assert app.engine.story_context.directives == ["No combat", "Stealth"]
        assert "No combat" in app.query_one("#directives-label", Label).render().plain


@pytest.mark.asyncio
async def test_save_game_succeeds_before_story_title_is_persisted(
    mock_app_dependencies, tmp_path, monkeypatch
):
    """Saving should not fail if the current node exists before title generation settles."""
    from cyoa.core import constants

    monkeypatch.setattr(constants, "SAVES_DIR", str(tmp_path))

    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await _wait_for_pilot(
            pilot,
            lambda: app.engine is not None and app.engine.state.current_node is not None,
        )

        assert app.engine is not None
        app.engine.state.story_title = None
        app.engine.state.current_node.title = "Recovered Save Title"

        await pilot.press("s")
        await _wait_for_pilot(
            pilot,
            lambda: len([f for f in os.listdir(str(tmp_path)) if f.endswith(".json")]) == 1,
        )

        save_files = [f for f in os.listdir(str(tmp_path)) if f.endswith(".json")]
        assert save_files == ["Recovered Save Title_turn1.json"]

        with open(os.path.join(str(tmp_path), save_files[0]), encoding="utf-8") as f:
            payload = json.load(f)
        assert payload["story_title"] == "Recovered Save Title"


@pytest.mark.asyncio
async def test_save_game_prompts_before_overwriting_existing_file(
    mock_app_dependencies, tmp_path, monkeypatch
):
    from cyoa.core import constants

    monkeypatch.setattr(constants, "SAVES_DIR", str(tmp_path))

    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await _wait_for_pilot(
            pilot,
            lambda: app.engine is not None and app.engine.state.current_node is not None,
        )

        await pilot.press("s")
        await _wait_for_pilot(
            pilot,
            lambda: len([f for f in os.listdir(str(tmp_path)) if f.endswith(".json")]) == 1,
        )

        await pilot.press("s")
        await _wait_for_pilot(pilot, lambda: isinstance(app.screen, ConfirmScreen))

        message = app.screen.query_one("#confirm-message", Label).render().plain
        assert "Overwrite existing save?" in message
        assert "already exists for this turn" in message


@pytest.mark.asyncio
async def test_save_load_preserves_restore_points_and_allows_restoring_them(
    mock_app_dependencies, tmp_path, monkeypatch
):
    from cyoa.core import constants

    monkeypatch.setattr(constants, "SAVES_DIR", str(tmp_path))

    app = CYOAApp(model_path="dummy_path.gguf")
    async with app.run_test() as pilot:
        await _wait_for_pilot(
            pilot,
            lambda: app.engine is not None and app.engine.state.current_node is not None,
        )
        await pilot.pause(1.0)
        app.action_skip_typewriter()
        app._bookmark_payloads["Turn 1"] = app._build_save_payload(
            app,
            app,
            include_restore_points=False,
        )

        await pilot.press("1")
        await _wait_for_pilot(
            pilot,
            lambda: (
                app.turn_count == 2
                and app.engine is not None
                and app.engine.state.current_node is not None
                and app.engine.state.current_node.narrative == "You went North."
            ),
            timeout=5.0,
        )
        app.action_skip_typewriter()
        app._bookmark_payloads["Turn 2"] = app._build_save_payload(
            app,
            app,
            include_restore_points=False,
        )

        await pilot.press("s")
        await _wait_for_pilot(
            pilot,
            lambda: len([f for f in os.listdir(str(tmp_path)) if f.endswith(".json")]) == 1,
        )

    save_files = PersistenceMixin._list_manual_save_files()
    assert len(save_files) == 1
    save_path = os.path.join(str(tmp_path), save_files[0])

    app2 = CYOAApp(model_path="dummy_path.gguf")
    async with app2.run_test() as pilot2:
        await _wait_for_pilot(
            pilot2,
            lambda: app2.engine is not None and app2.engine.state.current_node is not None,
        )

        app2._restore_from_save(save_path)
        await pilot2.pause(0.2)

        assert sorted(app2._bookmark_payloads) == ["Turn 1", "Turn 2"]

        app2._restore_from_payload(
            app2._bookmark_payloads["Turn 1"],
            source_label="Restored restore point Turn 1",
            preserve_restore_points=True,
        )
        await pilot2.pause(0.2)

        assert app2.turn_count == 1
        assert app2.engine is not None
        assert app2.engine.state.current_node is not None
        assert app2.engine.state.current_node.narrative == "You awaken in a test dungeon."
        assert sorted(app2._bookmark_payloads) == ["Turn 1", "Turn 2"]


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_full_save_load_lifecycle(mock_app_dependencies, tmp_path, monkeypatch):
    """Test that saving and then loading a game correctly restores all relevant state."""
    from cyoa.core import constants

    monkeypatch.setattr(constants, "SAVES_DIR", str(tmp_path))

    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await _wait_for_pilot(
            pilot,
            lambda: app.engine is not None and app.engine.state.current_node is not None,
        )

        # Set some unique state
        from cyoa.core.models import Choice, StoryNode

        app.engine.state.inventory = ["Unique Item 1", "Unique Item 2"]
        app.engine.state.player_stats = {"health": 88, "gold": 123, "reputation": 5}
        app.engine.state.turn_count = 5
        node = StoryNode(
            narrative="A unique story begins.",
            choices=[Choice(text="Continue"), Choice(text="Quit")],
            title="Test Adventure",
        )
        app.engine.state.current_node = node
        app._current_story = "Previous history.\n\n---\n\n" + node.narrative
        app._story_segments = [{"kind": "story_turn", "text": app._current_story}]

        # Save
        await pilot.press("s")
        await _wait_for_pilot(
            pilot,
            lambda: len([f for f in os.listdir(str(tmp_path)) if f.endswith(".json")]) == 1,
        )

        # Create a new app instance to simulate loading fresh
        app2 = CYOAApp(model_path="dummy_path.gguf")
        async with app2.run_test() as pilot2:
            await _wait_for_pilot(
                pilot2,
                lambda: app2.engine is not None and app2.engine.state.current_node is not None,
            )

            # Find the save file
            save_files = PersistenceMixin._list_manual_save_files()
            assert len(save_files) == 1
            save_path = os.path.join(str(tmp_path), save_files[0])

            # Load it into the second app
            app2._restore_from_save(save_path)

            # Verify restoration
            assert app2.engine.state.turn_count == 5
            assert app2.engine.state.inventory == ["Unique Item 1", "Unique Item 2"]
            assert app2.engine.state.player_stats["health"] == 88
            assert app2.engine.state.player_stats["gold"] == 123
            # After restore, _current_story should contain the narrative
            assert "unique story" in app2._current_story
            assert app2._current_turn_text == "A unique story begins."
            story_turns = list(app2.query_one("#story-container").query(".story-turn"))
            assert len(story_turns) == 1
            health_value_text = app2.query_one("#health-value").render().plain
            assert "88%" in health_value_text


def test_list_manual_save_files_excludes_autosave(tmp_path, monkeypatch) -> None:
    from cyoa.core import constants

    monkeypatch.setattr(constants, "SAVES_DIR", str(tmp_path))
    (tmp_path / "autosave_latest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "manual_turn2.json").write_text("{}", encoding="utf-8")

    assert PersistenceMixin._list_manual_save_files() == ["manual_turn2.json"]


@pytest.mark.asyncio
async def test_restore_from_save_handles_malformed_ui_state(mock_app_dependencies, tmp_path):
    save_path = tmp_path / "broken-ui-save.json"
    save_path.write_text(
        json.dumps(
            {
                "starting_prompt": "Start",
                "context_history": [],
                "turn_count": 2,
                "inventory": ["Torch"],
                "player_stats": {"health": 77, "gold": 3, "reputation": 1},
                "current_node": {
                    "narrative": "Recovered scene",
                    "choices": [{"text": "Continue"}, {"text": "Wait"}],
                },
                "ui_state": {
                    "current_story_text": 99,
                    "current_turn_text": None,
                    "journal_entries": [
                        "bad",
                        {"label": 55, "scene_index": "oops", "entry_kind": 8},
                    ],
                    "journal_panel_collapsed": False,
                    "story_map_panel_collapsed": "invalid",
                },
            }
        ),
        encoding="utf-8",
    )

    app = CYOAApp(model_path="dummy_path.gguf")
    async with app.run_test() as pilot:
        await pilot.pause(1.0)

        app._restore_from_save(str(save_path))
        await pilot.pause(0.1)

        assert app._current_story
        assert app._current_story == app._current_turn_text
        journal_panel = app.query_one("#journal-panel", Container)
        map_panel = app.query_one("#story-map-panel", Container)
        assert not journal_panel.has_class("panel-collapsed")
        assert map_panel.has_class("panel-collapsed")
        journal_items = list(app.query_one("#journal-list", ListView).children)
        assert len(journal_items) == 1
        assert "Unknown Turn" in journal_items[0].query_one(Label).render().plain


@pytest.mark.asyncio
async def test_restore_from_save_tolerates_missing_story_map_panel(
    mock_app_dependencies, tmp_path, monkeypatch: pytest.MonkeyPatch
):
    save_path = tmp_path / "missing-story-map-save.json"
    save_path.write_text(
        json.dumps(
            {
                "starting_prompt": "Start",
                "context_history": [],
                "story_title": "Recovered Adventure",
                "turn_count": 2,
                "inventory": ["Torch"],
                "player_stats": {"health": 77, "gold": 3, "reputation": 1},
                "current_node": {
                    "narrative": "Recovered scene",
                    "choices": [{"text": "Continue"}, {"text": "Wait"}],
                },
                "ui_state": {
                    "current_story_text": "Recovered scene",
                    "current_turn_text": "Recovered scene",
                    "journal_entries": [],
                    "story_map_panel_collapsed": False,
                },
            }
        ),
        encoding="utf-8",
    )

    app = CYOAApp(model_path="dummy_path.gguf")
    async with app.run_test() as pilot:
        await pilot.pause(1.0)

        original_query_one = app.query_one

        def query_one_without_story_map(*args: Any, **kwargs: Any) -> Any:
            selector = args[0] if args else None
            if selector == "#story-map-panel":
                raise NoMatches("No nodes match '#story-map-panel' on Screen(id='_default')")
            return original_query_one(*args, **kwargs)

        monkeypatch.setattr(app, "query_one", query_one_without_story_map)

        app._restore_from_save(str(save_path))
        await pilot.pause(0.1)

        assert app._current_story == "Recovered scene"
        assert app.engine is not None
        assert app.engine.state.turn_count == 2


@pytest.mark.asyncio
async def test_restore_from_save_rebuilds_story_timeline(mock_app_dependencies, tmp_path):
    save_path = tmp_path / "timeline-save.json"
    save_path.write_text(
        json.dumps(
            {
                "starting_prompt": "Start",
                "context_history": [],
                "story_title": "Timeline Adventure",
                "turn_count": 3,
                "inventory": ["Compass"],
                "player_stats": {"health": 91, "gold": 4, "reputation": 2},
                "current_node": {
                    "narrative": "You return to the crossroads.",
                    "choices": [{"text": "Take the east road"}, {"text": "Camp"}],
                },
                "timeline_metadata": [
                    {
                        "kind": "branch_restore",
                        "source_scene_id": "scene-3",
                        "target_scene_id": "scene-1",
                        "restored_turn": 2,
                    }
                ],
                "ui_state": {
                    "current_story_text": "Opening scene.\n\n> **You chose:** Go North\n\n---\n\nNorth path.",
                    "current_turn_text": "You return to the crossroads.",
                    "story_segments": [
                        {"kind": "story_turn", "text": "Opening scene."},
                        {"kind": "player_choice", "text": "**You chose:** Go North"},
                        {"kind": "story_turn", "text": "North path."},
                        {
                            "kind": "branch_marker",
                            "text": "**[Time fractures... you return to Turn 2]**",
                        },
                        {"kind": "story_turn", "text": "You return to the crossroads."},
                    ],
                    "journal_entries": [
                        {"label": "Turn 1: Go North", "scene_index": 0, "entry_kind": "choice"},
                        {
                            "label": "Timeline fracture → resumed from Turn 2",
                            "scene_index": 1,
                            "entry_kind": "branch",
                        },
                    ],
                    "journal_panel_collapsed": False,
                    "story_map_panel_collapsed": False,
                },
            }
        ),
        encoding="utf-8",
    )

    app = CYOAApp(model_path="dummy_path.gguf")
    async with app.run_test() as pilot:
        await pilot.pause(1.0)

        app._restore_from_save(str(save_path))
        await pilot.pause(0.1)

        story_widgets = list(app.query_one("#story-container").query(Markdown))
        assert len(story_widgets) == 5
        assert app._story_segments == [
            {"kind": "story_turn", "text": "Opening scene."},
            {"kind": "player_choice", "text": "**You chose:** Go North"},
            {"kind": "story_turn", "text": "North path."},
            {"kind": "branch_marker", "text": "**[Time fractures... you return to Turn 2]**"},
            {"kind": "story_turn", "text": "You return to the crossroads."},
        ]
        story_turns = list(app.query_one("#story-container").query(".story-turn"))
        assert len(story_turns) == 3
        assert story_turns[-1].has_class("current-turn")
        assert not story_turns[-1].has_class("archived-turn")
        assert all(turn.has_class("archived-turn") for turn in story_turns[:-1])

        choice_widgets = list(app.query_one("#story-container").query(".player-choice"))
        assert len(choice_widgets) == 2
        assert choice_widgets[-1].has_class("latest-choice")
        assert choice_widgets[0].has_class("archived-choice")
        assert app.engine.state.timeline_metadata == [
            {
                "kind": "branch_restore",
                "source_scene_id": "scene-3",
                "target_scene_id": "scene-1",
                "restored_turn": 2,
            }
        ]


@pytest.mark.asyncio
async def test_restore_from_save_accepts_partial_payload(mock_app_dependencies, tmp_path):
    save_path = tmp_path / "partial-save.json"
    save_path.write_text(
        json.dumps(
            {
                "story_title": "Partial Adventure",
                "current_node": {
                    "narrative": "Partial scene",
                    "choices": [{"text": "Push on"}, {"text": "Hide"}],
                },
            }
        ),
        encoding="utf-8",
    )

    app = CYOAApp(model_path="dummy_path.gguf")
    async with app.run_test() as pilot:
        await pilot.pause(1.0)

        app._restore_from_save(str(save_path))
        await pilot.pause(0.1)

        assert app.engine.state.story_title == "Partial Adventure"
        assert app.engine.state.turn_count == 1
        assert app.turn_count == 1
        assert app.engine.state.inventory == []
        assert app._current_story
        buttons = list(app.query_one("#choices-container", Container).query(Button))
        assert len(buttons) == 2


@pytest.mark.asyncio
async def test_restore_from_save_ignores_malformed_story_segments(mock_app_dependencies, tmp_path):
    save_path = tmp_path / "bad-story-segments.json"
    save_path.write_text(
        json.dumps(
            {
                "story_title": "Broken Timeline",
                "current_node": {
                    "narrative": "Recovered ending",
                    "choices": [{"text": "Continue"}],
                },
                "ui_state": {
                    "current_story_text": "Recovered history",
                    "current_turn_text": "Recovered ending",
                    "story_segments": [
                        {"kind": "story_turn", "text": 99},
                        {"kind": "bad", "text": "ignored"},
                        "oops",
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    app = CYOAApp(model_path="dummy_path.gguf")
    async with app.run_test() as pilot:
        await pilot.pause(1.0)

        app._restore_from_save(str(save_path))
        await pilot.pause(0.1)

        story_turns = list(app.query_one("#story-container").query(".story-turn"))
        assert len(story_turns) == 1
        assert app._current_turn_text == "Recovered ending"
        assert app._story_segments == [{"kind": "story_turn", "text": "Recovered ending"}]


@pytest.mark.asyncio
async def test_restore_from_save_does_not_rebuild_branch_context_without_story_segments(
    mock_app_dependencies, tmp_path
):
    save_path = tmp_path / "legacy-branch-save.json"
    save_path.write_text(
        json.dumps(
            {
                "story_title": "Legacy Branch Adventure",
                "turn_count": 4,
                "timeline_metadata": [
                    {
                        "kind": "branch_restore",
                        "source_scene_id": "scene-8",
                        "target_scene_id": "scene-2",
                        "restored_turn": 2,
                    }
                ],
                "current_node": {
                    "narrative": "You return to the crossroads.",
                    "choices": [{"text": "Take the east road"}, {"text": "Camp"}],
                },
                "ui_state": {
                    "current_story_text": "Opening scene.\n\nNorth path.\n\nYou return to the crossroads.",
                    "current_turn_text": "You return to the crossroads.",
                    "journal_entries": [],
                    "journal_panel_collapsed": False,
                },
            }
        ),
        encoding="utf-8",
    )

    app = CYOAApp(model_path="dummy_path.gguf")
    async with app.run_test() as pilot:
        await pilot.pause(1.0)

        app._restore_from_save(str(save_path))
        await pilot.pause(0.1)

        assert app.turn_count == 4
        assert app._story_segments == [
            {"kind": "story_turn", "text": "You return to the crossroads."},
        ]
        journal_items = list(app.query_one("#journal-list", ListView).children)
        assert len(journal_items) == 0


@pytest.mark.asyncio
async def test_restore_from_save_ignores_branch_state_without_structured_timeline(
    mock_app_dependencies, tmp_path
):
    save_path = tmp_path / "bad-branch-state-save.json"
    save_path.write_text(
        json.dumps(
            {
                "story_title": "Malformed Branch Adventure",
                "turn_count": 3,
                "timeline_metadata": [
                    {
                        "kind": "branch_restore",
                        "source_scene_id": "scene-5",
                        "target_scene_id": "scene-1",
                        "restored_turn": "3",
                    }
                ],
                "current_node": {
                    "narrative": "Recovered branch scene",
                    "choices": [{"text": "Continue"}, {"text": "Wait"}],
                },
                "ui_state": {
                    "current_story_text": "Recovered branch scene",
                    "current_turn_text": "Recovered branch scene",
                    "branch_state": {"restored_turn": 99},
                },
            }
        ),
        encoding="utf-8",
    )

    app = CYOAApp(model_path="dummy_path.gguf")
    async with app.run_test() as pilot:
        await pilot.pause(1.0)

        app._restore_from_save(str(save_path))
        await pilot.pause(0.1)

        assert app.turn_count == 3
        assert app._story_segments == [
            {"kind": "story_turn", "text": "Recovered branch scene"},
        ]


@pytest.mark.asyncio
async def test_branch_journal_entries_track_rendered_branch_turn(mock_app_dependencies):
    app = CYOAApp(model_path="dummy_path.gguf")

    history = {
        "scenes": [
            {
                "id": "scene-1",
                "narrative": "You awaken in a test dungeon.",
                "available_choices": ["Go North", "Go South"],
                "inventory": ["Broken Sword"],
                "player_stats": {"health": 100, "gold": 0, "reputation": 0},
            },
            {
                "id": "scene-2",
                "narrative": "You went North.",
                "available_choices": ["Open Door", "Go Back"],
                "inventory": ["Broken Sword", "Health Potion"],
                "player_stats": {"health": 90, "gold": 50, "reputation": 0},
            },
        ],
        "choices": ["Go North"],
    }

    async with app.run_test() as pilot:
        await pilot.pause(1.0)
        app.action_skip_typewriter()

        await pilot.press("1")
        await pilot.pause(1.0)
        app.action_skip_typewriter()

        app.restore_to_scene(idx=1, history=history)
        await pilot.pause(0.3)

        journal_items = list(app.query_one("#journal-list", ListView).children)
        assert len(journal_items) == 2
        branch_item = journal_items[-1]
        assert "Timeline fracture" in branch_item.query_one(Label).render().plain
        assert branch_item.scene_index == 2

        app._jump_to_story_turn(branch_item.scene_index)
        await pilot.pause(0.1)

        story_turns = list(app.query_one("#story-container").query(".story-turn"))
        assert len(story_turns) == 3
        assert app._current_turn_widget is story_turns[2]

        await pilot.press("1")
        await pilot.pause(1.0)
        app.action_skip_typewriter()

        journal_items = list(app.query_one("#journal-list", ListView).children)
        latest_choice = journal_items[-1]
        assert latest_choice.scene_index == 2


def test_branch_screen_scene_preview_includes_choice_and_state_metadata() -> None:
    preview = BranchScreen._build_scene_preview(
        {
            "narrative": "You return to the crossroads beneath a broken moon and count the paths ahead.",
            "available_choices": ["Take the east road", "Camp", "Scout"],
            "inventory": ["Compass", "Torch"],
        },
        turn_index=1,
        choice_text="Take the east road",
    )

    assert "Turn 2" in preview
    assert "Next choice: Take the east road" in preview
    assert "3 future path(s)" in preview
    assert "2 item(s) carried" in preview


def test_story_map_label_marks_branch_restore_turns() -> None:
    label = NavigationMixin._format_story_map_label(
        scene_id="scene-2",
        narrative="You return to the crossroads.",
        mood="heroic",
        current_scene_id="scene-3",
        branch_targets={"scene-2": [2, 2, 4]},
        turn=2,
        depth=1,
        is_ending=False,
    )

    assert "[H]" in label
    assert "⟲ T2, 4" in label


def test_story_map_label_marks_current_scene_restore_turns() -> None:
    label = NavigationMixin._format_story_map_label(
        scene_id="scene-2",
        narrative="You return to the crossroads.",
        mood="heroic",
        current_scene_id="scene-2",
        branch_targets={"scene-2": [2]},
        turn=2,
        depth=1,
        is_ending=False,
    )

    assert "[reverse]" in label
    assert "⟲ T2" in label


@pytest.mark.asyncio
async def test_story_map_queries_are_cached_per_scene(mock_app_dependencies) -> None:
    app = CYOAApp(model_path="dummy_path.gguf")

    tree_payload = {
        "root_id": "scene-1",
        "nodes": {
            "scene-1": {"id": "scene-1", "narrative": "Opening scene", "mood": "default"},
            "scene-2": {"id": "scene-2", "narrative": "North path", "mood": "heroic"},
        },
        "edges": {"scene-1": [{"target_id": "scene-2", "choice": "Go North"}], "scene-2": []},
    }

    async with app.run_test() as pilot:
        await pilot.pause(1.0)
        assert app.engine is not None
        assert app.engine.db is not None
        get_story_tree = cast(MagicMock, app.engine.db.get_story_tree)
        get_story_tree.return_value = tree_payload

        app.update_story_map()
        await pilot.pause(0.2)
        app.update_story_map()
        await pilot.pause(0.2)

        assert get_story_tree.call_count == 1

        app.engine.state.current_scene_id = "scene-2"
        app.update_story_map()
        await pilot.pause(0.2)

        assert get_story_tree.call_count == 2


@pytest.mark.asyncio
async def test_branch_history_queries_are_cached_per_scene(mock_app_dependencies) -> None:
    app = CYOAApp(model_path="dummy_path.gguf")
    history = {
        "scenes": [
            {
                "id": "scene-1",
                "narrative": "Opening scene",
                "available_choices": ["Go North"],
                "inventory": [],
                "player_stats": {"health": 100, "gold": 0, "reputation": 0},
            }
        ],
        "choices": [],
    }

    async with app.run_test() as pilot:
        await pilot.pause(1.0)
        assert app.engine is not None
        assert app.engine.db is not None
        get_scene_history_path = cast(MagicMock, app.engine.db.get_scene_history_path)
        get_scene_history_path.return_value = history

        with patch.object(app, "push_screen", MagicMock()):
            app.action_branch_past()
            await pilot.pause(0.2)
            app.action_branch_past()
            await pilot.pause(0.2)

            assert get_scene_history_path.call_count == 1

            app.engine.state.current_scene_id = "scene-2"
            get_scene_history_path.return_value = history | {
                "scenes": [{**history["scenes"][0], "id": "scene-2"}]
            }
            app.action_branch_past()
            await pilot.pause(0.2)

            assert get_scene_history_path.call_count == 2


@pytest.mark.asyncio
async def test_status_notifications_are_batched(mock_app_dependencies) -> None:
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)
        dispatched: list[str] = []

        def capture_dispatch(
            message: str,
            *,
            title: str,
            severity: str,
            timeout: float | None,
            markup: bool,
            update_latest: bool,
        ) -> None:
            dispatched.append(message)

        with patch.object(app, "_dispatch_notification", side_effect=capture_dispatch):
            bus.emit(Events.STATUS_MESSAGE, message="⚡ Weaving possible futures...")
            bus.emit(Events.STATUS_MESSAGE, message="📜 Archiving old chapters...")
            await pilot.pause(0.3)

            assert dispatched == [
                "Information: ⚡ Weaving possible futures... | 📜 Archiving old chapters..."
            ]
            message = dispatched[0]
            assert "⚡ Weaving possible futures..." in message
            assert "📜 Archiving old chapters..." in message
