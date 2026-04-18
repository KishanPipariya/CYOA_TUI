import logging
from typing import Any

from cyoa.core.models import StoryNode
from cyoa.core.ports import (
    NarrativeMemoryFactory,
    NarrativeMemoryStore,
    NPCMemoryFactory,
    NPCMemoryStore,
)
from cyoa.db.rag_memory import NarrativeMemory, NPCMemory
from cyoa.llm.broker import MemoryEntry, StoryContext

logger = logging.getLogger(__name__)


def _append_unique_memory(
    text: str,
    *,
    category: str,
    reason: str,
    entries: list[MemoryEntry],
    seen: set[str],
    source: str | None = None,
    exclude: set[str] | None = None,
) -> None:
    normalized = text.strip()
    if not normalized:
        return
    if exclude and normalized in exclude:
        return
    if normalized in seen:
        return
    entries.append(MemoryEntry(text=normalized, category=category, reason=reason, source=source))
    seen.add(normalized)


class RAGManager:
    """Handles the Retrieval-Augmented Generation (RAG) tasks for the StoryEngine.

    Separates narrative and NPC memory retrieval and indexing from the core engine flow.
    """

    def __init__(
        self,
        memory: NarrativeMemoryStore | None = None,
        npc_memory: NPCMemoryStore | None = None,
        memory_factory: NarrativeMemoryFactory | None = None,
        npc_memory_factory: NPCMemoryFactory | None = None,
    ) -> None:
        self._memory_factory = memory_factory or (lambda: NarrativeMemory())
        self._npc_memory_factory = npc_memory_factory or (lambda: NPCMemory())
        self.memory = memory or self._memory_factory()
        self.npc_memory = npc_memory or self._npc_memory_factory()

    async def retrieve_memories(
        self,
        current_node: StoryNode | None,
        story_context: StoryContext,
    ) -> list[str]:
        """Query relevant memories based on the current storyline and inject them into context."""
        last_narrative = current_node.narrative if current_node else None
        if not last_narrative:
            return []

        entries: list[MemoryEntry] = []
        seen: set[str] = set()
        excluded = {last_narrative.strip()}

        # 1. Retrieve short-term scene continuity from the latest prior scenes.
        recent_memories = await self.memory.get_recent_async(n=2, exclude_text=last_narrative)
        for memory in recent_memories:
            _append_unique_memory(
                memory,
                category="scene",
                reason="Recent scene continuity with the immediately preceding beats.",
                entries=entries,
                seen=seen,
                exclude=excluded,
            )

        # 2. Retrieve longer-range chapter context via semantic matching.
        chapter_memories = await self.memory.query_async(last_narrative, n=3)
        for memory in chapter_memories:
            _append_unique_memory(
                memory,
                category="chapter",
                reason="Semantically relevant older chapter context for continuity.",
                entries=entries,
                seen=seen,
                exclude=excluded,
            )

        # 3. Retrieve character-specific (NPC/entity) memories.
        if current_node and getattr(current_node, "npcs_present", None):
            for npc in current_node.npcs_present:
                npc_mems = await self.npc_memory.query_async(npc, last_narrative, n=2)
                for mem in npc_mems:
                    _append_unique_memory(
                        mem,
                        category="entity",
                        source=npc,
                        reason=f"{npc} is present in the current scene.",
                        entries=entries,
                        seen=seen,
                        exclude=excluded,
                    )

        memories = [entry.text for entry in entries]
        for entry in entries:
            logger.debug(
                "Injected %s memory%s: %s | reason=%s",
                entry.category,
                f" ({entry.source})" if entry.source else "",
                entry.text,
                entry.reason,
            )

        # 4. Inject into the LLM context.
        story_context.inject_memory(memories, memory_entries=entries)
        return memories

    async def index_node(
        self,
        scene_id: str,
        node: StoryNode,
    ) -> None:
        """Add a story node to the persistent narrative and NPC vector databases."""
        # Index general narrative
        await self.memory.add_async(scene_id, node.narrative)

        # Index NPC-specific details if present
        if getattr(node, "npcs_present", None):
            for npc in node.npcs_present:
                await self.npc_memory.add_async(npc, scene_id, node.narrative)

    async def reset(self) -> None:
        """Clear and close all current session memories to prevent leaks."""
        self.reset_sync()

    def reset_sync(self) -> None:
        """Clear and recreate all current session memories synchronously."""
        if hasattr(self.memory, "close"):
            self.memory.close()
        if hasattr(self.npc_memory, "close"):
            self.npc_memory.close()
        self.memory = self._memory_factory()
        self.npc_memory = self._npc_memory_factory()

    async def rebuild_async(self, history_scenes: list[dict[str, Any]]) -> None:
        """Rebuild memory from a list of past scenes."""
        await self.reset()
        for scene in history_scenes:
            scene_id = scene["id"]
            narrative = scene["narrative"]
            await self.memory.add_async(scene_id, narrative)
            if scene.get("npcs_present"):
                for npc in scene["npcs_present"]:
                    await self.npc_memory.add_async(npc, scene_id, narrative)
