import asyncio
import logging
import uuid
from typing import Any

from cyoa.core.events import Events, bus
from cyoa.core.models import Choice, StoryNode
from cyoa.db.graph_db import CYOAGraphDB
from cyoa.db.rag_memory import NarrativeMemory, NPCMemory
from cyoa.llm.broker import ModelBroker, SpeculationCache, StoryContext

logger = logging.getLogger(__name__)


class StoryEngine:
    """The central state machine and coordinator for the narrative flow.

    Decouples the TUI (UI) from the LLM (Models), Database (Persistence), and Memory (RAG)
    by orchestrating the story lifecycle through events.
    """

    def __init__(
        self,
        broker: ModelBroker,
        starting_prompt: str,
        db: CYOAGraphDB | None = None,
        memory: NarrativeMemory | None = None,
        npc_memory: NPCMemory | None = None,
    ) -> None:
        self.broker = broker
        self.starting_prompt = starting_prompt
        self.db = db
        self.memory = memory or NarrativeMemory()
        self.npc_memory = npc_memory or NPCMemory()
        self.speculation_cache = SpeculationCache()

        # Application state managed by the engine
        self.current_node: StoryNode | None = None
        self.inventory: list[str] = []
        self.player_stats: dict[str, int] = {"health": 100, "gold": 0, "reputation": 0}
        self.turn_count: int = 1
        self.story_context: StoryContext | None = None
        self.story_title: str | None = None
        self.current_scene_id: str | None = None
        self.last_choice_text: str | None = None

        # Snapshot for one-level undo
        self._undo_snapshot: dict[str, Any] | None = None

    async def initialize(self) -> None:
        """Start a brand-new adventure."""
        logger.info("Initializing Story Engine...")

        self.story_context = StoryContext(
            starting_prompt=self.starting_prompt,
            token_budget=self.broker.token_budget,
            token_counter=self.broker.provider.count_tokens,
        )

        self.turn_count = 1
        self.inventory = []
        self.player_stats = {"health": 100, "gold": 0, "reputation": 0}
        self.current_node = None
        self.story_title = None
        self.current_scene_id = None
        self.last_choice_text = None
        self._undo_snapshot = None

        # Reset memories for a new session
        # Note: If these were persistent (e.g. ChromaDB on disk), we'd need to handle that.
        # NarrativeMemory() creates a new collection by default in current impl.
        # But we'll trust the caller if they want to reuse them.

        bus.emit(Events.ENGINE_STARTED)
        await self._generate_next()

    async def restart(self) -> None:
        """Restart the engine with the same configuration."""
        # Clear engine-level caches
        self.speculation_cache.clear_all()
        await self.initialize()
        bus.emit(Events.ENGINE_RESTARTED)

    async def make_choice(self, choice_text: str) -> None:
        """Process a player's choice and advance the story."""
        if not self.story_context or not self.current_node:
            logger.warning("Choice made before engine was ready.")
            return

        # Snapshot for undo BEFORE making changes
        self._create_undo_snapshot()

        bus.emit(Events.CHOICE_MADE, choice_text=choice_text)

        # Update the LLM context (history and state)
        self.story_context.add_turn(
            self.current_node.narrative,
            choice_text,
            self.inventory,
            self.player_stats,
        )

        self.last_choice_text = choice_text
        self.turn_count += 1
        await self._generate_next(choice_text=choice_text)

    async def _generate_next(self, choice_text: str | None = None) -> None:
        """Orchestrate the generation of the next story node, including RAG and DB saving."""
        if not self.story_context:
            return

        bus.emit(Events.NODE_GENERATING)

        # 1. RAG: Retrieve relevant memories
        last_narrative = self.current_node.narrative if self.current_node else None
        if last_narrative:
            memories = await self.memory.query_async(last_narrative, n=3)

            # NPC Memory
            if self.current_node and getattr(self.current_node, "npcs_present", None):
                for npc in self.current_node.npcs_present:
                    npc_mems = await self.npc_memory.query_async(npc, last_narrative, n=2)
                    for mem in npc_mems:
                        if mem not in memories:
                            memories.append(mem)

            self.story_context.inject_memory(memories)

        # 2. Summarization check
        if self.story_context.needs_summarization():
            bus.emit(Events.STATUS_MESSAGE, message="📜 Archiving old chapters...")
            await self.broker.update_story_summaries_async(self.story_context)

        # 3. Speculation Cache check
        cached_node = None
        if choice_text and self.current_scene_id:
            cached_node = self.speculation_cache.get_node(self.current_scene_id, choice_text)

        def on_token(token: str) -> None:
            bus.emit(Events.TOKEN_STREAMED, token=token)

        try:
            if cached_node:
                # Simulated minimal stream to maintain UI feel
                bus.emit(Events.TOKEN_STREAMED, token="*(Recalling future memories...)* ")
                await asyncio.sleep(0.1)
                node = cached_node
            else:
                node = await self.broker.generate_next_node_async(
                    self.story_context, on_token_chunk=on_token
                )

            # 4. Save State (KV cache) if available
            state = await self.broker.save_state_async()
            if state and self.current_scene_id:
                self.speculation_cache.set_state(self.current_scene_id, state)

            # 5. Apply updates
            self._apply_node_updates(node)
            self.current_node = node

            # 6. First node check (Title)
            if self.turn_count == 1:
                # If first node, generate story title if not present
                generated_title = node.title if node.title else "Untitled Adventure"
                if self.db:
                    self.story_title = await asyncio.to_thread(
                        self.db.create_story_node_and_get_title, generated_title
                    )
                else:
                    self.story_title = generated_title
                bus.emit(Events.STORY_TITLE_GENERATED, title=self.story_title)

            # 7. Persistence and Indexing
            new_id = self.current_scene_id or str(uuid.uuid4())
            # Background indexing
            await self.memory.add_async(new_id, node.narrative)
            if getattr(node, "npcs_present", None):
                for npc in node.npcs_present:
                    await self.npc_memory.add_async(npc, new_id, node.narrative)

            # Database save
            if self.db and self.story_title:
                choices_text = [choice.text for choice in node.choices]
                self.current_scene_id = await self.db.save_scene_async(
                    narrative=node.narrative,
                    available_choices=choices_text,
                    story_title=self.story_title,
                    source_scene_id=self.current_scene_id,
                    choice_text=choice_text,
                )
            else:
                self.current_scene_id = new_id

            bus.emit(Events.NODE_COMPLETED, node=node)

            if node.is_ending:
                bus.emit(Events.ENDING_REACHED, node=node)

        except Exception as e:
            logger.error(f"Story Engine error: {e}", exc_info=True)
            bus.emit(Events.ERROR_OCCURRED, error=str(e))

    def _apply_node_updates(self, node: StoryNode) -> None:
        """Update local state from node feedback."""
        # Stats
        stats_changed = False
        updates = getattr(node, "stat_updates", {})
        for stat, change in updates.items():
            if change != 0:
                self.player_stats[stat] = self.player_stats.get(stat, 0) + change
                stats_changed = True

        if stats_changed:
            bus.emit(Events.STATS_UPDATED, stats=dict(self.player_stats))

        # Inventory
        inv_changed = False
        for item in getattr(node, "items_gained", []):
            if item not in self.inventory:
                self.inventory.append(item)
                inv_changed = True
        for item in getattr(node, "items_lost", []):
            if item in self.inventory:
                self.inventory.remove(item)
                inv_changed = True

        if inv_changed:
            bus.emit(Events.INVENTORY_UPDATED, inventory=list(self.inventory))

    def _create_undo_snapshot(self) -> None:
        if not self.story_context:
            return

        self._undo_snapshot = {
            "turn_count": self.turn_count,
            "current_node": self.current_node,
            "inventory": list(self.inventory),
            "player_stats": dict(self.player_stats),
            "story_context_history": [msg.copy() for msg in self.story_context.history],
            "story_title": self.story_title,
            "current_scene_id": self.current_scene_id,
            "last_choice_text": self.last_choice_text,
        }

    def undo(self) -> bool:
        """Revert to the previous turn's state."""
        if not self._undo_snapshot or not self.story_context:
            return False

        snap = self._undo_snapshot
        self.turn_count = snap["turn_count"]
        self.current_node = snap["current_node"]
        self.inventory = list(snap["inventory"])
        self.player_stats = dict(snap["player_stats"])
        self.story_context.history = snap["story_context_history"]
        self.story_title = snap["story_title"]
        self.current_scene_id = snap["current_scene_id"]
        self.last_choice_text = snap["last_choice_text"]

        self._undo_snapshot = None

        bus.emit(Events.STATS_UPDATED, stats=dict(self.player_stats))
        bus.emit(Events.INVENTORY_UPDATED, inventory=list(self.inventory))
        # Trigger UI refresh for node
        if self.current_node:
            bus.emit(Events.NODE_COMPLETED, node=self.current_node)

        return True

    def get_save_data(self) -> dict[str, Any]:
        """Produce a dictionary of the current state for saving."""
        if not self.story_context:
            return {}

        return {
            "version": 1,
            "story_title": self.story_title,
            "turn_count": self.turn_count,
            "inventory": self.inventory,
            "player_stats": self.player_stats,
            "starting_prompt": self.starting_prompt,
            "current_node": self.current_node.model_dump() if self.current_node else None,
            "context_history": self.story_context.history,
            "current_scene_id": self.current_scene_id,
            "last_choice_text": self.last_choice_text,
        }

    def load_save_data(self, data: dict[str, Any]) -> None:
        """Hydrate engine state from a save data dictionary."""
        self.story_title = data.get("story_title")
        self.turn_count = data.get("turn_count", 1)
        self.inventory = data.get("inventory", [])
        self.player_stats = data.get("player_stats", {"health": 100, "gold": 0, "reputation": 0})
        self.current_scene_id = data.get("current_scene_id")
        self.last_choice_text = data.get("last_choice_text")

        node_data = data.get("current_node")
        if node_data:
            self.current_node = StoryNode(**node_data)
        else:
            self.current_node = None

        self.story_context = StoryContext(
            starting_prompt=data.get("starting_prompt", self.starting_prompt),
            token_budget=self.broker.token_budget,
            token_counter=self.broker.provider.count_tokens,
        )
        self.story_context.history = data.get("context_history", [])

        bus.emit(Events.STATS_UPDATED, stats=dict(self.player_stats))
        bus.emit(Events.INVENTORY_UPDATED, inventory=list(self.inventory))
        if self.current_node:
            bus.emit(Events.NODE_COMPLETED, node=self.current_node)
        
        bus.emit(Events.STORY_TITLE_GENERATED, title=self.story_title)
