import pytest
from unittest.mock import patch, MagicMock

from textual.widgets import Markdown, Button, Label, ListView
from textual.containers import Container

from cyoa.ui.app import CYOAApp
from cyoa.core.models import StoryNode, Choice
from cyoa.db.graph_db import CYOAGraphDB


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mock_generator(*args, **kwargs):
    """Return a mock generator that yields predefined StoryNodes."""
    mock_gen = MagicMock()
    # First generated node (startup)
    node1 = StoryNode(
        narrative="You awaken in a test dungeon.",
        choices=[Choice(text="Go North"), Choice(text="Go South")],
        items_gained=["Broken Sword"],
        title="Test Adventure"
    )
    # Second generated node (after choice)
    node2 = StoryNode(
        narrative="You went North.",
        choices=[Choice(text="Open Door")],
        items_gained=["Health Potion"],
        title="Test Adventure"
    )
    # Third generated node (ending)
    node3 = StoryNode(
        narrative="You opened the door and escaped!",
        choices=[],
        is_ending=True,
        title="Test Adventure"
    )
    def side_effect_func(context, *args, **kwargs):
        history_len = len(context.history)
        if history_len <= 2:
            return node1  # new adventure / restart
        elif history_len == 4:
            return node2  # first choice made
        else:
            return node3  # second choice made / ending
            
    mock_gen.generate_next_node.side_effect = side_effect_func
    return mock_gen


@pytest.fixture
def mock_app_dependencies():
    """Mock the LLM Generator and DB to be fast and deterministic in UI tests."""
    with patch("cyoa.ui.app.StoryGenerator", new=_mock_generator), \
         patch("cyoa.ui.app.CYOAGraphDB") as mock_db:
        
        # Configure the mock DB to not fail async DB operations
        db_instance = mock_db.return_value
        db_instance.create_story_node_and_get_title.return_value = "Test Adventure"
        db_instance.get_story_tree.return_value = None # Just empty for story map test initially
        
        # db.save_scene_async calls on_complete callback immediately to simulate success
        def mock_save_scene_async(on_complete=None, **kwargs):
            if on_complete:
                on_complete("dummy-scene-id")
                
        db_instance.save_scene_async.side_effect = mock_save_scene_async
        
        yield


# ── Tests ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_app_startup_and_loading_state(mock_app_dependencies):
    """Test that the app starts up, shows loading art, and renders the initial generated scene."""
    app = CYOAApp(model_path="dummy_path.gguf")
    
    async with app.run_test() as pilot:
        # Give the background workers a moment to process initial generation
        await pilot.pause(0.2)
        
        # Verify the story text container updated with the mock narrative
        story_md = app.query_one("#story-text", Markdown)
        assert "You awaken in a test dungeon." in app._current_story
        
        # Verify choices were generated
        choices_container = app.query_one("#choices-container", Container)
        buttons = list(choices_container.query(Button))
        assert len(buttons) == 2
        assert str(buttons[0].label) == "Go North"
        assert str(buttons[1].label) == "Go South"
        
        # Verify inventory was updated
        inventory_label = app.query_one("#inventory-display", Label)
        inventory_text = str(inventory_label.render())
        assert "Broken Sword" in inventory_text


@pytest.mark.asyncio
async def test_ui_panels_toggle(mock_app_dependencies):
    """Test pressing hotkeys toggles the visibility of the side panels and dark mode."""
    app = CYOAApp(model_path="dummy_path.gguf")
    
    async with app.run_test() as pilot:
        journal_panel = app.query_one("#journal-panel", Container)
        map_panel = app.query_one("#story-map-panel", Container)
        
        # Both panels should be hidden by default
        assert journal_panel.has_class("hidden")
        assert map_panel.has_class("hidden")
        
        # Press 'j' to toggle Journal
        await pilot.press("j")
        assert not journal_panel.has_class("hidden")
        
        # Press 'm' to toggle Story Map
        await pilot.press("m")
        assert not map_panel.has_class("hidden")
        
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
        await pilot.pause(0.2)
        
        # Press '1' to select the first choice ("Go North")
        await pilot.press("1")
        
        # Pause to let the worker thread process the next mock node
        await pilot.pause(0.2)
        
        # Verify the story text appended the new narrative
        story_md = app.query_one("#story-text", Markdown)
        assert "You went North." in app._current_story
        
        # Check that the UI choice buttons updated to the new choices
        choices_container = app.query_one("#choices-container", Container)
        buttons = list(choices_container.query(Button))
        assert len(buttons) == 1
        assert str(buttons[0].label) == "Open Door"
        
        # Verify inventory accumulated the new item
        inventory_label = app.query_one("#inventory-display", Label)
        inventory_text = str(inventory_label.render())
        assert "Broken Sword" in inventory_text
        assert "Health Potion" in inventory_text
        
        # Verify journal updated
        journal_list = app.query_one("#journal-list", ListView)
        journal_labels = journal_list.query(Label)
        journal_text = "".join(str(l.render()) for l in journal_labels)
        assert "Go North" in journal_text


@pytest.mark.asyncio
async def test_choice_selection_via_click(mock_app_dependencies):
    """Test clicking a choice button triggers the next step as expected."""
    app = CYOAApp(model_path="dummy_path.gguf")
    
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        
        # Click the first choice button
        choices_container = app.query_one("#choices-container", Container)
        first_btn = list(choices_container.query(Button))[0]
        await pilot.click(f"#{first_btn.id}")
        
        await pilot.pause(0.2)
        assert "You went North." in app._current_story
        
        choices_container = app.query_one("#choices-container", Container)
        buttons = list(choices_container.query(Button))
        assert len(buttons) == 1
        assert str(buttons[0].label) == "Open Door"


@pytest.mark.asyncio
async def test_game_over_state_and_restart(mock_app_dependencies):
    """Test the game over state ends the choices and 'r' restarts the app."""
    app = CYOAApp(model_path="dummy_path.gguf")
    
    async with app.run_test() as pilot:
        await pilot.pause(0.2) # Node 1
        await pilot.press("1") 
        await pilot.pause(0.2) # Node 2
        await pilot.press("1") 
        await pilot.pause(0.2) # Node 3 (Ending)
        
        assert "You opened the door and escaped!" in app._current_story
        
        # Verify the choices are replaced with the restart button
        choices_container = app.query_one("#choices-container", Container)
        buttons = list(choices_container.query(Button))
        assert len(buttons) == 1
        assert str(buttons[0].label) == "✦ Start a New Adventure"
        assert buttons[0].id == "btn-new-adventure"
        
        # Test clicking the restart button
        await pilot.click("#btn-new-adventure")
        await pilot.pause(0.2) # Back to Node 1
        
        # Verify reset
        assert app.turn_count == 1
        assert "You awaken in a test dungeon." in app._current_story
        assert app.inventory == ["Broken Sword"]


@pytest.mark.asyncio
async def test_app_restart_via_keyboard(mock_app_dependencies):
    """Test pressing 'r' forcefully restarts the app at any point."""
    app = CYOAApp(model_path="dummy_path.gguf")
    
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        await pilot.press("1")
        await pilot.pause(0.2) # Node 2 (turn 2)
        
        assert app.turn_count == 2
        
        # Press R
        await pilot.press("r")
        await pilot.pause(0.2) # Node 1 again
        
        assert app.turn_count == 1
        assert "You awaken in a test dungeon." in app._current_story
        assert app.inventory == ["Broken Sword"]
        
        journal_list = app.query_one("#journal-list", ListView)
        assert len(list(journal_list.children)) == 0
