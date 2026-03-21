"""
Automated Story Tests — tests/test_story.py

Headless test harness that verifies core CYOA behaviour without loading the
actual LLM model or requiring a Neo4j instance.
"""
import pytest  # type: ignore
from unittest.mock import patch, MagicMock

from models import StoryNode, Choice
from llm_backend import StoryContext, MAX_CONTEXT_TURNS
from graph_db import CYOAGraphDB
from theme_loader import load_theme, list_themes
from rag_memory import NarrativeMemory


# ── Helpers ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def mock_textual_workers(monkeypatch):
    """
    Textual's @work decorator tries to create async tasks which fails in
    synchronous pytest environments with 'no running event loop'.
    We mock Worker._start to simply run the coroutine synchronously if needed,
    or just mock it entirely since we test the sync logic directly.
    """
    from textual.worker import Worker  # type: ignore
    monkeypatch.setattr(Worker, "_start", lambda *args, **kwargs: None)


def _make_node(narrative: str = "You are in a dungeon.", n_choices: int = 2, is_ending: bool = False) -> StoryNode:
    choices = [Choice(text=f"Choice {i + 1}") for i in range(n_choices)]
    return StoryNode(narrative=narrative, choices=choices, is_ending=is_ending)


# ── 1. Context window sliding window ─────────────────────────────────────────

class TestStoryContext:
    def test_history_within_max_turns(self):
        """History should never exceed (system + initial_prompt + max_turns*2) messages."""
        ctx = StoryContext(starting_prompt="Start", max_turns=3)
        for i in range(10):
            ctx.add_turn(f"Narrative {i}", f"Choice {i}")

        # system + initial user + 3 turn pairs = 2 + 6 = 8
        assert len(ctx.history) == 8

    def test_system_and_initial_prompt_preserved(self):
        """System message and starting prompt must always remain."""
        ctx = StoryContext(starting_prompt="My prompt", max_turns=2)
        for i in range(8):
            ctx.add_turn("narrative", "choice")

        assert ctx.history[0]["role"] == "system"
        assert ctx.history[1]["content"] == "My prompt"

    def test_no_trim_when_under_limit(self):
        """History should not trim if turns are within the window."""
        ctx = StoryContext(starting_prompt="Start", max_turns=5)
        ctx.add_turn("n1", "c1")
        ctx.add_turn("n2", "c2")
        # 2 + 2*2 = 6 messages — under limit of 2 + 5*2 = 12
        assert len(ctx.history) == 6


# ── 2. LLM JSON parse failure graceful fallback ───────────────────────────────

class TestStoryGeneratorFallback:
    def test_bad_json_returns_fallback_node(self):
        """If LLM returns invalid JSON, generate_next_node should return a valid fallback StoryNode."""
        from llm_backend import StoryGenerator

        with patch("llm_backend.Llama") as MockLlama:
            mock_llm = MagicMock()
            mock_llm.create_chat_completion.return_value = {
                "choices": [{"message": {"content": "NOT VALID JSON {"}}]
            }
            MockLlama.return_value = mock_llm

            gen = StoryGenerator.__new__(StoryGenerator)
            gen.llm = mock_llm
            gen._schema = StoryNode.model_json_schema()
            gen._temperature = 0.6
            gen._max_tokens = 512

            ctx = StoryContext("start")
            node = gen.generate_next_node(ctx)

        assert isinstance(node, StoryNode)
        assert len(node.choices) >= 1  # fallback always has a choice

    def test_valid_json_returns_parsed_node(self):
        """If LLM returns valid JSON, generate_next_node should return a proper StoryNode."""
        import json
        from llm_backend import StoryGenerator

        payload = StoryNode(
            narrative="A torch flickers.",
            choices=[Choice(text="Pick it up"), Choice(text="Leave it")]
        ).model_dump()

        with patch("llm_backend.Llama") as MockLlama:
            mock_llm = MagicMock()
            mock_llm.create_chat_completion.return_value = {
                "choices": [{"message": {"content": json.dumps(payload)}}]
            }
            MockLlama.return_value = mock_llm

            gen = StoryGenerator.__new__(StoryGenerator)
            gen.llm = mock_llm
            gen._schema = StoryNode.model_json_schema()
            gen._temperature = 0.6
            gen._max_tokens = 512

            ctx = StoryContext("start")
            node = gen.generate_next_node(ctx)

        assert node.narrative == "A torch flickers."
        assert len(node.choices) == 2


# ── 3. Graph DB offline graceful degradation ──────────────────────────────────

class TestCYOAGraphDBOffline:
    def test_offline_sets_driver_none(self):
        """CYOAGraphDB with unreachable URI should set driver=None without raising."""
        db = CYOAGraphDB(uri="bolt://localhost:9999")  # nothing listening here
        assert db.driver is None

    def test_offline_create_scene_returns_uuid(self):
        """create_scene_node with no driver should return a UUID string without crashing."""
        db = CYOAGraphDB(uri="bolt://localhost:9999")
        scene_id = db.create_scene_node("narrative", ["choice"], "My Story")
        assert isinstance(scene_id, str) and len(scene_id) == 36  # UUID format

    def test_offline_create_edge_is_noop(self):
        """create_choice_edge with no driver should return silently."""
        db = CYOAGraphDB(uri="bolt://localhost:9999")
        db.create_choice_edge("id-a", "id-b", "Go north")  # should not raise

    def test_offline_story_title_passthrough(self):
        """create_story_node_and_get_title with no driver should return the input title unchanged."""
        db = CYOAGraphDB(uri="bolt://localhost:9999")
        result = db.create_story_node_and_get_title("My Adventure")
        assert result == "My Adventure"


# ── 4. is_ending propagation ──────────────────────────────────────────────────

class TestStoryNodeEnding:
    def test_is_ending_defaults_false(self):
        node = _make_node()
        assert node.is_ending is False

    def test_is_ending_true_with_empty_choices(self):
        node = StoryNode(narrative="You have escaped!", choices=[], is_ending=True)
        assert node.is_ending is True
        assert len(node.choices) == 0

    def test_is_ending_false_with_choices(self):
        node = _make_node(n_choices=3)
        assert node.is_ending is False
        assert len(node.choices) == 3


# ── 5. Theme loading ──────────────────────────────────────────────────────────

class TestThemeLoader:
    def test_load_dark_dungeon(self):
        theme = load_theme("dark_dungeon")
        assert "prompt" in theme
        assert "name" in theme
        assert len(theme["prompt"]) > 10

    def test_load_space_explorer(self):
        theme = load_theme("space_explorer")
        assert "prompt" in theme
        assert "name" in theme

    def test_invalid_theme_raises(self):
        with pytest.raises(FileNotFoundError, match="not found"):
            load_theme("nonexistent_theme_xyz")

    def test_list_themes_includes_defaults(self):
        themes = list_themes()
        assert "dark_dungeon" in themes
        assert "space_explorer" in themes


# ── 6. RAG Narrative Memory ───────────────────────────────────────────────────

class TestNarrativeMemory:
    def test_add_and_query_returns_results(self):
        """Adding a scene and querying with similar text should return it."""
        mem = NarrativeMemory()
        mem.add("scene-1", "You discover a hidden door behind a bookshelf.")
        results = mem.query("secret passage behind shelf")
        assert len(results) == 1
        assert "bookshelf" in results[0]

    def test_empty_memory_returns_empty_list(self):
        """Querying an empty memory store should return []."""
        mem = NarrativeMemory()
        results = mem.query("anything")
        assert results == []

    def test_n_limits_results(self):
        """Query with n=2 should return at most 2 results."""
        mem = NarrativeMemory()
        for i in range(5):
            mem.add(f"scene-{i}", f"Scene {i}: You see something interesting.")
        results = mem.query("interesting scene", n=2)
        assert len(results) <= 2

    def test_lazy_init_defers_client_creation(self):
        """Fix #7: _collection should be None until the first add() triggers lazy init."""
        mem = NarrativeMemory()
        assert mem._collection is None, "Client should not be created at __init__ time"
        mem.add("scene-lazy", "A dark corridor stretches ahead.")
        assert mem._collection is not None, "_collection should exist after first add()"

    def test_duplicate_id_upserts(self):
        """Adding the same scene_id twice should not raise and should have 1 entry."""
        mem = NarrativeMemory()
        mem.add("scene-x", "First version.")
        mem.add("scene-x", "Updated version.")
        # Should not raise; collection count stays at 1
        assert mem._collection.count() == 1

class TestNPCMemory:
    def test_add_and_query_npc_returns_results(self):
        """Adding a scene for a specific NPC and querying it should return it."""
        from rag_memory import NPCMemory
        mem = NPCMemory()
        mem.add("Elara", "scene-1", "Elara hands you a glowing potion.")
        results = mem.query("Elara", "glowing potion")
        assert len(results) == 1
        assert "potion" in results[0]

    def test_different_npcs_have_isolated_memory(self):
        """Memories added to one NPC should not be retrieved by another."""
        from rag_memory import NPCMemory
        mem = NPCMemory()
        mem.add("Bob", "scene-b", "Bob gives you a sword.")
        mem.add("Alice", "scene-a", "Alice gives you a shield.")
        
        bob_results = mem.query("Bob", "gives you")
        assert len(bob_results) == 1
        assert "sword" in bob_results[0]
        
        alice_results = mem.query("Alice", "gives you")
        assert len(alice_results) == 1
        assert "shield" in alice_results[0]

    def test_empty_npc_memory_returns_empty_list(self):
        from rag_memory import NPCMemory
        mem = NPCMemory()
        assert mem.query("UnknownNPC", "anything") == []

# ── 7. Streaming token callback ───────────────────────────────────────────────

class TestStreamingCallback:
    def test_inject_memory_inserts_before_last_user(self):
        """inject_memory() should insert a system block before the last user message."""
        ctx = StoryContext(starting_prompt="Start", max_turns=5)
        ctx.add_turn("Narrative one.", "Go left")

        # Inject memory after first turn
        ctx.inject_memory(["You once saw a torch flicker."])

        # Find the injected memory block
        memory_msgs = [m for m in ctx.history if
                       m["role"] == "system" and "Memory" in m["content"]]
        assert len(memory_msgs) == 1
        assert "torch flicker" in memory_msgs[0]["content"]

    def test_inject_memory_empty_is_noop(self):
        """inject_memory([]) should not modify context history."""
        ctx = StoryContext(starting_prompt="Start", max_turns=5)
        before_len = len(ctx.history)
        ctx.inject_memory([])
        assert len(ctx.history) == before_len

    def test_inject_memory_replaces_existing_block(self):
        """inject_memory() called twice should replace, not accumulate, the memory block."""
        ctx = StoryContext(starting_prompt="Start", max_turns=5)
        ctx.add_turn("Narrative one.", "Go left")

        ctx.inject_memory(["Memory A."])
        ctx.inject_memory(["Memory B."])  # should replace A, not add a second block

        memory_msgs = [m for m in ctx.history if
                       m["role"] == "system" and m["content"].startswith("[Memory")]
        assert len(memory_msgs) == 1, "There should be exactly one memory block after two injects"
        assert "Memory B" in memory_msgs[0]["content"]
        assert "Memory A" not in memory_msgs[0]["content"]

    def test_stream_narrative_extractor(self):
        """_stream_with_callback should extract narrative characters correctly."""
        import json
        from llm_backend import StoryGenerator

        payload = {
            "title": None,
            "narrative": "A torch flickers in the dark.",
            "choices": [{"text": "Run"}],
            "is_ending": False,
        }
        json_str = json.dumps(payload)

        # Simulate streaming chunks split mid-string
        chunks = []
        for ch in json_str:
            chunks.append({"choices": [{"delta": {"content": ch}, "finish_reason": None}]})
        chunks.append({"choices": [{"delta": {}, "finish_reason": "stop"}]})

        received = []

        gen = StoryGenerator.__new__(StoryGenerator)
        result = gen._stream_with_callback(iter(chunks), on_token=received.append)

        extracted = "".join(received)
        assert "torch" in extracted
        assert "dark" in extracted
        # Full JSON still reconstructable
        assert json.loads(result)["narrative"] == "A torch flickers in the dark."


# ── 8. New UI Components: Branching and Animated Spinner ───────────────────────

class TestThemeSpinner:
    def test_spinner_cycles_frames(self):
        """ThemeSpinner should update its frame index on each tick."""
        from app import ThemeSpinner
        
        frames = ["[A]", "[B]", "[C]"]
        spinner = ThemeSpinner(frames=frames)
        
        # Manually invoke on_mount behavior for headless testing
        spinner._frame_idx = 0
        
        spinner.tick()
        assert spinner._frame_idx == 1
        spinner.tick()
        assert spinner._frame_idx == 2
        spinner.tick()
        assert spinner._frame_idx == 0


class TestBranchingLogic:
    def test_restore_to_scene_rebuilds_context(self):
        """Restoring to a past scene should rebuild the StoryContext and memory correctly."""
        from app import CYOAApp
        
        history = {
            "scenes": [
                {"id": "scene-1", "narrative": "You wake up.", "available_choices": ["Stand"]},
                {"id": "scene-2", "narrative": "You stand up.", "available_choices": ["Walk left", "Walk right"]},
                {"id": "scene-3", "narrative": "You walk left into a wall.", "available_choices": ["Turn around"]}
            ],
            "choices": ["Stand", "Walk left"]
        }
        
        app = CYOAApp(model_path="dummy")
        app.current_scene_id = "scene-3"
        app._current_story = "You wake up.\n\nYou stand up.\n\nYou walk left into a wall."
        
        # In a headless pytest environment without an event loop, we must mock out
        # both UI node queries (which fail without being mounted). We also mock
        # call_from_thread to do NOTHING for the final `display_node` call because
        # that method manipulates the DOM. The `pre_update` callback we can execute instantly.
        def mock_call_from_thread(callback, *args, **kwargs):
            if callback.__name__ == 'pre_update':
                # Run the pre_update synchronous closure
                callback(*args, **kwargs)
            # Ignore display_node or show_branch_screen as they need real text DOM
            
        with patch.object(app, "call_from_thread", side_effect=mock_call_from_thread), \
             patch.object(app, "query_one"), \
             patch.object(app, "set_timer"):
             
            # 0-based idx: idx=1 implies restoring to scene-2
            # Since restore_to_scene is a @work worker, call the unwrapped original function directly for sync testing
            app.restore_to_scene.__wrapped__(app, idx=1, history=history)
            
            # Check context
            assert app.current_scene_id == "scene-2"
            assert app.last_choice_text == "Stand"
            assert app._last_raw_narrative == "You stand up."
            
            # Context history should correctly have prompt + (narrative, choice) pairs up to idx
            assert app.story_context is not None
            assert len(app.story_context.history) == 4  # System + User Prompt + Assistant Scene 1 + User Choice 1
            assert "You wake up." in app.story_context.history[2]["content"]
            assert "Stand" in app.story_context.history[3]["content"]

    def test_action_branch_past_aborts_if_no_history(self):
        """action_branch_past should return early if there is no db or current scene."""
        from app import CYOAApp
        app = CYOAApp(model_path="dummy")
        
        # db is None
        assert app.db is None
        
        # Mock work decorator to just call the function
        def mock_call_from_thread(callback, *args, **kwargs):
            callback(*args, **kwargs)
            
        with patch.object(app, "call_from_thread", side_effect=mock_call_from_thread) as mock_call:
            app.action_branch_past()
            mock_call.assert_not_called()


# ── 9. Procedural Item System ────────────────────────────────────────────────

class TestProceduralItemSystem:
    def test_story_context_formats_inventory(self):
        """StoryContext should properly inject the inventory state into the user prompt."""
        ctx = StoryContext(starting_prompt="Start", max_turns=5)
        ctx.add_turn("You found a sword.", "Take sword", inventory=["Iron Sword", "Torch"])
        
        last_user_msg = ctx.history[-1]
        assert last_user_msg["role"] == "user"
        assert "Take sword" in last_user_msg["content"]
        assert "[System Note: Current Inventory: Iron Sword, Torch]" in last_user_msg["content"]

    def test_story_context_handles_empty_inventory(self):
        """StoryContext should format gracefully when inventory is empty."""
        ctx = StoryContext(starting_prompt="Start", max_turns=5)
        ctx.add_turn("You found nothing.", "Wait", inventory=[])
        
        last_user_msg = ctx.history[-1]
        assert last_user_msg["role"] == "user"
        assert "[System Note: Current Inventory: Empty]" in last_user_msg["content"]

    def test_app_updates_inventory_state(self):
        """CYOAApp should extract the items list from the generated StoryNode and update state."""
        from app import CYOAApp
        from models import Choice, StoryNode
        
        app = CYOAApp(model_path="dummy")
        
        # Mock generator to return a node with items
        mock_node = StoryNode(
            narrative="You found a shiny key.",
            choices=[Choice(text="Take key")],
            items_gained=["Shiny Key", "Map"],
            items_lost=[]
        )
        
        app.generator = MagicMock()
        app.generator.generate_next_node.return_value = mock_node
        app.db = MagicMock()
        
        with patch.object(app, "call_from_thread"):
            # Call wrapped synchronous version since @work is mocked out
            app.initialize_and_start.__wrapped__(app, model_path="dummy")
            
        assert app.inventory == ["Shiny Key", "Map"]

