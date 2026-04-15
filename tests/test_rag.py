from unittest.mock import AsyncMock, MagicMock

import pytest

from cyoa.core.models import Choice, StoryNode
from cyoa.core.rag import RAGManager
from cyoa.llm.broker import StoryContext


def _node(
    narrative: str = "The party enters the ruins.",
    npcs_present: list[str] | None = None,
) -> StoryNode:
    return StoryNode(
        narrative=narrative,
        choices=[Choice(text="Advance"), Choice(text="Retreat")],
        npcs_present=npcs_present or [],
    )


@pytest.mark.asyncio
async def test_retrieve_memories_returns_empty_without_current_narrative() -> None:
    memory = MagicMock()
    npc_memory = MagicMock()
    manager = RAGManager(memory=memory, npc_memory=npc_memory)

    result = await manager.retrieve_memories(None, StoryContext("Start"))

    assert result == []
    memory.get_recent_async.assert_not_called()
    memory.query_async.assert_not_called()
    npc_memory.query_async.assert_not_called()


@pytest.mark.asyncio
async def test_retrieve_memories_separates_scene_chapter_and_entity_memories() -> None:
    memory = MagicMock()
    memory.get_recent_async = AsyncMock(
        return_value=[
            "The torchlight fades behind the party.",
            "Mira warns that the gate is trapped.",
        ]
    )
    memory.query_async = AsyncMock(
        return_value=[
            "Mira warns that the gate is trapped.",
            "An old oath binds Captain Varo to the salt vault.",
            "The hidden vault lies beneath the drowned chapel.",
        ]
    )
    npc_memory = MagicMock()
    npc_memory.query_async = AsyncMock(
        side_effect=[
            [
                "An old oath binds Captain Varo to the salt vault.",
                "Mira distrusts the crown and the regent.",
            ],
            ["Captain Varo lost the map"],
        ]
    )
    manager = RAGManager(memory=memory, npc_memory=npc_memory)
    ctx = StoryContext("Start")
    node = _node(npcs_present=["Mira", "Captain Varo"])

    result = await manager.retrieve_memories(node, ctx)

    assert result == [
        "The torchlight fades behind the party.",
        "Mira warns that the gate is trapped.",
        "An old oath binds Captain Varo to the salt vault.",
        "The hidden vault lies beneath the drowned chapel.",
        "Mira distrusts the crown and the regent.",
        "Captain Varo lost the map",
    ]
    assert ctx.memories == result
    assert [entry.category for entry in ctx.memory_entries] == [
        "scene",
        "scene",
        "chapter",
        "chapter",
        "entity",
        "entity",
    ]
    assert ctx.memory_entries[4].source == "Mira"
    assert ctx.memory_entries[5].source == "Captain Varo"
    memory.get_recent_async.assert_awaited_once_with(n=2, exclude_text="The party enters the ruins.")
    memory.query_async.assert_awaited_once_with("The party enters the ruins.", n=3)
    assert npc_memory.query_async.await_args_list[0].args == ("Mira", "The party enters the ruins.")
    assert npc_memory.query_async.await_args_list[0].kwargs == {"n": 2}
    assert npc_memory.query_async.await_args_list[1].args == (
        "Captain Varo",
        "The party enters the ruins.",
    )
    assert npc_memory.query_async.await_args_list[1].kwargs == {"n": 2}


@pytest.mark.asyncio
async def test_retrieve_memories_filters_current_scene_duplicates() -> None:
    memory = MagicMock()
    memory.get_recent_async = AsyncMock(
        return_value=[
            "The party enters the ruins.",
            "A bell tolls from the crypt.",
        ]
    )
    memory.query_async = AsyncMock(
        return_value=[
            "The party enters the ruins.",
            "A bell tolls from the crypt.",
            "The crown was last seen in the marsh.",
        ]
    )
    npc_memory = MagicMock()
    npc_memory.query_async = AsyncMock(
        return_value=[
            "The party enters the ruins.",
            "The crown was last seen in the marsh.",
            "Mira hid the key beneath the altar.",
        ]
    )
    manager = RAGManager(memory=memory, npc_memory=npc_memory)
    ctx = StoryContext("Start")

    result = await manager.retrieve_memories(_node(npcs_present=["Mira"]), ctx)

    assert result == [
        "A bell tolls from the crypt.",
        "The crown was last seen in the marsh.",
        "Mira hid the key beneath the altar.",
    ]
    assert [entry.category for entry in ctx.memory_entries] == ["scene", "chapter", "entity"]


@pytest.mark.asyncio
async def test_index_node_indexes_narrative_and_npc_memories() -> None:
    memory = MagicMock()
    memory.add_async = AsyncMock(return_value=None)
    npc_memory = MagicMock()
    npc_memory.add_async = AsyncMock(return_value=None)
    manager = RAGManager(memory=memory, npc_memory=npc_memory)
    node = _node(npcs_present=["Mira", "Captain Varo"])

    await manager.index_node("scene-7", node)

    memory.add_async.assert_awaited_once_with("scene-7", "The party enters the ruins.")
    assert npc_memory.add_async.await_args_list[0].args == (
        "Mira",
        "scene-7",
        "The party enters the ruins.",
    )
    assert npc_memory.add_async.await_args_list[1].args == (
        "Captain Varo",
        "scene-7",
        "The party enters the ruins.",
    )


@pytest.mark.asyncio
async def test_reset_closes_existing_memories_and_replaces_them(monkeypatch: pytest.MonkeyPatch) -> None:
    old_memory = MagicMock()
    old_npc_memory = MagicMock()
    new_memory = MagicMock()
    new_npc_memory = MagicMock()
    manager = RAGManager(memory=old_memory, npc_memory=old_npc_memory)

    monkeypatch.setattr("cyoa.core.rag.NarrativeMemory", lambda: new_memory)
    monkeypatch.setattr("cyoa.core.rag.NPCMemory", lambda: new_npc_memory)

    await manager.reset()

    old_memory.close.assert_called_once_with()
    old_npc_memory.close.assert_called_once_with()
    assert manager.memory is new_memory
    assert manager.npc_memory is new_npc_memory


@pytest.mark.asyncio
async def test_rebuild_async_resets_then_reindexes_history(monkeypatch: pytest.MonkeyPatch) -> None:
    memory = MagicMock()
    memory.add_async = AsyncMock(return_value=None)
    npc_memory = MagicMock()
    npc_memory.add_async = AsyncMock(return_value=None)
    manager = RAGManager(memory=memory, npc_memory=npc_memory)
    reset = AsyncMock(return_value=None)
    monkeypatch.setattr(manager, "reset", reset)

    await manager.rebuild_async(
        [
            {"id": "scene-1", "narrative": "A bell tolls.", "npcs_present": ["Mira"]},
            {"id": "scene-2", "narrative": "The gate opens."},
        ]
    )

    reset.assert_awaited_once_with()
    assert memory.add_async.await_args_list[0].args == ("scene-1", "A bell tolls.")
    assert memory.add_async.await_args_list[1].args == ("scene-2", "The gate opens.")
    npc_memory.add_async.assert_awaited_once_with("Mira", "scene-1", "A bell tolls.")
