import asyncio
import logging
import uuid
from typing import Any

from cyoa.core.events import Events, bus
from cyoa.core.models import Choice, StoryNode
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

        # Background summarization task — kept alive to prevent GC and allow
        # inspection. A new task replaces this reference each time summarization
        # is triggered; completed tasks are released automatically.
        self._pending_summarization_task: asyncio.Task[None] | None = None

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

            # Snapshot for undo BEFORE making changes, including history because it belongs to story_context
            self.state.create_undo_snapshot({
                "story_context_history": [msg.copy() for msg in self.story_context.history]
            })

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

    async def retry(self) -> None:
        """Re-run generation for the current context without advancing the turn."""
        with EngineObservedSession("retry"):
            await self._generate_next(choice_text=self.state.last_choice_text)

    async def _generate_next(self, choice_text: str | None = None) -> None:
        """Orchestrate the generation of the next story node, including RAG and DB saving."""
        if not self.story_context:
            return

        bus.emit(Events.NODE_GENERATING)

        # 1. RAG: Retrieve relevant memories using the manager
        await self.rag.retrieve_memories(self.state.current_node, self.story_context)

        # 2. Summarization check — fire-and-forget as a background task so it
        #    runs concurrently with (or just before) node generation instead of
        #    blocking Time-to-First-Token for the current turn.
        #    The updated summary will be injected into the context for the NEXT turn.
        if self.story_context.needs_summarization():
            bus.emit(Events.SUMMARIZATION_STARTED)
            self._pending_summarization_task = asyncio.create_task(
                self._run_summarization_in_background(self.story_context)
            )

        # 3. Speculation Cache check
        cached_node = None
        if choice_text and self.state.current_scene_id:
            cached_node = self.speculation_cache.get_node(self.state.current_scene_id, choice_text)

        def on_token(token: str) -> None:
            bus.emit(Events.TOKEN_STREAMED, token=token)

        try:
            with EngineObservedSession("process_turn") as session:
                if cached_node:
                    # Indicate cache hit via status message instead of polluting the narrative stream
                    bus.emit(Events.STATUS_MESSAGE, message="✨ Recalling future memories...")
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
                        player_stats=self.state.player_stats,
                        inventory=self.state.inventory,
                        mood=node.mood,
                    )
                else:
                    self.state.current_scene_id = new_id

                bus.emit(Events.NODE_COMPLETED, node=node)

                if node.is_ending:
                    bus.emit(Events.ENDING_REACHED, node=node)

        except Exception as e:
            logger.error(f"Story Engine error: {e}", exc_info=True)
            bus.emit(Events.ERROR_OCCURRED, error=str(e))

    async def _run_summarization_in_background(self, context: StoryContext) -> None:
        """Run hierarchical summarization as a fire-and-forget background task.

        This is intentionally decoupled from the main generation path so it
        never contributes to Time-to-First-Token latency. The updated summary
        will be available in `context` by the time the *next* turn is generated.
        """
        try:
            bus.emit(Events.STATUS_MESSAGE, message="📜 Archiving old chapters...")
            await self.broker.update_story_summaries_async(context)
            logger.debug("Background summarization completed successfully.")
        except Exception as exc:
            # Failure is non-fatal — the next turn will simply run without a
            # fresh summary, which is preferable to blocking or crashing.
            logger.warning("Background summarization failed (non-fatal): %s", exc)

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

    async def branch_to_scene(self, idx: int, history: dict[str, Any]) -> None:
        """Restore the engine state to a specific scene from the history."""
        # 1. Rebuild user-facing context history
        self.story_context = StoryContext(
            starting_prompt=self.starting_prompt,
            token_budget=self.broker.token_budget,
            token_counter=self.broker.provider.count_tokens,
        )
        for i in range(idx):
            self.story_context.add_turn(history["scenes"][i]["narrative"], history["choices"][i])

        # 2. Update state manager
        target_scene = history["scenes"][idx]
        self.state.current_scene_id = target_scene["id"]
        self.state.last_choice_text = history["choices"][idx - 1] if idx > 0 else None
        self.state.turn_count = idx + 1
        self.state.inventory = list(target_scene.get("inventory", []))
        self.state.player_stats = dict(
            target_scene.get("player_stats", {"health": 100, "gold": 0, "reputation": 0})
        )

        # 3. Rebuild memory
        await self.rag.rebuild_async(history["scenes"][: idx + 1])

        # 4. Restore provider state (KV cache) if available
        state = self.speculation_cache.get_state(self.state.current_scene_id)
        if state:
            await self.broker.load_state_async(state)

        # 5. Create the node for UI display
        available = target_scene.get("available_choices") or []
        choices = [Choice(text=c) for c in available]
        node = StoryNode(
            narrative=target_scene["narrative"],
            choices=choices,
            is_ending=len(choices) == 0,
        )
        self.state.current_node = node

        # Emit events so UI can refresh stats/inventory/narrative
        bus.emit(Events.STATS_UPDATED, stats=self.state.player_stats)
        bus.emit(Events.INVENTORY_UPDATED, inventory=self.state.inventory)
        bus.emit(Events.NODE_COMPLETED, node=node)
