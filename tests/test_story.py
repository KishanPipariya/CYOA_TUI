"""
Automated Story Tests — tests/test_story.py

Headless test harness that verifies core CYOA behaviour without loading the
actual LLM model or requiring a Neo4j instance.
"""

import pytest  # type: ignore
from unittest.mock import patch, MagicMock

from cyoa.core.models import StoryNode, Choice
from cyoa.llm.llm_backend import StoryContext
from cyoa.db.graph_db import CYOAGraphDB
from cyoa.core.theme_loader import load_theme, list_themes
from cyoa.db.rag_memory import NarrativeMemory


# ── Helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def mock_textual_workers(request, monkeypatch):
    """
    Textual's @work decorator tries to create async tasks which fails in
    synchronous pytest environments with 'no running event loop'.
    We mock Worker._start to simply run the coroutine synchronously if needed,
    or just mock it entirely since we test the sync logic directly.

    Tests using app.run_test() must opt out with @pytest.mark.no_worker_mock
    so that real Textual workers can execute inside the pilot.
    """
    if request.node.get_closest_marker("no_worker_mock"):
        return  # Let real Textual workers run for run_test()-based tests
    from textual.worker import Worker  # type: ignore

    monkeypatch.setattr(Worker, "_start", lambda *args, **kwargs: None)


def _make_node(
    narrative: str = "You are in a dungeon.",
    n_choices: int = 2,
    is_ending: bool = False,
) -> StoryNode:
    choices = [Choice(text=f"Choice {i + 1}") for i in range(n_choices)]
    return StoryNode(narrative=narrative, choices=choices, is_ending=is_ending)


# ── 1. Context window sliding window ─────────────────────────────────────────


class TestStoryContext:
    def test_history_within_max_turns(self):
        """History should never exceed (initial_prompt + max_turns*2) messages."""
        ctx = StoryContext(starting_prompt="Start", max_turns=3)
        for i in range(10):
            ctx.add_turn(f"Narrative {i}", f"Choice {i}")

        # initial user + 3 turn pairs = 1 + 6 = 7
        assert len(ctx.history) == 7

    def test_system_and_initial_prompt_preserved(self):
        """Initial user prompt must always remain."""
        ctx = StoryContext(starting_prompt="My prompt", max_turns=2)
        for i in range(8):
            ctx.add_turn("narrative", "choice")

        assert ctx.history[0]["role"] == "user"
        assert ctx.history[0]["content"] == "My prompt"

    def test_no_trim_when_under_limit(self):
        """History should not trim if turns are within the window."""
        ctx = StoryContext(starting_prompt="Start", max_turns=5)
        ctx.add_turn("n1", "c1")
        ctx.add_turn("n2", "c2")
        # 1 + 2*2 = 5 messages — under limit of 1 + 5*2 = 11
        assert len(ctx.history) == 5

    def test_needs_summarization_trigger(self):
        """needs_summarization should return True at 80% of max_turns."""
        ctx = StoryContext(starting_prompt="Start", max_turns=10)
        # 0.8 * 10 = 8 turn pairs
        for i in range(7):
            ctx.add_turn(f"n{i}", f"c{i}")
        assert ctx.needs_summarization(threshold=0.8) is False

        ctx.add_turn("n7", "c7")  # 8th pair
        assert ctx.needs_summarization(threshold=0.8) is True

    def test_get_turns_for_summary_identifies_older_half(self):
        """get_turns_for_summary should return only the oldest turn pairs."""
        ctx = StoryContext(starting_prompt="Start", max_turns=4)
        # Add 4 turn pairs. keep_pairs = 4//2 = 2.
        # Older half = index 0, 1 (pair 1 & 2). Newer half = 2, 3 (pair 3 & 4).
        for i in range(4):
            ctx.add_turn(f"narrative {i}", f"choice {i}")

        turns = ctx.get_turns_for_summary()
        # 4 turns total, keep 2 pairs = 4 messages. Summary should have (4*2 - 4) = 4 messages.
        assert len(turns) == 4
        assert "narrative 0" in turns[0]["content"]
        assert "narrative 1" in turns[2]["content"]
        # FRESH turns should NOT be in the summary tail
        assert "narrative 2" not in [t["content"] for t in turns]

    def test_set_rolling_summary_truncates_history(self):
        """set_rolling_summary should store the summary string and prune history correctly."""
        ctx = StoryContext(starting_prompt="Start", max_turns=4)
        for i in range(4):
            ctx.add_turn(f"n{i}", f"c{i}")

        ctx.set_rolling_summary("The story so far: stuff happened.")
        assert ctx.rolling_summary == "The story so far: stuff happened."

        # Should preserve: Initial prompt (1) + freshest max_turns/2 pairs (2 pairs = 4 msgs) = 5 total
        assert len(ctx.history) == 5
        assert ctx.history[0]["content"] == "Start"
        # The oldest were n0, n1. The freshest were n2, n3.
        assert "n2" in ctx.history[1]["content"]
        assert "n3" in ctx.history[3]["content"]

    def test_system_prompt_includes_summary(self):
        """get_messages should render the rolling_summary in the system prompt."""
        ctx = StoryContext(starting_prompt="Start", max_turns=4)
        ctx.set_rolling_summary("MY UNIQUE SUMMARY")
        msgs = ctx.get_messages()
        sys_msg = msgs[0]["content"]
        assert "<rolling_summary>" in sys_msg
        assert "MY UNIQUE SUMMARY" in sys_msg


# ── 2. LLM JSON parse failure graceful fallback ───────────────────────────────


class TestStoryGeneratorFallback:
    @pytest.mark.asyncio
    async def test_bad_json_returns_fallback_node(self):
        """If LLM returns invalid JSON, generate_next_node_async should return a valid fallback StoryNode."""
        from cyoa.llm.llm_backend import StoryGenerator

        with patch("cyoa.llm.llm_backend.Llama") as MockLlama:
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
            node = await gen.generate_next_node_async(ctx)

        assert isinstance(node, StoryNode)
        assert len(node.choices) >= 1  # fallback always has a choice

    @pytest.mark.asyncio
    async def test_valid_json_returns_parsed_node(self):
        """If LLM returns valid JSON, generate_next_node_async should return a proper StoryNode."""
        import json
        from cyoa.llm.llm_backend import StoryGenerator

        payload = StoryNode(
            narrative="A torch flickers.",
            choices=[Choice(text="Pick it up"), Choice(text="Leave it")],
        ).model_dump()

        with patch("cyoa.llm.llm_backend.Llama") as MockLlama:
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
            node = await gen.generate_next_node_async(ctx)

        assert node.narrative == "A torch flickers."
        assert len(node.choices) == 2

    @pytest.mark.asyncio
    async def test_generate_summary_async_calls_llm(self):
        """generate_summary_async should invoke chat completion with the summary prompt."""
        from cyoa.llm.llm_backend import StoryGenerator

        with patch("cyoa.llm.llm_backend.Llama") as MockLlama:
            mock_llm = MagicMock()
            mock_llm.create_chat_completion.return_value = {
                "choices": [{"message": {"content": "Summary result."}}]
            }
            MockLlama.return_value = mock_llm

            gen = StoryGenerator.__new__(StoryGenerator)
            gen.llm = mock_llm
            gen._summary_max_tokens = 200

            turns = [
                {"role": "assistant", "content": "Scene 1"},
                {"role": "user", "content": "Choice 1"},
            ]
            summary = await gen.generate_summary_async(turns)

            assert summary == "Summary result."
            assert mock_llm.create_chat_completion.called
            # Verify system instruction presence
            call_args = mock_llm.create_chat_completion.call_args[1]
            sys_prompt = call_args["messages"][0]["content"]
            assert "precise narrative archivist" in sys_prompt


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
    @pytest.mark.asyncio
    async def test_add_and_query_returns_results(self):
        """Adding a scene and querying with similar text should return it."""
        mem = NarrativeMemory()
        await mem.add_async("scene-1", "You discover a hidden door behind a bookshelf.")
        results = await mem.query_async("secret passage behind shelf")
        assert len(results) == 1
        assert "bookshelf" in results[0]

    @pytest.mark.asyncio
    async def test_empty_memory_returns_empty_list(self):
        """Querying an empty memory store should return []."""
        mem = NarrativeMemory()
        results = await mem.query_async("anything")
        assert results == []

    @pytest.mark.asyncio
    async def test_n_limits_results(self):
        """Query with n=2 should return at most 2 results."""
        mem = NarrativeMemory()
        for i in range(5):
            await mem.add_async(f"scene-{i}", f"Scene {i}: You see something interesting.")
        results = await mem.query_async("interesting scene", n=2)
        assert len(results) <= 2

    @pytest.mark.asyncio
    async def test_lazy_init_defers_client_creation(self):
        """Fix #7: _collection should be None until the first add() triggers lazy init."""
        mem = NarrativeMemory()
        assert mem._collection is None, "Client should not be created at __init__ time"
        await mem.add_async("scene-lazy", "A dark corridor stretches ahead.")
        assert mem._collection is not None, "_collection should exist after first add_async()"

    @pytest.mark.asyncio
    async def test_duplicate_id_upserts(self):
        """Adding the same scene_id twice should not raise and should have 1 entry."""
        mem = NarrativeMemory()
        await mem.add_async("scene-x", "First version.")
        await mem.add_async("scene-x", "Updated version.")
        # Should not raise; collection count stays at 1
        assert mem._collection.count() == 1


class TestNPCMemory:
    @pytest.mark.asyncio
    async def test_add_and_query_npc_returns_results(self):
        """Adding a scene for a specific NPC and querying it should return it."""
        from cyoa.db.rag_memory import NPCMemory

        mem = NPCMemory()
        await mem.add_async("Elara", "scene-1", "Elara hands you a glowing potion.")
        results = await mem.query_async("Elara", "glowing potion")
        assert len(results) == 1
        assert "potion" in results[0]

    @pytest.mark.asyncio
    async def test_different_npcs_have_isolated_memory(self):
        """Memories added to one NPC should not be retrieved by another."""
        from cyoa.db.rag_memory import NPCMemory

        mem = NPCMemory()
        await mem.add_async("Bob", "scene-b", "Bob gives you a sword.")
        await mem.add_async("Alice", "scene-a", "Alice gives you a shield.")

        bob_results = await mem.query_async("Bob", "gives you")
        assert len(bob_results) == 1
        assert "sword" in bob_results[0]

        alice_results = await mem.query_async("Alice", "gives you")
        assert len(alice_results) == 1
        assert "shield" in alice_results[0]

    @pytest.mark.asyncio
    async def test_empty_npc_memory_returns_empty_list(self):
        from cyoa.db.rag_memory import NPCMemory

        mem = NPCMemory()
        assert await mem.query_async("UnknownNPC", "anything") == []


# ── 7. Streaming token callback ───────────────────────────────────────────────


class TestStreamingCallback:
    def test_inject_memory_inserts_before_last_user(self):
        """inject_memory() should update the memories state."""
        ctx = StoryContext(starting_prompt="Start", max_turns=5)
        ctx.add_turn("Narrative one.", "Go left")

        # Inject memory after first turn
        ctx.inject_memory(["You once saw a torch flicker."])

        assert len(ctx.memories) == 1
        assert "torch flicker" in ctx.memories[0]

    def test_inject_memory_empty_is_noop(self):
        """inject_memory([]) should update state properly."""
        ctx = StoryContext(starting_prompt="Start", max_turns=5)
        ctx.inject_memory([])
        assert len(ctx.memories) == 0

    def test_inject_memory_replaces_existing_block(self):
        """inject_memory() called twice should replace, not accumulate, the memory block."""
        ctx = StoryContext(starting_prompt="Start", max_turns=5)
        ctx.add_turn("Narrative one.", "Go left")

        ctx.inject_memory(["Memory A."])
        ctx.inject_memory(["Memory B."])  # should replace A, not add a second block

        assert len(ctx.memories) == 1
        assert "Memory B" in ctx.memories[0]
        assert "Memory A" not in ctx.memories[0]

    @pytest.mark.asyncio
    async def test_stream_narrative_extractor(self):
        """_stream_with_callback_async should extract narrative characters correctly."""
        import json
        from cyoa.llm.llm_backend import StoryGenerator

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
            chunks.append(
                {"choices": [{"delta": {"content": ch}, "finish_reason": None}]}
            )
        chunks.append({"choices": [{"delta": {}, "finish_reason": "stop"}]})

        received = []

        gen = StoryGenerator.__new__(StoryGenerator)
        result = await gen._stream_with_callback_async(iter(chunks), on_token_chunk=received.append)

        extracted = "".join(received)
        assert "torch" in extracted
        assert "dark" in extracted
        # Full JSON still reconstructable
        assert json.loads(result)["narrative"] == "A torch flickers in the dark."

    @pytest.mark.asyncio
    async def test_stream_resilience(self):
        """Verify extractor handles weird spacing and newlines using jiter."""
        from cyoa.llm.llm_backend import StoryGenerator

        # Weird spacing, newlines, and escaping that would break a simple regex
        json_str = '{"title": null,  "narrative" \n : \n  "The dragon said, \\"Return my gold!\\"." , "choices": []}'
        
        chunks = []
        for ch in json_str:
            chunks.append({"choices": [{"delta": {"content": ch}}]})
        
        received = []
        gen = StoryGenerator.__new__(StoryGenerator)
        result = await gen._stream_with_callback_async(iter(chunks), on_token_chunk=received.append)
        
        extracted = "".join(received)
        assert extracted == 'The dragon said, "Return my gold!".'
        assert "gold" in extracted


# ── 8. New UI Components: Branching and Animated Spinner ───────────────────────


class TestThemeSpinner:
    def test_spinner_cycles_frames(self):
        """ThemeSpinner should update its frame index on each tick."""
        from cyoa.ui.app import ThemeSpinner

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
    @pytest.mark.asyncio
    @pytest.mark.no_worker_mock
    async def test_restore_to_scene_rebuilds_context(self):
        """Restoring to a past scene should rebuild the StoryContext and memory correctly."""
        from unittest.mock import AsyncMock
        from cyoa.ui.app import CYOAApp

        history = {
            "scenes": [
                {
                    "id": "scene-1",
                    "narrative": "You wake up.",
                    "available_choices": ["Stand"],
                },
                {
                    "id": "scene-2",
                    "narrative": "You stand up.",
                    "available_choices": ["Walk left", "Walk right"],
                },
                {
                    "id": "scene-3",
                    "narrative": "You walk left into a wall.",
                    "available_choices": ["Turn around"],
                },
            ],
            "choices": ["Stand", "Walk left"],
        }

        # Provide a mock generator that never produces real nodes so restore_to_scene
        # can be driven in isolation without loading the real LLM.
        mock_gen = MagicMock()
        mock_gen.generate_next_node_async = AsyncMock(
            return_value=MagicMock(
                narrative="You stand up.",
                choices=[],
                is_ending=False,
                items_gained=[],
                items_lost=[],
                stat_updates={},
                title=None,
            )
        )

        with (
            patch("cyoa.ui.app.StoryGenerator", return_value=mock_gen),
            patch("cyoa.ui.app.CYOAGraphDB") as mock_db_cls,
        ):
            mock_db = mock_db_cls.return_value
            mock_db.create_story_node_and_get_title.return_value = "Test Story"
            mock_db.get_story_tree.return_value = None
            mock_db.save_scene_async.side_effect = (
                lambda on_complete=None, **kw: on_complete("sid") if on_complete else None
            )

            app = CYOAApp(model_path="dummy")
            app.current_scene_id = "scene-3"
            app._current_story = (
                "You wake up.\n\nYou stand up.\n\nYou walk left into a wall."
            )

            async with app.run_test() as pilot:
                # Allow the initial startup worker to settle
                await pilot.pause(0.2)

                # restore_to_scene is a @work coroutine — calling it schedules a Worker;
                # do NOT await it, just call it and give the event loop time to run it.
                app.restore_to_scene(idx=1, history=history)
                # Give the worker two ticks to fully execute and flush state
                await pilot.pause(0.3)

                # Check context
                assert app.current_scene_id == "scene-2"
                assert app.last_choice_text == "Stand"
                assert app._last_raw_narrative == "You stand up."

                # Context history should correctly have prompt + (narrative, choice) pairs up to idx
                assert app.story_context is not None
                assert (
                    len(app.story_context.history) == 3
                )  # User Prompt + Assistant Scene 1 + User Choice 1
                assert "You wake up." in app.story_context.history[1]["content"]
                assert "Stand" in app.story_context.history[2]["content"]

    def test_action_branch_past_aborts_if_no_history(self):
        """action_branch_past should return early if there is no db or current scene."""
        from cyoa.ui.app import CYOAApp

        app = CYOAApp(model_path="dummy")

        # db is None
        assert app.db is None

        # Mock work decorator to just call the function
        def mock_call_from_thread(callback, *args, **kwargs):
            callback(*args, **kwargs)

        with patch.object(
            app, "call_from_thread", side_effect=mock_call_from_thread
        ) as mock_call:
            app.action_branch_past()
            mock_call.assert_not_called()


# ── 9. Procedural Item System ────────────────────────────────────────────────


class TestProceduralItemSystem:
    def test_story_context_formats_inventory(self):
        """StoryContext should properly inject the inventory state into the user prompt."""
        ctx = StoryContext(starting_prompt="Start", max_turns=5)
        ctx.add_turn(
            "You found a sword.", "Take sword", inventory=["Iron Sword", "Torch"]
        )

        messages = ctx.get_messages()
        sys_msg = messages[0]
        assert sys_msg["role"] == "system"
        assert "Current Inventory: Iron Sword, Torch" in sys_msg["content"]

    def test_story_context_handles_empty_inventory(self):
        """StoryContext should format gracefully when inventory is empty."""
        ctx = StoryContext(starting_prompt="Start", max_turns=5)
        ctx.add_turn("You found nothing.", "Wait", inventory=[])

        messages = ctx.get_messages()
        sys_msg = messages[0]
        assert sys_msg["role"] == "system"
        assert "Current Inventory: Empty" in sys_msg["content"]

    @pytest.mark.asyncio
    @pytest.mark.no_worker_mock
    async def test_app_updates_inventory_state(self):
        """CYOAApp should extract the items list from the generated StoryNode and update state."""
        from unittest.mock import AsyncMock
        from cyoa.ui.app import CYOAApp
        from cyoa.core.models import Choice, StoryNode

        mock_node = StoryNode(
            narrative="You found a shiny key.",
            choices=[Choice(text="Take key")],
            items_gained=["Shiny Key", "Map"],
            items_lost=[],
        )

        mock_gen = MagicMock()
        mock_gen.generate_next_node_async = AsyncMock(return_value=mock_node)

        # Use a factory callable (matching test_tui.py's _mock_generator pattern)
        # so StoryGenerator(...) instantiation returns the pre-built mock_gen.
        def mock_generator_factory(*args, **kwargs):
            return mock_gen

        with (
            patch("cyoa.ui.app.StoryGenerator", new=mock_generator_factory),
            patch("cyoa.ui.app.CYOAGraphDB") as mock_db_cls,
        ):
            mock_db = mock_db_cls.return_value
            mock_db.create_story_node_and_get_title.return_value = "Test Story"
            mock_db.get_story_tree.return_value = None
            mock_db.save_scene_async.side_effect = (
                lambda on_complete=None, **kw: on_complete("sid") if on_complete else None
            )

            app = CYOAApp(model_path="dummy")
            async with app.run_test() as pilot:
                # Give the initialize_and_start @work task time to fully complete
                await pilot.pause(0.5)

                assert app.inventory == ["Shiny Key", "Map"]
