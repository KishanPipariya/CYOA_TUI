import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from cyoa.core.models import Choice, StoryNode
from cyoa.llm.broker import ModelBroker, SpeculationCache, StoryContext
from cyoa.llm.providers import LLMProvider


def _node(text: str) -> StoryNode:
    return StoryNode(narrative=text, choices=[Choice(text="A"), Choice(text="B")])


def _make_provider() -> MagicMock:
    provider = MagicMock(spec=LLMProvider)
    provider.count_tokens = MagicMock(return_value=3)
    provider.generate_text = AsyncMock(return_value="summary")
    provider.save_state = AsyncMock(return_value=None)
    provider.load_state = AsyncMock(return_value=None)
    return provider


def test_speculation_cache_lru_and_clear_operations():
    cache = SpeculationCache(max_nodes=2, max_states=1)
    n1 = _node("n1")
    n2 = _node("n2")
    n3 = _node("n3")

    cache.set_node("s1", "c1", n1)
    cache.set_node("s2", "c2", n2)
    assert cache.get_node("s1", "c1") == n1  # refresh s1:c1 recency
    cache.set_node("s3", "c3", n3)

    assert cache.get_node("s2", "c2") is None
    assert cache.get_node("s1", "c1") == n1
    assert cache.get_node("s3", "c3") == n3

    cache.set_state("s1", "state-1")
    cache.set_state("s2", "state-2")
    assert cache.get_state("s1") is None
    assert cache.get_state("s2") == "state-2"

    cache.clear_nodes()
    assert cache.get_node("s1", "c1") is None
    assert cache.get_state("s2") == "state-2"

    cache.clear_all()
    assert cache.get_node("s3", "c3") is None
    assert cache.get_state("s2") is None


@pytest.mark.asyncio
async def test_model_broker_judge_mode_combines_narrator_and_extraction():
    provider = _make_provider()
    narrator = {
        "narrative": "You find a chest.",
        "title": "Treasure",
        "npcs_present": ["Guard"],
        "choices": [{"text": "Open it"}, {"text": "Leave it"}],
        "is_ending": False,
        "mood": "mysterious",
    }
    extraction = {"items_gained": ["Gold Coin"], "items_lost": [], "stat_updates": {"gold": 10}}
    provider.generate_json = AsyncMock(side_effect=[json.dumps(narrator), json.dumps(extraction)])

    broker = ModelBroker(provider=provider)
    broker.unified_mode = False
    node = await broker.generate_next_node_async(StoryContext("start"))

    assert node.narrative == "You find a chest."
    assert node.npcs_present == ["Guard"]
    assert node.items_gained == ["Gold Coin"]
    assert node.stat_updates == {"gold": 10}


@pytest.mark.asyncio
async def test_model_broker_judge_mode_extraction_failure_returns_empty_delta():
    provider = _make_provider()
    narrator = {
        "narrative": "A trap snaps shut.",
        "choices": [{"text": "Dodge"}, {"text": "Run"}],
        "is_ending": False,
        "mood": "combat",
    }
    provider.generate_json = AsyncMock(side_effect=[json.dumps(narrator), "not-json"])

    broker = ModelBroker(provider=provider)
    broker.unified_mode = False
    node = await broker.generate_next_node_async(StoryContext("start"))

    assert node.narrative == "A trap snaps shut."
    assert node.items_gained == []
    assert node.items_lost == []
    assert node.stat_updates == {}


@pytest.mark.asyncio
async def test_generate_legacy_summary_falls_back_to_plaintext():
    provider = _make_provider()
    provider.generate_text = AsyncMock(side_effect=RuntimeError("summary fail"))
    broker = ModelBroker(provider=provider)
    turns = [
        {"role": "assistant", "content": "A very long narrative " * 10},
        {"role": "user", "content": "I choose the left door"},
    ]

    summary = await broker.generate_legacy_summary_async(turns)

    assert "A very long narrative" in summary
    assert "I choose the left door" in summary
