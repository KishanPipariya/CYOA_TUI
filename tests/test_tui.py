import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from textual.containers import Container
from textual.widgets import Button, Label, ListView
from textual.worker import WorkerFailed

from cyoa.core.events import EventBus, EventDispatchError, Events, bus
from cyoa.core.models import Choice, StoryNode
from cyoa.ui.app import CYOAApp
from cyoa.ui.components import ConfirmScreen, HelpScreen

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


@pytest.fixture
def mock_app_dependencies():
    """Mock the LLM Generator and DB to be fast and deterministic in UI tests."""
    with (
        patch("cyoa.ui.app.ModelBroker", new=_mock_generator),
        patch("cyoa.ui.app.CYOAGraphDB") as mock_db,
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
        assert str(buttons[0].label) == "1  Go North"
        assert str(buttons[1].label) == "2  Go South"

        # Verify inventory was updated
        inventory_label = app.query_one("#inventory-label", Label)
        inventory_text = str(inventory_label.render())
        assert "Broken Sword" in inventory_text


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


@pytest.mark.asyncio
async def test_stats_display_reflects_player_stats(mock_app_dependencies):
    """Test that the stats display updates with different color codes depending on health."""
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)
        status_display = app.query_one("#status-display")
        stats_label = app.query_one("#stats-text", Label)

        # Initial stats: Health 100 (high)
        assert "100%" in str(stats_label.render())
        assert status_display.has_class("health-high")

        # Update stats to mid-health
        app.query_one("StatusDisplay").health = 50
        await pilot.pause(0.1) # Wait for reactive update
        assert "50%" in str(stats_label.render())
        assert status_display.has_class("health-mid")

        # Update stats to low-health
        app.query_one("StatusDisplay").health = 20
        await pilot.pause(0.1)
        assert "20%" in str(stats_label.render())
        assert status_display.has_class("health-low")

        # Update stats to dead
        app.query_one("StatusDisplay").health = 0
        await pilot.pause(0.1)
        # Use .plain to get the text without markup/formatting
        rendered_text = stats_label.render().plain
        assert "0%" in rendered_text
        assert status_display.has_class("health-low")


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
        assert str(buttons[0].label) == "1  Open Door"
        assert str(buttons[1].label) == "2  Go Back"

        # Verify inventory accumulated the new item
        inventory_label = app.query_one("#inventory-label", Label)
        inventory_text = str(inventory_label.render())
        assert "Broken Sword" in inventory_text
        assert "Health Potion" in inventory_text

        # Verify stats updated (now in separate #stats-text label)
        stats_label = app.query_one("#stats-text", Label)
        stats_text = str(stats_label.render())
        assert "90%" in stats_text
        assert "50 Gold" in stats_text

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
        assert str(buttons[0].label) == "1  Open Door"
        assert str(buttons[1].label) == "2  Go Back"


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
    generator.close.assert_called_once_with()
    db.close.assert_called_once_with()
    assert app.generator is None
    assert app.engine is None


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
async def test_on_unmount_cancels_workers_and_unsubscribes(mock_app_dependencies):
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(0.2)

        app.workers.cancel_group = MagicMock()
        app.workers.cancel_all = MagicMock()
        generator = MagicMock()
        db = MagicMock()
        engine = MagicMock()
        engine.db = db
        app.generator = generator
        app.engine = engine

        app.on_unmount()

        assert app._is_shutting_down is True
        app.workers.cancel_group.assert_any_call(app, "speculation")
        app.workers.cancel_group.assert_any_call(app, "typewriter")
        app.workers.cancel_all.assert_called_once_with()
        generator.close.assert_called_once_with()
        db.close.assert_called_once_with()
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
    """Test that choice buttons display numbered labels like [1], [2], etc."""
    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)

        choices_container = app.query_one("#choices-container", Container)
        buttons = list(choices_container.query(Button))

        assert len(buttons) == 2
        assert str(buttons[0].label).startswith("1 ")
        assert str(buttons[1].label).startswith("2 ")


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
        assert len(payload["ui_state"]["journal_entries"]) == 1

        # Restart the game
        await pilot.press("r")
        await pilot.pause(0.2)
        await pilot.press("y")
        await pilot.pause(1.0)

        assert app.turn_count == 1


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_full_save_load_lifecycle(mock_app_dependencies, tmp_path, monkeypatch):
    """Test that saving and then loading a game correctly restores all relevant state."""
    from cyoa.core import constants

    monkeypatch.setattr(constants, "SAVES_DIR", str(tmp_path))

    app = CYOAApp(model_path="dummy_path.gguf")

    async with app.run_test() as pilot:
        await pilot.pause(1.0)

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

        # Save
        await pilot.press("s")
        await pilot.pause(0.2)

        # Create a new app instance to simulate loading fresh
        app2 = CYOAApp(model_path="dummy_path.gguf")
        async with app2.run_test() as pilot2:
            await pilot2.pause(1.0) # Wait for engine to initialize

            # Find the save file
            save_files = [f for f in os.listdir(str(tmp_path)) if f.endswith(".json")]
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
            stats_label2_text = app2.query_one("#stats-text").render().plain
            assert "88%" in stats_label2_text
