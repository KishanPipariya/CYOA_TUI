import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from cyoa.core.engine import StoryEngine
from cyoa.core.events import Events, bus
from cyoa.core.models import Choice, StoryNode
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
