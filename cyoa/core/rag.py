import logging
from typing import Any

from cyoa.core.models import StoryNode
from cyoa.db.rag_memory import NarrativeMemory, NPCMemory
from cyoa.llm.broker import StoryContext

logger = logging.getLogger(__name__)


class RAGManager:
    """Handles the Retrieval-Augmented Generation (RAG) tasks for the StoryEngine.

    Separates narrative and NPC memory retrieval and indexing from the core engine flow.
    """

    def __init__(
        self,
        memory: NarrativeMemory | None = None,
        npc_memory: NPCMemory | None = None,
    ) -> None:
        self.memory = memory or NarrativeMemory()
        self.npc_memory = npc_memory or NPCMemory()

    async def retrieve_memories(
        self,
        current_node: StoryNode | None,
        story_context: StoryContext,
    ) -> list[str]:
        """Query relevant memories based on the current storyline and inject them into context."""
        last_narrative = current_node.narrative if current_node else None
        if not last_narrative:
            return []

        # 1. Retrieve narrative-level memories
        memories = await self.memory.query_async(last_narrative, n=3)

        # 2. Retrieve character-specific (NPC) memories
        if current_node and getattr(current_node, "npcs_present", None):
            for npc in current_node.npcs_present:
                npc_mems = await self.npc_memory.query_async(npc, last_narrative, n=2)
                for mem in npc_mems:
                    if mem not in memories:
                        memories.append(mem)

        # 3. Inject into the LLM context
        story_context.inject_memory(memories)
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
        if hasattr(self.memory, "close"):
            self.memory.close()
        if hasattr(self.npc_memory, "close"):
            self.npc_memory.close()
        self.memory = NarrativeMemory()
        self.npc_memory = NPCMemory()

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
