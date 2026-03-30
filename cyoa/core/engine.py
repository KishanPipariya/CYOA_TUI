import asyncio
import logging
import uuid
from typing import Any

from cyoa.core.events import Events, bus
from cyoa.core.models import StoryNode
from cyoa.core.observability import EngineObservedSession
from cyoa.core.rag import RAGManager
from cyoa.core.state import GameState
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

        # Extracted components
        self.rag = RAGManager(memory=memory, npc_memory=npc_memory)
        self.state = GameState()
        self.speculation_cache = SpeculationCache()

        # Story context (for LLM interactions)
        self.story_context: StoryContext | None = None

    @property
    def turn_count(self) -> int:
        return self.state.turn_count

    @turn_count.setter
    def turn_count(self, value: int) -> None:
        self.state.turn_count = value

    @property
    def inventory(self) -> list[str]:
        return self.state.inventory

    @inventory.setter
    def inventory(self, value: list[str]) -> None:
        self.state.inventory = value

    @property
    def player_stats(self) -> dict[str, int]:
        return self.state.player_stats

    @player_stats.setter
    def player_stats(self, value: dict[str, int]) -> None:
        self.state.player_stats = value

    @property
    def current_node(self) -> StoryNode | None:
        return self.state.current_node

    @current_node.setter
    def current_node(self, value: StoryNode | None) -> None:
        self.state.current_node = value

    @property
    def story_title(self) -> str | None:
        return self.state.story_title

    @story_title.setter
    def story_title(self, value: str | None) -> None:
        self.state.story_title = value

    @property
    def current_scene_id(self) -> str | None:
        return self.state.current_scene_id

    @current_scene_id.setter
    def current_scene_id(self, value: str | None) -> None:
        self.state.current_scene_id = value

    @property
    def last_choice_text(self) -> str | None:
        return self.state.last_choice_text

    @last_choice_text.setter
    def last_choice_text(self, value: str | None) -> None:
        self.state.last_choice_text = value

    @property
    def memory(self) -> NarrativeMemory:
        return self.rag.memory

    @memory.setter
    def memory(self, value: NarrativeMemory) -> None:
        self.rag.memory = value

    @property
    def npc_memory(self) -> NPCMemory:
        return self.rag.npc_memory

    @npc_memory.setter
    def npc_memory(self, value: NPCMemory) -> None:
        self.rag.npc_memory = value

    async def initialize(self) -> None:
        """Start a brand-new adventure."""
        with EngineObservedSession("initialize"):
            self.story_context = StoryContext(
                starting_prompt=self.starting_prompt,
                token_budget=self.broker.token_budget,
                token_counter=self.broker.provider.count_tokens,
            )

            # Reset extracted state
            self.state.reset()

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
        if not self.story_context or not self.state.current_node:
            logger.warning("Choice made before engine was ready.")
            return

        with EngineObservedSession("make_choice") as session:
            if session.span:
                session.span.set_attribute("choice.text", choice_text)

            # Snapshot for undo BEFORE making changes
            self.state.create_undo_snapshot()
            # Capture history snapshot separately because it belongs to story_context
            self.state._undo_snapshot["story_context_history"] = [
                msg.copy() for msg in self.story_context.history
            ]

            bus.emit(Events.CHOICE_MADE, choice_text=choice_text)

            # Update the LLM context (history and state)
            self.story_context.add_turn(
                self.state.current_node.narrative,
                choice_text,
                self.state.inventory,
                self.state.player_stats,
            )

            self.state.last_choice_text = choice_text
            self.state.turn_count += 1
            await self._generate_next(choice_text=choice_text)

    async def _generate_next(self, choice_text: str | None = None) -> None:
        """Orchestrate the generation of the next story node, including RAG and DB saving."""
        if not self.story_context:
            return

        bus.emit(Events.NODE_GENERATING)

        # 1. RAG: Retrieve relevant memories using the manager
        await self.rag.retrieve_memories(self.state.current_node, self.story_context)

        # 2. Summarization check
        if self.story_context.needs_summarization():
            bus.emit(Events.STATUS_MESSAGE, message="📜 Archiving old chapters...")
            await self.broker.update_story_summaries_async(self.story_context)

        # 3. Speculation Cache check
        cached_node = None
        if choice_text and self.state.current_scene_id:
            cached_node = self.speculation_cache.get_node(self.state.current_scene_id, choice_text)

        def on_token(token: str) -> None:
            bus.emit(Events.TOKEN_STREAMED, token=token)

        try:
            with EngineObservedSession("process_turn") as session:
                if cached_node:
                    # Simulated minimal stream to maintain UI feel
                    bus.emit(Events.TOKEN_STREAMED, token="*(Recalling future memories...)* ")
                    await asyncio.sleep(0.1)
                    node = cached_node
                    if session.span:
                        session.span.set_attribute("engine.cache_hit", True)
                else:
                    node = await self.broker.generate_next_node_async(
                        self.story_context, on_token_chunk=on_token
                    )
                    if session.span:
                        session.span.set_attribute("engine.cache_hit", False)

                # 4. Save State (KV cache) if available
                state = await self.broker.save_state_async()
                if state and self.state.current_scene_id:
                    self.speculation_cache.set_state(self.state.current_scene_id, state)

                # 5. Apply updates via GameState
                self.state.apply_node_updates(node)

                # 6. First node check (Title)
                if self.state.turn_count == 1:
                    generated_title = node.title if node.title else "Untitled Adventure"
                    if self.db:
                        self.state.story_title = await asyncio.to_thread(
                            self.db.create_story_node_and_get_title, generated_title
                        )
                    else:
                        self.state.story_title = generated_title
                    bus.emit(Events.STORY_TITLE_GENERATED, title=self.state.story_title)

                # 7. Persistence and Indexing using RAGManager
                new_id = self.state.current_scene_id or str(uuid.uuid4())
                await self.rag.index_node(new_id, node)

                # 8. Database save
                if self.db and self.state.story_title:
                    choices_text = [choice.text for choice in node.choices]
                    self.state.current_scene_id = await self.db.save_scene_async(
                        narrative=node.narrative,
                        available_choices=choices_text,
                        story_title=self.state.story_title,
                        source_scene_id=self.state.current_scene_id,
                        choice_text=choice_text,
                    )
                else:
                    self.state.current_scene_id = new_id

                bus.emit(Events.NODE_COMPLETED, node=node)

                if node.is_ending:
                    bus.emit(Events.ENDING_REACHED, node=node)

        except Exception as e:
            logger.error(f"Story Engine error: {e}", exc_info=True)
            bus.emit(Events.ERROR_OCCURRED, error=str(e))

    def undo(self) -> bool:
        """Revert to the previous turn's state."""
        if not self.state._undo_snapshot or not self.story_context:
            return False

        with EngineObservedSession("undo"):
            # Restore LLM context history from snapshot before delegating to GameState
            self.story_context.history = self.state._undo_snapshot["story_context_history"]
            return self.state.undo()

    def get_save_data(self) -> dict[str, Any]:
        """Produce a dictionary of the current state for saving."""
        if not self.story_context:
            return {}

        data = {
            "version": 1,
            "starting_prompt": self.starting_prompt,
            "context_history": self.story_context.history,
        }
        data.update(self.state.get_save_data())
        return data

    def load_save_data(self, data: dict[str, Any]) -> None:
        """Hydrate engine state from a save data dictionary."""
        # Hydrate state manager
        self.state.load_save_data(data)

        # Hydrate engine-level LLM context
        self.story_context = StoryContext(
            starting_prompt=data.get("starting_prompt", self.starting_prompt),
            token_budget=self.broker.token_budget,
            token_counter=self.broker.provider.count_tokens,
        )
        self.story_context.history = data.get("context_history", [])
