import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from cyoa.core.engine import StoryEngine
from cyoa.core.events import Events, bus
from cyoa.core.models import Choice, Objective, StoryNode
from cyoa.core.runtime import EnginePhase
from cyoa.core.state import GameState
from cyoa.llm.broker import ModelBroker, StoryContext
from cyoa.llm.providers import LLMProvider


def _make_story_node(narrative: str = "N") -> StoryNode:
    return StoryNode(narrative=narrative, choices=[Choice(text="A"), Choice(text="B")])


def _make_broker_with_mock_provider() -> tuple[ModelBroker, MagicMock]:
    provider = MagicMock(spec=LLMProvider)
    provider.count_tokens = MagicMock(return_value=5)
    provider.generate_json = AsyncMock(
        return_value=json.dumps(
            {"narrative": "Generated", "choices": [{"text": "A"}, {"text": "B"}]}
        )
    )
    provider.save_state = AsyncMock(return_value=None)
    provider.load_state = AsyncMock(return_value=None)
    broker = ModelBroker(provider=provider)
    return broker, provider


@pytest.mark.asyncio
async def test_engine_retry_uses_last_choice_text():
    broker, _provider = _make_broker_with_mock_provider()
    engine = StoryEngine(broker=broker, starting_prompt="Start")
    engine.state.last_choice_text = "Go North"
    engine._generate_next = AsyncMock(return_value=None)  # type: ignore[method-assign]

    await engine.retry()

    engine._generate_next.assert_awaited_once_with(choice_text="Go North")


@pytest.mark.asyncio
async def test_engine_generate_next_emits_error_event_on_failure():
    broker, _provider = _make_broker_with_mock_provider()
    engine = StoryEngine(broker=broker, starting_prompt="Start")
    engine.story_context = StoryContext("Start", token_counter=lambda _x: 1)
    engine.rag.retrieve_memories = AsyncMock(return_value=[])  # type: ignore[method-assign]
    engine.rag.index_node = AsyncMock(return_value=None)  # type: ignore[method-assign]
    broker.generate_next_node_async = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

    errors: list[str] = []
    bus.subscribe(Events.ERROR_OCCURRED, lambda error: errors.append(error))

    await engine._generate_next(choice_text="anything")

    assert errors == ["boom"]


@pytest.mark.asyncio
async def test_engine_generate_next_uses_cached_node_without_generation():
    broker, _provider = _make_broker_with_mock_provider()
    broker.generate_next_node_async = AsyncMock()  # type: ignore[method-assign]
    broker.save_state_async = AsyncMock(return_value=b"kv-state")  # type: ignore[method-assign]

    engine = StoryEngine(broker=broker, starting_prompt="Start")
    engine.story_context = StoryContext("Start", token_counter=lambda _x: 1)
    engine.state.current_scene_id = "scene-1"
    engine.state.turn_count = 2
    engine.rag.retrieve_memories = AsyncMock(return_value=[])  # type: ignore[method-assign]
    engine.rag.index_node = AsyncMock(return_value=None)  # type: ignore[method-assign]

    cached_node = StoryNode(
        narrative="Cached path",
        choices=[Choice(text="Go"), Choice(text="Wait")],
    )
    engine.speculation_cache.set_node("scene-1", "Open door", cached_node)

    statuses: list[str] = []
    completions: list[str] = []
    bus.subscribe(Events.STATUS_MESSAGE, lambda message: statuses.append(message))
    bus.subscribe(Events.NODE_COMPLETED, lambda node: completions.append(node.narrative))

    await engine._generate_next(choice_text="Open door")

    broker.generate_next_node_async.assert_not_awaited()
    broker.save_state_async.assert_awaited_once()
    engine.rag.index_node.assert_awaited_once_with("scene-1", cached_node)
    assert engine.speculation_cache.get_state("scene-1") == b"kv-state"
    assert statuses == ["✨ Recalling future memories..."]
    assert completions == ["Cached path"]
    assert engine.state.current_node == cached_node
    assert engine.state.current_scene_id == "scene-1"


@pytest.mark.asyncio
async def test_engine_generate_next_first_turn_persists_title_and_scene():
    broker, _provider = _make_broker_with_mock_provider()
    broker.generate_next_node_async = AsyncMock(  # type: ignore[method-assign]
        return_value=StoryNode(
            narrative="Opening scene",
            choices=[Choice(text="Enter"), Choice(text="Leave")],
            title="Fresh Adventure",
        )
    )
    broker.save_state_async = AsyncMock(return_value=None)  # type: ignore[method-assign]

    db = MagicMock()
    db.create_story_node_and_get_title.return_value = "Fresh Adventure"
    db.save_scene_async = AsyncMock(return_value="scene-db-1")

    engine = StoryEngine(broker=broker, starting_prompt="Start", db=db)
    engine.story_context = StoryContext("Start", token_counter=lambda _x: 1)
    engine.rag.retrieve_memories = AsyncMock(return_value=[])  # type: ignore[method-assign]
    engine.rag.index_node = AsyncMock(return_value=None)  # type: ignore[method-assign]

    titles: list[str | None] = []
    completions: list[str] = []
    bus.subscribe(Events.STORY_TITLE_GENERATED, lambda title: titles.append(title))
    bus.subscribe(Events.NODE_COMPLETED, lambda node: completions.append(node.narrative))

    await engine._generate_next()

    db.create_story_node_and_get_title.assert_called_once_with("Fresh Adventure")
    engine.rag.index_node.assert_awaited_once()
    indexed_scene_id, indexed_node = engine.rag.index_node.await_args.args
    assert isinstance(indexed_scene_id, str)
    assert indexed_scene_id
    assert indexed_node.narrative == "Opening scene"
    db.save_scene_async.assert_awaited_once_with(
        narrative="Opening scene",
        available_choices=["Enter", "Leave"],
        story_title="Fresh Adventure",
        source_scene_id=None,
        choice_text=None,
        player_stats=engine.state.player_stats,
        inventory=engine.state.inventory,
        mood="default",
    )
    assert titles == ["Fresh Adventure"]
    assert completions == ["Opening scene"]
    assert engine.state.story_title == "Fresh Adventure"
    assert engine.state.current_scene_id == "scene-db-1"


def test_engine_save_and_load_roundtrip():
    broker, _provider = _make_broker_with_mock_provider()
    engine = StoryEngine(broker=broker, starting_prompt="Start")
    engine.story_context = StoryContext("Start")
    engine.story_context.history = [
        {"role": "user", "content": "Start"},
        {"role": "assistant", "content": "You wake up."},
    ]
    engine.state.story_title = "Title"
    engine.state.turn_count = 3
    engine.state.inventory = ["Key"]
    engine.state.player_stats = {"health": 88, "gold": 12, "reputation": 3}
    engine.state.current_scene_id = "scene-2"
    engine.state.last_choice_text = "Open door"
    engine.state.current_node = _make_story_node("Node")

    data = engine.get_save_data()

    loaded = StoryEngine(broker=broker, starting_prompt="IgnoreThis")
    loaded.load_save_data(data)

    assert loaded.starting_prompt == "IgnoreThis"
    assert loaded.story_context is not None
    assert loaded.story_context.starting_prompt == "Start"
    assert loaded.story_context.history[1]["content"] == "You wake up."
    assert loaded.state.story_title == "Title"
    assert loaded.state.turn_count == 3
    assert loaded.state.inventory == ["Key"]
    assert loaded.state.player_stats["health"] == 88
    assert loaded.state.current_scene_id == "scene-2"
    assert loaded.state.last_choice_text == "Open door"


def test_engine_save_payload_uses_current_ui_state_contract():
    broker, _provider = _make_broker_with_mock_provider()
    engine = StoryEngine(broker=broker, starting_prompt="Start")
    engine.story_context = StoryContext("Start")
    engine.state.current_node = _make_story_node("Node")

    data = engine.get_save_data()

    assert "version" not in data
    assert "current_story_text" not in data


def test_engine_get_save_data_copies_context_history():
    broker, _provider = _make_broker_with_mock_provider()
    engine = StoryEngine(broker=broker, starting_prompt="Start")
    engine.story_context = StoryContext("Start")
    engine.story_context.history = [{"role": "user", "content": "Start"}]
    engine.state.current_node = _make_story_node("Node")

    data = engine.get_save_data()
    data["context_history"].append({"role": "assistant", "content": "Mutated"})

    assert engine.story_context.history == [{"role": "user", "content": "Start"}]


def test_game_state_redo_reapplies_last_undone_snapshot():
    state = GameState()
    node = _make_story_node("Original")
    redone = _make_story_node("Redone")
    state.current_node = node
    state.turn_count = 2
    state.create_undo_snapshot({"story_context_history": [{"role": "user", "content": "Start"}]})
    state.current_node = redone
    state.turn_count = 3

    assert state.undo() is True
    assert state.turn_count == 2
    assert state.current_node == node

    assert state.redo() is True
    assert state.turn_count == 3
    assert state.current_node == redone


def test_game_state_bookmark_round_trip_survives_save_payload():
    state = GameState()
    state.turn_count = 4
    state.current_node = _make_story_node("Checkpoint")
    assert state.create_bookmark("Before Boss", extra_data={"story_context_history": []}) is True
    payload = state.get_save_data()

    restored = GameState()
    restored.load_save_data(payload)
    restored.turn_count = 6
    restored.current_node = _make_story_node("Later")

    assert restored.restore_bookmark("Before Boss") is True
    assert restored.turn_count == 4
    assert restored.current_node is not None
    assert restored.current_node.narrative == "Checkpoint"


def test_game_state_get_save_data_copies_inventory_and_stats():
    state = GameState(inventory=["Torch"], player_stats={"health": 90, "gold": 4, "reputation": 1})

    payload = state.get_save_data()
    payload["inventory"].append("Key")
    payload["player_stats"]["gold"] = 99

    assert state.inventory == ["Torch"]
    assert state.player_stats == {"health": 90, "gold": 4, "reputation": 1}


@pytest.mark.asyncio
async def test_engine_shutdown_cancels_summarization_and_closes_resources():
    broker, _provider = _make_broker_with_mock_provider()
    db = MagicMock()
    engine = StoryEngine(broker=broker, starting_prompt="Start", db=db)
    engine.rag.memory.close = MagicMock()  # type: ignore[method-assign]
    engine.rag.npc_memory.close = MagicMock()  # type: ignore[method-assign]
    task = asyncio.create_task(asyncio.sleep(10))
    engine._pending_summarization_task = task

    engine.shutdown()
    await asyncio.sleep(0)

    assert task.cancelled()
    engine.rag.memory.close.assert_called_once_with()
    engine.rag.npc_memory.close.assert_called_once_with()
    db.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_engine_restart_resets_rag_and_cancels_inflight_summarization():
    broker, _provider = _make_broker_with_mock_provider()
    engine = StoryEngine(broker=broker, starting_prompt="Start")
    engine.initialize = AsyncMock(return_value=None)  # type: ignore[method-assign]
    engine.rag.reset = AsyncMock(return_value=None)  # type: ignore[method-assign]
    task = asyncio.create_task(asyncio.sleep(10))
    engine._pending_summarization_task = task

    await engine.restart()
    await asyncio.sleep(0)

    engine.rag.reset.assert_awaited_once_with()
    engine.initialize.assert_awaited_once_with()
    assert task.cancelled()


@pytest.mark.asyncio
async def test_engine_generate_next_ignores_runtime_event_subscriber_failures():
    broker, _provider = _make_broker_with_mock_provider()
    broker.generate_next_node_async = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda _ctx, on_token_chunk: (
            on_token_chunk("chunk"),
            StoryNode(narrative="Recovered", choices=[Choice(text="A"), Choice(text="B")]),
        )[1]
    )
    broker.save_state_async = AsyncMock(return_value=None)  # type: ignore[method-assign]

    engine = StoryEngine(broker=broker, starting_prompt="Start")
    engine.story_context = StoryContext("Start", token_counter=lambda _x: 1)
    engine.state.turn_count = 2
    engine.rag.retrieve_memories = AsyncMock(return_value=[])  # type: ignore[method-assign]
    engine.rag.index_node = AsyncMock(return_value=None)  # type: ignore[method-assign]

    bus.subscribe(Events.TOKEN_STREAMED, lambda token: (_ for _ in ()).throw(RuntimeError(token)))
    bus.subscribe(Events.NODE_COMPLETED, lambda node: (_ for _ in ()).throw(RuntimeError(node.narrative)))

    await engine._generate_next(choice_text="anything")

    assert engine.state.current_node is not None
    assert engine.state.current_node.narrative == "Recovered"


@pytest.mark.asyncio
async def test_engine_make_choice_ignores_runtime_event_subscriber_failures():
    broker, _provider = _make_broker_with_mock_provider()
    broker.generate_next_node_async = AsyncMock(  # type: ignore[method-assign]
        return_value=StoryNode(narrative="After choice", choices=[Choice(text="A"), Choice(text="B")])
    )
    broker.save_state_async = AsyncMock(return_value=None)  # type: ignore[method-assign]

    engine = StoryEngine(broker=broker, starting_prompt="Start")
    engine.story_context = StoryContext("Start", token_counter=lambda _x: 1)
    engine.state.current_node = _make_story_node("Current")
    engine.state.current_scene_id = "scene-1"
    engine.state.turn_count = 1
    engine.rag.retrieve_memories = AsyncMock(return_value=[])  # type: ignore[method-assign]
    engine.rag.index_node = AsyncMock(return_value=None)  # type: ignore[method-assign]

    bus.subscribe(
        Events.CHOICE_MADE,
        lambda choice_text: (_ for _ in ()).throw(RuntimeError(choice_text)),
    )

    await engine.make_choice("Go North")

    assert engine.state.turn_count == 2
    assert engine.state.last_choice_text == "Go North"
    assert engine.state.current_node is not None
    assert engine.state.current_node.narrative == "After choice"


def test_engine_load_save_data_accepts_versionless_payload():
    broker, _provider = _make_broker_with_mock_provider()
    loaded = StoryEngine(broker=broker, starting_prompt="IgnoreThis")

    data = {
        "starting_prompt": "Start",
        "context_history": [{"role": "user", "content": "Start"}],
        "story_title": "Versionless",
        "turn_count": 2,
        "inventory": ["Coin"],
        "player_stats": {"health": 99, "gold": 1, "reputation": 0},
        "current_scene_id": "scene-1",
        "last_choice_text": "Look",
    }

    loaded.load_save_data(data)

    assert loaded.story_context is not None
    assert loaded.story_context.starting_prompt == "Start"
    assert loaded.state.story_title == "Versionless"
    assert loaded.state.turn_count == 2
    assert loaded.state.inventory == ["Coin"]


def test_engine_load_save_data_ignores_malformed_optional_payloads():
    broker, _provider = _make_broker_with_mock_provider()
    loaded = StoryEngine(broker=broker, starting_prompt="Fallback")

    loaded.load_save_data(
        {
            "starting_prompt": 123,
            "context_history": "not-a-list",
            "turn_count": "bad",
            "inventory": "Coin",
            "player_stats": {"health": "oops", "gold": 4},
            "current_node": {"choices": "broken"},
            "timeline_metadata": ["bad", {"kind": "branch_restore", "restored_turn": "3"}],
        }
    )

    assert loaded.story_context is not None
    assert loaded.story_context.starting_prompt == "Fallback"
    assert loaded.story_context.history == []
    assert loaded.state.turn_count == 1
    assert loaded.state.inventory == []
    assert loaded.state.player_stats == {"health": 100, "gold": 4, "reputation": 0}
    assert loaded.state.current_node is None
    assert loaded.state.timeline_metadata == [{"kind": "branch_restore", "restored_turn": 3}]


def test_engine_save_and_load_roundtrip_preserves_extended_world_state():
    broker, _provider = _make_broker_with_mock_provider()
    engine = StoryEngine(
        broker=broker,
        starting_prompt="Start",
        initial_world_state={
            "inventory": ["Torch"],
            "player_stats": {"health": 95, "gold": 3, "reputation": 1},
            "objectives": [{"id": "escape", "text": "Escape", "status": "active"}],
            "faction_reputation": {"Wardens": -2},
            "npc_affinity": {"Mira": 2},
            "story_flags": ["cell_opened"],
        },
        initial_prompt_config={
            "goals": ["Survive"],
            "directives": ["Honor locked choices."],
        },
    )
    engine.story_context = StoryContext("Start")
    engine._apply_initial_state()
    engine.state.current_node = _make_story_node("Node")

    data = engine.get_save_data()

    loaded = StoryEngine(broker=broker, starting_prompt="Start")
    loaded.load_save_data(data)

    assert loaded.state.objectives == [Objective(id="escape", text="Escape", status="active")]
    assert loaded.state.faction_reputation == {"Wardens": -2}
    assert loaded.state.npc_affinity == {"Mira": 2}
    assert loaded.state.story_flags == {"cell_opened"}
    assert loaded.story_context is not None
    assert loaded.story_context.goals == ["Survive"]
    assert loaded.story_context.directives == ["Honor locked choices."]


@pytest.mark.asyncio
async def test_engine_branch_to_scene_uses_cached_provider_state():
    broker, _provider = _make_broker_with_mock_provider()
    broker.load_state_async = AsyncMock(return_value=None)  # type: ignore[method-assign]
    engine = StoryEngine(broker=broker, starting_prompt="Start")
    engine.rag.rebuild_async = AsyncMock(return_value=None)  # type: ignore[method-assign]

    history = {
        "scenes": [
            {
                "id": "scene-1",
                "narrative": "First",
                "available_choices": ["A", "B"],
                "inventory": ["Torch"],
                "player_stats": {"health": 90, "gold": 1, "reputation": 0},
            },
            {
                "id": "scene-2",
                "narrative": "Second",
                "available_choices": ["C", "D"],
                "inventory": ["Torch", "Key"],
                "player_stats": {"health": 80, "gold": 2, "reputation": 1},
            },
        ],
        "choices": ["A"],
    }
    engine.speculation_cache.set_state("scene-2", b"cached-state")

    node_events: list[str] = []
    bus.subscribe(Events.NODE_COMPLETED, lambda node: node_events.append(node.narrative))

    await engine.branch_to_scene(1, history)

    broker.load_state_async.assert_awaited_once_with(b"cached-state")
    assert engine.state.current_scene_id == "scene-2"
    assert engine.state.last_choice_text == "A"
    assert engine.state.turn_count == 2
    assert engine.state.inventory == ["Torch", "Key"]
    assert engine.state.player_stats["health"] == 80
    assert engine.state.current_node is not None
    assert [c.text for c in engine.state.current_node.choices] == ["C", "D"]
    assert engine.state.timeline_metadata == [
        {
            "kind": "branch_restore",
            "source_scene_id": None,
            "target_scene_id": "scene-2",
            "restored_turn": 2,
        }
    ]
    assert node_events == ["Second"]


def test_game_state_apply_node_updates_ignores_noop_changes():
    state = GameState(inventory=["Key"], player_stats={"health": 100, "gold": 0, "reputation": 0})
    node = StoryNode(
        narrative="No change.",
        choices=[Choice(text="Wait"), Choice(text="Look")],
        items_gained=["Key"],  # duplicate
        items_lost=["Missing"],  # absent
        stat_updates={"health": 0},  # no-op
    )

    stats_events: list[dict[str, int]] = []
    inv_events: list[list[str]] = []
    bus.subscribe(Events.STATS_UPDATED, lambda stats: stats_events.append(stats))
    bus.subscribe(Events.INVENTORY_UPDATED, lambda inventory: inv_events.append(inventory))

    state.apply_node_updates(node)

    assert stats_events == []
    assert inv_events == []
    assert state.inventory == ["Key"]
    assert state.player_stats["health"] == 100
    assert state.current_node == node


def test_game_state_load_save_data_emits_title_and_node_events():
    state = GameState()
    data = {
        "story_title": "Restored",
        "turn_count": 2,
        "inventory": ["Gem"],
        "player_stats": {"health": 75, "gold": 3, "reputation": 1},
        "current_scene_id": "scene-x",
        "last_choice_text": "Take gem",
        "current_node": _make_story_node("Restored node").model_dump(),
    }

    emitted_titles: list[str | None] = []
    emitted_nodes: list[str] = []
    bus.subscribe(Events.STORY_TITLE_GENERATED, lambda title: emitted_titles.append(title))
    bus.subscribe(Events.NODE_COMPLETED, lambda node: emitted_nodes.append(node.narrative))

    state.load_save_data(data)

    assert state.story_title == "Restored"
    assert state.turn_count == 2
    assert state.inventory == ["Gem"]
    assert state.player_stats["health"] == 75
    assert emitted_titles == ["Restored"]
    assert emitted_nodes == ["Restored node"]


def test_game_state_seed_world_state_deduplicates_and_copies_models():
    state = GameState()
    objective = Objective(id="escape", text="Escape", status="active")

    state.seed_world_state(
        inventory=["Torch", "Torch", "Key"],
        player_stats={"health": "88", "gold": 4, "reputation": 2},
        objectives=[objective],
        faction_reputation={"Guild": 3},
        npc_affinity={"Mira": 5},
        story_flags={"met_mira"},
    )

    objective.status = "completed"

    assert state.inventory == ["Torch", "Key"]
    assert state.player_stats == {"health": 88, "gold": 4, "reputation": 2}
    assert state.objectives == [Objective(id="escape", text="Escape", status="active")]
    assert state.faction_reputation == {"Guild": 3}
    assert state.npc_affinity == {"Mira": 5}
    assert state.story_flags == {"met_mira"}


def test_game_state_apply_node_updates_emits_world_state_for_objectives_relationships_and_flags():
    state = GameState()
    node = StoryNode(
        narrative="World changes.",
        choices=[Choice(text="Continue"), Choice(text="Wait")],
        objectives_updated=[Objective(id="escape", text="Escape", status="active")],
        faction_updates={"Guild": 2},
        npc_affinity_updates={"Mira": -1},
        story_flags_set=["met_mira"],
        story_flags_cleared=["missing-flag"],
    )

    world_events: list[dict[str, object]] = []
    bus.subscribe(Events.WORLD_STATE_UPDATED, lambda state: world_events.append(state))

    state.apply_node_updates(node)

    assert state.objectives == [Objective(id="escape", text="Escape", status="active")]
    assert state.faction_reputation == {"Guild": 2}
    assert state.npc_affinity == {"Mira": -1}
    assert state.story_flags == {"met_mira"}
    assert world_events == [state.get_world_state()]


def test_game_state_load_save_data_coerces_extended_world_fields():
    state = GameState()

    state.load_save_data(
        {
            "turn_count": True,
            "inventory": ["Torch", 7, "Key"],
            "player_stats": {"health": "91", "gold": False, "luck": "5"},
            "timeline_metadata": [
                "bad",
                {"kind": "branch_restore", "source_scene_id": "scene-1", "restored_turn": 2.8},
                {"kind": None},
            ],
            "objectives": [
                {"id": "escape", "text": "Escape", "status": "active"},
                {"id": "broken"},
            ],
            "faction_reputation": {"Guild": "4", "Broken": True},
            "npc_affinity": {"Mira": 2.2, "Broken": object()},
            "story_flags": ["met_mira", "", 9],
            "current_node": {"choices": "broken"},
        }
    )

    assert state.turn_count == 1
    assert state.inventory == ["Torch", "Key"]
    assert state.player_stats == {"health": 91, "gold": 0, "reputation": 0, "luck": 5}
    assert state.timeline_metadata == [
        {
            "kind": "branch_restore",
            "source_scene_id": "scene-1",
            "restored_turn": 2,
        }
    ]
    assert state.objectives == [Objective(id="escape", text="Escape", status="active")]
    assert state.faction_reputation == {"Guild": 4}
    assert state.npc_affinity == {"Mira": 2}
    assert state.story_flags == {"met_mira"}
    assert state.current_node is None


def test_engine_load_save_data_tracks_phase_transitions():
    broker, _provider = _make_broker_with_mock_provider()
    engine = StoryEngine(broker=broker, starting_prompt="Fallback")

    transitions: list[tuple[str, str, str]] = []
    bus.subscribe(
        Events.ENGINE_PHASE_CHANGED,
        lambda transition: transitions.append(
            (transition.from_phase.value, transition.to_phase.value, transition.reason)
        ),
    )

    engine.load_save_data(
        {
            "starting_prompt": "Start",
            "context_history": [{"role": "user", "content": "Start"}],
            "turn_count": 2,
        }
    )

    assert transitions == [
        ("idle", "restoring", "load_save_data"),
        ("restoring", "ready", "load_completed"),
    ]
    assert engine.phase is EnginePhase.READY
