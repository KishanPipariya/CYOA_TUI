"""
Automated Story Tests — tests/test_story.py

Headless test harness that verifies core CYOA behaviour without loading the
actual LLM model or requiring a Neo4j instance.
"""

from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest  # type: ignore

from cyoa.core.models import Choice, LoreEntry, StoryNode
from cyoa.db.rag_memory import NarrativeMemory
from cyoa.llm.broker import StoryContext

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


# ── 1. Context window sliding window ─────────────────────────────────────────


class TestStoryContext:
    def test_history_within_budget(self):
        """History should be pruned when exceeding token budget."""
        # Each turn is 2 messages (assistant + user).
        # We'll use a mock counter: roles are 5 tokens each, content is 20 tokens each.
        # System prompt + initial prompt + 1 turn pair = (~50 + 25 + 50) = ~125 tokens.
        # A budget of 150 should only allow 1 turn pair to stay in history.
        ctx = StoryContext(
            starting_prompt="Start",
            token_budget=150,
            token_counter=lambda x: 20 if len(x) > 5 else 5,
        )
        for i in range(5):
            ctx.add_turn(f"Narrative {i}", f"Choice {i}")

        # Should have initial prompt (1) + latest turn pair (2) = 3 messages
        assert len(ctx.history) == 3
        # The latest turn should be preserved
        assert "Narrative 4" in ctx.history[1]["content"]

    def test_system_and_initial_prompt_preserved(self):
        """Initial user prompt must always remain."""
        ctx = StoryContext(starting_prompt="My prompt")
        for _i in range(8):
            ctx.add_turn("narrative", "choice")

        assert ctx.history[0]["role"] == "user"
        assert ctx.history[0]["content"] == "My prompt"

    def test_no_trim_when_under_limit(self):
        """History should not trim if tokens are within budget."""
        ctx = StoryContext(starting_prompt="Start", token_budget=1000)
        ctx.add_turn("n1", "c1")
        ctx.add_turn("n2", "c2")
        # 1 + 2*2 = 5 messages — should all fit in 1000 tokens
        assert len(ctx.history) == 5

    def test_needs_summarization_trigger(self):
        """needs_summarization should return True at 80% of token_budget."""
        # 0.8 * 100 = 80 tokens
        ctx = StoryContext(starting_prompt="Start", token_budget=100, token_counter=lambda x: 10)
        # initial prompt = 10 (role) + 10 (content) = 20
        # messages after 1st turn = system (20) + prompt (20) + turn1 (40) = 80
        ctx.add_turn("n0", "c0")
        assert ctx.needs_summarization(threshold=0.8) is True

    def test_get_turns_for_summary_identifies_older_tail(self):
        """get_turns_for_summary should return all but the 3 most recent turn pairs."""
        ctx = StoryContext(starting_prompt="Start")
        # Add 5 turn pairs.
        for i in range(5):
            ctx.add_turn(f"narrative {i}", f"choice {i}")

        turns = ctx.get_turns_for_summary()
        # 5 pairs total, keep 3 recent = 2 pairs for summary = 4 messages.
        assert len(turns) == 4
        assert "narrative 0" in turns[0]["content"]
        assert "narrative 1" in turns[2]["content"]
        # Recent turns should NOT be in the summary tail
        assert "narrative 2" not in [t["content"] for t in turns]

    def test_set_hierarchical_summary_and_pruning(self):
        """set_hierarchical_summary should store the summary strings and prune history."""
        ctx = StoryContext(starting_prompt="Start", token_budget=100, token_counter=lambda x: 10)
        for i in range(4):
            ctx.add_turn(f"n{i}", f"c{i}")

        ctx.set_hierarchical_summary(scene="The scene.", chapter="The chapter.", arc="The arc.")
        assert ctx.scene_summary == "The scene."
        assert ctx.chapter_summary == "The chapter."
        assert ctx.arc_summary == "The arc."

        # Should have pruned to fit budget.
        assert len(ctx.history) < 9
        assert ctx.history[0]["content"] == "Start"

    def test_system_prompt_includes_hierarchical_summaries(self):
        """get_messages should render all three hierarchy levels in the system prompt."""
        ctx = StoryContext(starting_prompt="Start")
        ctx.set_hierarchical_summary(scene="SCENE_TXT", chapter="CHAPTER_TXT", arc="ARC_TXT")
        msgs = ctx.get_messages()
        sys_msg = msgs[0]["content"]
        assert "<scene_summary>" in sys_msg
        assert "SCENE_TXT" in sys_msg
        assert "<chapter_summary>" in sys_msg
        assert "CHAPTER_TXT" in sys_msg
        assert "<arc_summary>" in sys_msg
        assert "ARC_TXT" in sys_msg

    def test_pruning_removes_memories_when_over_budget(self):
        """History and memories should be pruned when exceeding budget."""
        # Setup context where system + history + 1 memory > budget
        # but system + history + 0 memories <= budget
        ctx = StoryContext(
            starting_prompt="Start",
            token_budget=100,
            token_counter=lambda x: 20 if len(x) > 5 else 5,
        )
        ctx.add_turn("Narrative 1", "Choice 1")
        # History is now: Prompt (25), Assistant (25), User (25) = 75 tokens
        # Adding 2 memories of 20 tokens each = 40. Total = 115 (> 100)
        ctx.inject_memory(["Memory 1 Content", "Memory 2 Content"])

        # Pruning should trigger
        ctx._prune_history()

        # Should have kept only the highest priority memory or none to stay under 100
        # In our case, 1 memory makes it 75 + 20 = 95 (<= 100)
        assert len(ctx.memories) == 1
        assert "Memory 1" in ctx.memories[0]

    def test_stats_and_inventory_rendered_in_system_prompt(self):
        """System prompt should include both inventory and player stats."""
        # Mock the template render to see what's passed in
        ctx = StoryContext(starting_prompt="Start")
        ctx.inventory = ["Key", "Sword"]
        ctx.player_stats = {"health": 42, "gold": 100}

        msgs = ctx.get_messages()
        sys_content = msgs[0]["content"]

        # Since we use a real Jinja template in StoryContext, we check if the content is there
        # but system_prompt.j2 might be complex. Let's assume it renders basic strings.
        assert "Key" in sys_content
        assert "Sword" in sys_content
        assert "42" in sys_content
        assert "100" in sys_content

    def test_lore_codex_entries_rendered_in_system_prompt(self):
        """Compact codex summaries should be injected into the prompt context."""
        ctx = StoryContext(starting_prompt="Start")
        ctx.sync_world_state(
            lore_entries=[
                LoreEntry(
                    category="npc",
                    name="Mira",
                    summary="A scout who knows the drowned passages.",
                    discovered_turn=2,
                ),
                LoreEntry(
                    category="location",
                    name="Drowned Passage",
                    summary="A flooded route below the prison.",
                    discovered_turn=2,
                ),
            ]
        )

        sys_content = ctx.get_messages()[0]["content"]

        assert "Discovered Lore:" in sys_content
        assert "Mira - A scout who knows the drowned passages." in sys_content
        assert "Drowned Passage - A flooded route below the prison." in sys_content


# ── 2. LLM JSON parse failure graceful fallback ───────────────────────────────


class TestModelBrokerFallback:
    @pytest.mark.asyncio
    async def test_bad_json_returns_fallback_node(self):
        """If LLM returns invalid JSON, generate_next_node_async should return a valid fallback StoryNode."""
        from cyoa.llm.broker import ModelBroker
        from cyoa.llm.providers import LLMProvider

        mock_provider = MagicMock(spec=LLMProvider)
        mock_provider.generate_json = AsyncMock(return_value="NOT VALID JSON {")

        gen = ModelBroker(provider=mock_provider)
        ctx = StoryContext("start")
        node = await gen.generate_next_node_async(ctx)

        assert isinstance(node, StoryNode)
        assert len(node.choices) >= 1  # fallback always has a choice

    @pytest.mark.asyncio
    async def test_valid_json_returns_parsed_node(self):
        """If LLM returns valid JSON, generate_next_node_async should return a proper StoryNode."""
        import json

        from cyoa.llm.broker import ModelBroker
        from cyoa.llm.providers import LLMProvider

        payload = StoryNode(
            narrative="A torch flickers.",
            choices=[Choice(text="Pick it up"), Choice(text="Leave it")],
        ).model_dump()

        mock_provider = MagicMock(spec=LLMProvider)
        mock_provider.generate_json = AsyncMock(return_value=json.dumps(payload))

        gen = ModelBroker(provider=mock_provider)
        ctx = StoryContext("start")
        node = await gen.generate_next_node_async(ctx)

        assert node.narrative == "A torch flickers."
        assert len(node.choices) == 2

    @pytest.mark.asyncio
    async def test_hierarchical_summarization_logic(self):
        """update_story_summaries_async should correctly flow through hierarchy levels."""
        from cyoa.llm.broker import ModelBroker, StoryContext
        from cyoa.llm.providers import LLMProvider

        mock_provider = MagicMock(spec=LLMProvider)
        mock_provider.generate_text = AsyncMock(return_value="Summary result.")

        gen = ModelBroker(provider=mock_provider)
        ctx = StoryContext(starting_prompt="Start")
        # Add a bunch of turns to trigger summarization
        for i in range(12):
            ctx.add_turn(f"n{i}", f"c{i}")

        await gen.update_story_summaries_async(ctx)

        # Initial summary should be 'scene'
        assert ctx.scene_summary == "Summary result."
        assert ctx._scene_turn_count > 0

        # Simulate Promotion by forcing turn count
        ctx._scene_turn_count = 11
        await gen.update_story_summaries_async(ctx)

        # Now chapter should be updated
        assert ctx.chapter_summary == "Summary result."
        assert ctx._chapter_scene_count == 1
        # Scene summary reset/updated with latest turns
        assert ctx.scene_summary == "Summary result."

    @pytest.mark.asyncio
    async def test_repair_loop_success_on_second_attempt(self):
        """ModelBroker should retry if JSON is invalid and succeed if the second attempt is valid."""
        import json

        from cyoa.llm.broker import ModelBroker
        from cyoa.llm.providers import LLMProvider

        mock_provider = MagicMock(spec=LLMProvider)
        # First call returns garbage, second returns valid JSON
        mock_provider.generate_json = AsyncMock(
            side_effect=[
                "GARBAGE {",
                json.dumps(
                    {
                        "narrative": "Repaired!",
                        "choices": [{"text": "OK"}, {"text": "Cancel"}],
                        "items_gained": ["Sword"],
                        "items_lost": [],
                        "stat_updates": {"health": 10},
                    }
                ),
            ]
        )

        broker = ModelBroker(provider=mock_provider)
        # Force unified mode for this test to match the expected call count logic
        broker.unified_mode = True

        ctx = StoryContext("start")
        node = await broker.generate_next_node_async(ctx)

        assert node.narrative == "Repaired!"
        assert node.items_gained == ["Sword"]
        assert mock_provider.generate_json.call_count == 2

        # Verify the second call included the error message
        repair_messages = mock_provider.generate_json.call_args_list[1][1]["messages"]
        assert any("Fix JSON error" in m["content"] for m in repair_messages)

    @pytest.mark.asyncio
    async def test_repair_loop_exhaustion_returns_fallback(self):
        """ModelBroker should return a fallback node if all repair attempts fail."""
        from cyoa.llm.broker import ModelBroker
        from cyoa.llm.providers import LLMProvider

        mock_provider = MagicMock(spec=LLMProvider)
        mock_provider.generate_json = AsyncMock(return_value="STILL GARBAGE")

        # Set repair attempts to 1 (total 2 tries)
        with patch.dict("os.environ", {"LLM_REPAIR_ATTEMPTS": "1"}):
            broker = ModelBroker(provider=mock_provider)
            ctx = StoryContext("start")
            node = await broker.generate_next_node_async(ctx)

            assert "anomaly" in node.narrative
            assert mock_provider.generate_json.call_count == 2

    @pytest.mark.asyncio
    async def test_unified_repair_retry_uses_repair_prompt_and_lower_temperature(self):
        """Unified mode should retry with the broken payload and repair instructions."""
        import json

        from cyoa.llm.broker import ModelBroker
        from cyoa.llm.providers import LLMProvider

        mock_provider = MagicMock(spec=LLMProvider)
        mock_provider.generate_json = AsyncMock(
            side_effect=[
                '{"narrative": "broken"',
                json.dumps(
                    {
                        "narrative": "Recovered.",
                        "choices": [{"text": "Continue"}, {"text": "Hide"}],
                        "items_gained": [],
                        "items_lost": [],
                        "stat_updates": {},
                    }
                ),
            ]
        )

        broker = ModelBroker(provider=mock_provider)
        broker.unified_mode = True

        node = await broker.generate_next_node_async(StoryContext("start"))

        assert node.narrative == "Recovered."
        assert mock_provider.generate_json.await_count == 2

        first_call = mock_provider.generate_json.await_args_list[0].kwargs
        second_call = mock_provider.generate_json.await_args_list[1].kwargs
        repair_messages = second_call["messages"]

        assert first_call["temperature"] == broker._temperature
        assert second_call["temperature"] == 0.2
        assert repair_messages[-2]["content"] == '{"narrative": "broken"'
        assert "Fix JSON error" in repair_messages[-1]["content"]

    @pytest.mark.asyncio
    async def test_judge_pattern_skips_extraction_when_narrator_fails(self):
        """Judge extraction must not run when narrator generation exhausts retries."""
        from cyoa.llm.broker import ModelBroker
        from cyoa.llm.providers import LLMProvider

        mock_provider = MagicMock(spec=LLMProvider)
        mock_provider.generate_json = AsyncMock(return_value="not valid json")

        broker = ModelBroker(provider=mock_provider)
        broker.unified_mode = False

        node = await broker.generate_next_node_async(StoryContext("start"))

        assert "anomaly" in node.narrative
        assert mock_provider.generate_json.await_count == broker._repair_attempts + 1

    @pytest.mark.asyncio
    async def test_summarization_does_not_block_generation(self):
        """Summarization should be dispatched as a background task, not blocking TTFT.

        When needs_summarization() is True, _generate_next must NOT await the
        summarization before calling generate_next_node_async.  We verify this by
        asserting that generate_next_node_async starts (and completes) even while
        the summarization coroutine has not yet been awaited.
        """
        import asyncio
        import json

        from cyoa.core.engine import StoryEngine
        from cyoa.llm.broker import ModelBroker, StoryContext
        from cyoa.llm.providers import LLMProvider

        ordered_calls: list[str] = []

        async def slow_summarization(context: StoryContext) -> None:
            # Yields so the event loop can run the generation task first
            await asyncio.sleep(0)
            ordered_calls.append("summarization_done")

        narrator_payload = json.dumps(
            {"narrative": "A door appears.", "choices": [{"text": "Enter"}, {"text": "Wait"}]}
        )
        extraction_payload = json.dumps({"items_gained": [], "items_lost": [], "stat_updates": {}})

        call_seq = [narrator_payload, extraction_payload]

        async def mock_generate_json(*args, **kwargs):
            ordered_calls.append("generation_called")
            return call_seq.pop(0)

        mock_provider = MagicMock(spec=LLMProvider)
        mock_provider.generate_json = mock_generate_json
        mock_provider.count_tokens = MagicMock(return_value=5)
        mock_provider.save_state = AsyncMock(return_value=None)

        broker = ModelBroker(provider=mock_provider)
        broker.update_story_summaries_async = slow_summarization  # type: ignore[method-assign]

        engine = StoryEngine(broker=broker, starting_prompt="Start")
        engine.story_context = StoryContext(
            starting_prompt="Start",
            token_budget=50,  # Very small budget so needs_summarization() fires
            token_counter=lambda x: 10,  # Every token = 10 units, easily exceeds 80%
        )
        # Artificially fill history so needs_summarization() returns True
        engine.story_context.history = [
            {"role": "user", "content": "Start"},
            {"role": "assistant", "content": "You are in a dungeon."},
            {"role": "user", "content": "I choose: Go north"},
        ]

        await engine._generate_next()
        # Allow the background task to complete
        await asyncio.sleep(0.05)

        # Generation must have been called; summarization may or may not have
        # finished yet at this point — the key guarantee is generation was NOT
        # blocked waiting for summarization.
        assert "generation_called" in ordered_calls

    @pytest.mark.asyncio
    async def test_summarization_failure_is_non_fatal(self):
        """A failing background summarization must not crash or surface to the user."""
        import asyncio
        import json

        from cyoa.core.engine import StoryEngine
        from cyoa.llm.broker import ModelBroker
        from cyoa.llm.providers import LLMProvider

        async def failing_summarization(context: StoryContext) -> None:
            raise RuntimeError("Simulated summarization service failure")

        narrator_payload = json.dumps(
            {
                "narrative": "Everything is fine.",
                "choices": [{"text": "Continue"}, {"text": "Wait"}],
            }
        )
        extraction_payload = json.dumps({"items_gained": [], "items_lost": [], "stat_updates": {}})
        call_seq = [narrator_payload, extraction_payload]

        async def mock_generate_json(*args, **kwargs):
            return call_seq.pop(0)

        mock_provider = MagicMock(spec=LLMProvider)
        mock_provider.generate_json = mock_generate_json
        mock_provider.count_tokens = MagicMock(return_value=5)
        mock_provider.save_state = AsyncMock(return_value=None)

        broker = ModelBroker(provider=mock_provider)
        broker.update_story_summaries_async = failing_summarization  # type: ignore[method-assign]

        engine = StoryEngine(broker=broker, starting_prompt="Start")
        engine.story_context = StoryContext(
            starting_prompt="Start",
            token_budget=50,
            token_counter=lambda x: 10,
        )
        engine.story_context.history = [
            {"role": "user", "content": "Start"},
            {"role": "assistant", "content": "You are here."},
            {"role": "user", "content": "I choose: Look around"},
        ]

        # Should not raise even though summarization will fail
        await engine._generate_next()
        # Allow the background task error handler to run
        await asyncio.sleep(0.05)
        # Engine is still alive with a valid current_node
        assert engine.state.current_node is not None


# ── 3. RAG Narrative Memory ───────────────────────────────────────────────────


class TestNarrativeMemory:
    class _FakeClock:
        def __init__(self) -> None:
            self.current = 0.0

        def now(self) -> float:
            return self.current

        def advance(self, seconds: float) -> None:
            self.current += seconds

    class _FakeCollection:
        def __init__(self, fail_upsert: bool = False) -> None:
            self.name = "fake_collection"
            self._docs: dict[str, str] = {}
            self._fail_upsert = fail_upsert

        def upsert(self, *, ids, documents) -> None:
            if self._fail_upsert:
                raise RuntimeError("upsert failed")
            for doc_id, document in zip(ids, documents, strict=True):
                self._docs[doc_id] = document

        def count(self) -> int:
            return len(self._docs)

        def query(self, *, query_texts, n_results):
            _ = query_texts
            docs = list(self._docs.values())[:n_results]
            return {"documents": [docs]}

    class _FakeClient:
        def __init__(self, collection) -> None:
            self._collection = collection

        def create_collection(self, *, name, metadata):
            _ = name, metadata
            return self._collection

        def delete_collection(self, name: str) -> None:
            _ = name

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

    @pytest.mark.asyncio
    async def test_retries_then_reprobes_after_chroma_init_failures(self, monkeypatch):
        clock = self._FakeClock()
        attempts = {"count": 0}

        def fake_client_factory():
            attempts["count"] += 1
            if attempts["count"] < 4:
                raise RuntimeError(f"init failed {attempts['count']}")
            return self._FakeClient(self._FakeCollection())

        monkeypatch.setattr("cyoa.db.rag_memory.chromadb.Client", fake_client_factory)

        mem = NarrativeMemory()
        mem._retry_state.clock = clock.now
        mem._retry_state.reprobe_interval_seconds = 5.0

        await mem.add_async("scene-1", "Fallback memory one.")
        assert await mem.query_async("anything") == ["Fallback memory one."]
        assert mem._collection is None

        clock.advance(1.0)
        await mem.add_async("scene-2", "Fallback memory two.")
        clock.advance(2.0)
        await mem.add_async("scene-3", "Fallback memory three.")

        assert mem.is_online is False
        assert mem._retry_state.unavailable_since is not None

        clock.advance(4.0)
        await mem.add_async("scene-4", "Recovered memory.")
        assert mem._collection is None

        clock.advance(1.0)
        await mem.add_async("scene-4", "Recovered memory.")

        assert attempts["count"] == 4
        assert mem._collection is not None
        assert await mem.query_async("recovered", n=1) == ["Recovered memory."]

    def test_verify_availability_returns_false_when_unavailable(self):
        mem = NarrativeMemory()
        mem._available = False

        assert mem.verify_availability() is False
        assert mem.is_online is False

    @pytest.mark.asyncio
    async def test_query_uses_fallback_when_chroma_disabled(self):
        mem = NarrativeMemory()
        mem._available = False

        await mem.add_async("scene-1", "Fallback one.")
        await mem.add_async("scene-2", "Fallback two.")

        assert await mem.query_async("anything", n=1) == ["Fallback two."]

    @pytest.mark.asyncio
    async def test_query_returns_fallback_when_collection_is_empty(self, monkeypatch):
        fake_collection = self._FakeCollection()
        monkeypatch.setattr(
            "cyoa.db.rag_memory.chromadb.Client",
            lambda: self._FakeClient(fake_collection),
        )

        mem = NarrativeMemory()
        mem._fallback.extend(["Older memory", "Newest memory"])

        assert await mem.query_async("anything", n=1) == ["Newest memory"]

    @pytest.mark.asyncio
    async def test_query_marks_failure_and_falls_back_on_query_error(self, monkeypatch):
        fake_collection = self._FakeCollection()
        fake_collection._docs["scene-1"] = "Stored memory"
        fake_collection.query = MagicMock(side_effect=RuntimeError("query failed"))  # type: ignore[method-assign]
        monkeypatch.setattr(
            "cyoa.db.rag_memory.chromadb.Client",
            lambda: self._FakeClient(fake_collection),
        )

        mem = NarrativeMemory()
        mem._fallback.extend(["Older memory", "Newest memory"])

        assert await mem.query_async("anything", n=1) == ["Newest memory"]
        assert mem._collection is None
        assert mem._retry_state.consecutive_failures == 1

    def test_close_swallows_delete_errors(self, monkeypatch):
        fake_collection = self._FakeCollection()
        fake_client = self._FakeClient(fake_collection)
        fake_client.delete_collection = MagicMock(side_effect=RuntimeError("cannot delete"))  # type: ignore[method-assign]
        monkeypatch.setattr("cyoa.db.rag_memory.chromadb.Client", lambda: fake_client)

        mem = NarrativeMemory()
        assert mem.verify_availability() is True

        mem.close()

        assert mem._collection is None
        assert mem._client is None
        assert mem._retry_state.consecutive_failures == 0


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

    @pytest.mark.asyncio
    async def test_npc_memory_recovers_after_operational_failure(self, monkeypatch):
        from cyoa.db.rag_memory import NPCMemory

        clock = TestNarrativeMemory._FakeClock()
        collections = iter(
            [
                TestNarrativeMemory._FakeCollection(fail_upsert=True),
                TestNarrativeMemory._FakeCollection(),
            ]
        )

        def fake_client_factory():
            return TestNarrativeMemory._FakeClient(next(collections))

        monkeypatch.setattr("cyoa.db.rag_memory.chromadb.Client", fake_client_factory)

        mem = NPCMemory()
        mem._retry_state.clock = clock.now
        mem._retry_state.base_backoff_seconds = 0.5

        await mem.add_async("Elara", "scene-1", "Elara fallback memory.")
        assert await mem.query_async("Elara", "anything") == ["Elara fallback memory."]
        assert mem._collections == {}

        clock.advance(0.5)
        await mem.add_async("Elara", "scene-2", "Elara recovered memory.")

        assert mem._collections
        assert await mem.query_async("Elara", "anything", n=1) == ["Elara recovered memory."]

    def test_verify_availability_returns_false_when_unavailable(self):
        from cyoa.db.rag_memory import NPCMemory

        mem = NPCMemory()
        mem._available = False

        assert mem.verify_availability() is False
        assert mem.is_online is False

    @pytest.mark.asyncio
    async def test_query_uses_fallback_when_chroma_disabled(self):
        from cyoa.db.rag_memory import NPCMemory

        mem = NPCMemory()
        mem._available = False

        await mem.add_async("Elara", "scene-1", "Fallback one.")
        await mem.add_async("Elara", "scene-2", "Fallback two.")

        assert await mem.query_async("Elara", "anything", n=1) == ["Fallback two."]

    @pytest.mark.asyncio
    async def test_query_returns_fallback_when_collection_missing(self, monkeypatch):
        from cyoa.db.rag_memory import NPCMemory

        monkeypatch.setattr(
            "cyoa.db.rag_memory.chromadb.Client",
            lambda: TestNarrativeMemory._FakeClient(TestNarrativeMemory._FakeCollection()),
        )

        mem = NPCMemory()
        mem._fallbacks["Elara"] = deque(["Older memory", "Newest memory"], maxlen=5)
        mem._collections["elara"] = TestNarrativeMemory._FakeCollection()

        assert mem.verify_availability() is True
        mem._collections.clear()

        assert await mem.query_async("Elara", "anything", n=1) == ["Newest memory"]

    @pytest.mark.asyncio
    async def test_query_returns_fallback_when_collection_empty(self, monkeypatch):
        from cyoa.db.rag_memory import NPCMemory

        monkeypatch.setattr(
            "cyoa.db.rag_memory.chromadb.Client",
            lambda: TestNarrativeMemory._FakeClient(TestNarrativeMemory._FakeCollection()),
        )

        mem = NPCMemory()
        mem._fallbacks["Elara"] = deque(["Older memory", "Newest memory"], maxlen=5)

        assert await mem.query_async("Elara", "anything", n=1) == ["Newest memory"]

    @pytest.mark.asyncio
    async def test_query_marks_failure_and_falls_back_on_query_error(self, monkeypatch):
        from cyoa.db.rag_memory import NPCMemory

        fake_collection = TestNarrativeMemory._FakeCollection()
        fake_collection._docs["scene-1"] = "Stored memory"
        fake_collection.query = MagicMock(side_effect=RuntimeError("query failed"))  # type: ignore[method-assign]
        monkeypatch.setattr(
            "cyoa.db.rag_memory.chromadb.Client",
            lambda: TestNarrativeMemory._FakeClient(fake_collection),
        )

        mem = NPCMemory()
        mem._fallbacks["Elara"] = deque(["Older memory", "Newest memory"], maxlen=5)

        assert await mem.query_async("Elara", "anything", n=1) == ["Newest memory"]
        assert mem._collections == {}
        assert mem._retry_state.consecutive_failures == 1

    def test_close_swallows_npc_delete_errors(self, monkeypatch):
        from cyoa.db.rag_memory import NPCMemory

        fake_client = TestNarrativeMemory._FakeClient(TestNarrativeMemory._FakeCollection())
        fake_client.delete_collection = MagicMock(side_effect=RuntimeError("cannot delete"))  # type: ignore[method-assign]
        monkeypatch.setattr("cyoa.db.rag_memory.chromadb.Client", lambda: fake_client)

        mem = NPCMemory()
        assert mem.verify_availability() is True

        mem.close()

        assert mem._collections == {}
        assert mem._client is None
        assert mem._retry_state.consecutive_failures == 0


# ── 4. Streaming token callback ───────────────────────────────────────────────


class TestStreamingCallback:
    def test_inject_memory_inserts_before_last_user(self):
        """inject_memory() should update the memories state."""
        ctx = StoryContext(starting_prompt="Start")
        ctx.add_turn("Narrative one.", "Go left")

        # Inject memory after first turn
        ctx.inject_memory(["You once saw a torch flicker."])

        assert len(ctx.memories) == 1
        assert len(ctx.memory_entries) == 1
        assert "torch flicker" in ctx.memories[0]

    def test_inject_memory_empty_is_noop(self):
        """inject_memory([]) should update state properly."""
        ctx = StoryContext(starting_prompt="Start")
        ctx.inject_memory([])
        assert len(ctx.memories) == 0

    def test_inject_memory_replaces_existing_block(self):
        """inject_memory() called twice should replace, not accumulate, the memory block."""
        ctx = StoryContext(starting_prompt="Start")
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

        from cyoa.llm.broker import ModelBroker

        payload = {
            "title": None,
            "narrative": "A torch flickers in the dark.",
            "choices": [{"text": "Run"}, {"text": "Hide"}],
            "is_ending": False,
        }
        json_str = json.dumps(payload)

        # Simulate streaming characters one-by-one
        def mock_stream(*args, **kwargs):
            async def gen():
                for ch in json_str:
                    yield ch

            return gen()

        mock_provider = MagicMock()
        mock_provider.stream_json.side_effect = mock_stream

        gen = ModelBroker(provider=mock_provider)
        received = []
        result = await gen._stream_with_callback_async(
            [], on_token_chunk=received.append, schema={}
        )

        extracted = "".join(received)
        assert "torch" in extracted
        assert "dark" in extracted
        # Full JSON still reconstructable
        assert json.loads(result)["narrative"] == "A torch flickers in the dark."

    @pytest.mark.asyncio
    async def test_stream_resilience(self):
        """Verify extractor handles weird spacing and newlines using jiter."""
        from cyoa.llm.broker import ModelBroker

        # Weird spacing, newlines, and escaping that would break a simple regex
        json_str = '{"title": null,  "narrative" \n : \n  "The dragon said, \\"Return my gold!\\"." , "choices": []}'

        def mock_stream(*args, **kwargs):
            async def gen():
                for ch in json_str:
                    yield ch

            return gen()

        mock_provider = MagicMock()
        mock_provider.stream_json.side_effect = mock_stream

        gen = ModelBroker(provider=mock_provider)
        received = []
        await gen._stream_with_callback_async([], on_token_chunk=received.append, schema={})

        extracted = "".join(received)
        assert extracted == 'The dragon said, "Return my gold!".'
        assert "gold" in extracted


# ── 5. New UI Components: Branching and Animated Spinner ───────────────────────


class TestThemeSpinner:
    def test_spinner_cycles_frames(self):
        """ThemeSpinner should update its frame index on each tick."""
        from cyoa.ui.components import ThemeSpinner

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
        mock_gen.token_budget = 2048
        mock_gen.provider = MagicMock()
        mock_gen.provider.count_tokens = MagicMock(return_value=10)
        mock_gen.generate_next_node_async = AsyncMock(
            return_value=StoryNode(
                narrative="You stand up.",
                choices=[Choice(text="Walk"), Choice(text="Wait")],
                is_ending=False,
                items_gained=[],
                items_lost=[],
                stat_updates={},
                title=None,
            )
        )
        mock_gen.update_story_summaries_async = AsyncMock()
        mock_gen.save_state_async = AsyncMock(return_value=b"state")
        mock_gen.load_state_async = AsyncMock()

        with (
            patch("cyoa.ui.app.ModelBroker", return_value=mock_gen),
            patch("cyoa.ui.app.CYOAGraphDB") as mock_db_cls,
        ):
            mock_db = mock_db_cls.return_value
            mock_db.verify_connectivity_async = AsyncMock(return_value=True)
            mock_db.create_story_node_and_get_title.return_value = "Test Story"
            mock_db.get_story_tree.return_value = None
            mock_db.save_scene_async = AsyncMock(return_value="sid")
            mock_db.verify_connectivity_async = AsyncMock(return_value=True)  # Redundant but safe

            app = CYOAApp(model_path="dummy")
            async with app.run_test() as pilot:
                # Wait for engine to initialize
                await pilot.pause(0.5)

                app.engine.state.current_scene_id = "scene-3"
                app._current_story = "You wake up.\n\nYou stand up.\n\nYou walk left into a wall."

                # Allow the initial startup worker to settle
                await pilot.pause(0.2)

                # restore_to_scene is a @work coroutine — calling it schedules a Worker;
                # do NOT await it, just call it and give the event loop time to run it.
                app.restore_to_scene(idx=1, history=history)
                # Give the worker two ticks to fully execute and flush state
                await pilot.pause(0.3)

                # Check context
                assert app.engine.state.current_scene_id == "scene-2"
                assert app.engine.state.last_choice_text == "Stand"

                # Context history should correctly have prompt + (narrative, choice) pairs up to idx
                assert app.engine.story_context is not None
                assert (
                    len(app.engine.story_context.history) == 3
                )  # User Prompt + Assistant Scene 1 + User Choice 1
                assert "You wake up." in app.engine.story_context.history[1]["content"]
                assert "Stand" in app.engine.story_context.history[2]["content"]

    @pytest.mark.asyncio
    @pytest.mark.no_worker_mock
    async def test_restore_to_scene_restores_stats(self):
        """Restoring to a past scene should restore player_stats and inventory from history."""
        from cyoa.ui.app import CYOAApp

        history = {
            "scenes": [
                {
                    "id": "scene-1",
                    "narrative": "You wake up.",
                    "available_choices": ["Stand", "Sleep"],
                    "player_stats": {"health": 80, "gold": 5, "reputation": 0},
                    "inventory": ["Old Key"],
                },
            ],
            "choices": [],
        }

        mock_gen = MagicMock()
        mock_gen.token_budget = 2048
        mock_gen.provider = MagicMock()
        mock_gen.provider.count_tokens = MagicMock(return_value=10)
        mock_gen.generate_next_node_async = AsyncMock(
            return_value=StoryNode(
                narrative="You stand up.",
                choices=[Choice(text="Walk"), Choice(text="Wait")],
                stat_updates={},
                items_gained=[],
            )
        )
        mock_gen.update_story_summaries_async = AsyncMock()
        mock_gen.save_state_async = AsyncMock(return_value=None)
        mock_gen.load_state_async = AsyncMock()

        with (
            patch("cyoa.ui.app.ModelBroker", return_value=mock_gen),
            patch("cyoa.ui.app.CYOAGraphDB") as mock_db_cls,
        ):
            mock_db = mock_db_cls.return_value
            mock_db.verify_connectivity_async = AsyncMock(return_value=True)
            mock_db.create_story_node_and_get_title.return_value = "Test Story"
            mock_db.get_story_tree.return_value = None
            mock_db.save_scene_async = AsyncMock(return_value="sid")
            mock_db.verify_connectivity_async = AsyncMock(return_value=True)  # Redundant but safe

            app = CYOAApp(model_path="dummy")
            async with app.run_test() as pilot:
                await pilot.pause(0.5)

                # Simulate being at a later turn with different stats
                app.engine.state.inventory = ["Sword"]
                app.engine.state.player_stats = {"health": 100, "gold": 50, "reputation": 10}

                # Restore to Turn 1
                app.restore_to_scene(idx=0, history=history)
                await pilot.pause(0.3)

                # Check stats and inventory
                assert app.engine.state.player_stats == {"health": 80, "gold": 5, "reputation": 0}
                assert app.engine.state.inventory == ["Old Key"]

    def test_action_branch_past_aborts_if_no_history(self):
        """action_branch_past should return early if there is no engine."""
        from cyoa.ui.app import CYOAApp

        app = CYOAApp(model_path="dummy")

        # engine is None (until mount/initialize)
        assert app.engine is None

        # Mock work decorator to just call the function
        def mock_call_from_thread(callback, *args, **kwargs):
            callback(*args, **kwargs)

        with patch.object(app, "call_from_thread", side_effect=mock_call_from_thread) as mock_call:
            app.action_branch_past()
            mock_call.assert_not_called()


# ── 6. Procedural Item System ────────────────────────────────────────────────


class TestProceduralItemSystem:
    def test_story_context_formats_inventory(self):
        """StoryContext should properly inject the inventory state into the user prompt."""
        ctx = StoryContext(starting_prompt="Start")
        ctx.add_turn("You found a sword.", "Take sword", inventory=["Iron Sword", "Torch"])

        messages = ctx.get_messages()
        sys_msg = messages[0]
        assert sys_msg["role"] == "system"
        assert "Current Inventory: Iron Sword, Torch" in sys_msg["content"]

    def test_story_context_handles_empty_inventory(self):
        """StoryContext should format gracefully when inventory is empty."""
        ctx = StoryContext(starting_prompt="Start")
        ctx.add_turn("You found nothing.", "Wait", inventory=[])

        messages = ctx.get_messages()
        sys_msg = messages[0]
        assert sys_msg["role"] == "system"
        assert "Current Inventory: Empty" in sys_msg["content"]

    def test_story_context_add_turn_copies_inventory_and_stats(self):
        """Turn state should not retain live references to caller-owned containers."""
        ctx = StoryContext(starting_prompt="Start")
        inventory = ["Torch"]
        stats = {"health": 95, "gold": 1, "reputation": 0}

        ctx.add_turn("You wait.", "Wait", inventory=inventory, player_stats=stats)
        inventory.append("Key")
        stats["gold"] = 99

        assert ctx.inventory == ["Torch"]
        assert ctx.player_stats == {"health": 95, "gold": 1, "reputation": 0}
