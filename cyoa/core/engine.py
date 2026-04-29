import asyncio
import logging
import random
import uuid
from typing import Any

from cyoa.core.events import Events, bus
from cyoa.core.models import (
    Choice,
    Companion,
    LoreEntry,
    Objective,
    ResolvedChoiceCheck,
    StoryNode,
)
from cyoa.core.observability import (
    EngineObservedSession,
    record_provider_cache_state_restore,
)
from cyoa.core.ports import NarrativeMemoryStore, NPCMemoryStore, StoryRepository
from cyoa.core.rag import RAGManager
from cyoa.core.runtime import EnginePhase, EngineTransition
from cyoa.core.state import GameState
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
        db: StoryRepository | None = None,
        memory: NarrativeMemoryStore | None = None,
        npc_memory: NPCMemoryStore | None = None,
        initial_world_state: dict[str, Any] | None = None,
        initial_prompt_config: dict[str, Any] | None = None,
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
        self.initial_world_state = initial_world_state or {}
        self.initial_prompt_config = initial_prompt_config or {}
        self.phase = EnginePhase.IDLE

        # Background summarization task — kept alive to prevent GC and allow
        # inspection. A new task replaces this reference each time summarization
        # is triggered; completed tasks are released automatically.
        self._pending_summarization_task: asyncio.Task[None] | None = None

    async def _cancel_pending_summarization_task(self) -> None:
        """Cancel and drain any in-flight summarization task."""
        task = self._pending_summarization_task
        if task is None:
            return

        self._pending_summarization_task = None
        if not task.done():
            task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.debug("Pending summarization task ended during lifecycle reset: %s", exc)

    async def _prepare_for_restart(self) -> None:
        """Reset transient runtime state before starting a fresh adventure."""
        self.speculation_cache.clear_all()
        await self._cancel_pending_summarization_task()
        await self.rag.reset()

    async def _prepare_for_history_restore(self) -> None:
        """Cancel stale background work before restoring saved or branched state."""
        await self._cancel_pending_summarization_task()
        await self.rag.reset()

    def _prepare_for_load(self) -> None:
        """Synchronously clear stale runtime state before hydrating a save."""
        task = self._pending_summarization_task
        self._pending_summarization_task = None
        if task is not None and not task.done():
            task.cancel()
        self.speculation_cache.clear_all()
        self.rag.reset_sync()

    def _emit_runtime_event(self, event_name: str, **kwargs: Any) -> None:
        """Emit an engine runtime event without letting subscriber failures abort the turn."""
        bus.emit_runtime(event_name, **kwargs)

    def _transition_phase(self, next_phase: EnginePhase, reason: str, **metadata: Any) -> None:
        """Track engine lifecycle explicitly and notify subscribers of phase changes."""
        previous_phase = self.phase
        if previous_phase == next_phase:
            return
        self.phase = next_phase
        transition = EngineTransition(
            from_phase=previous_phase,
            to_phase=next_phase,
            reason=reason,
            metadata=metadata or None,
        )
        self._emit_runtime_event(Events.ENGINE_PHASE_CHANGED, transition=transition)

    async def initialize(self) -> None:
        """Start a brand-new adventure."""
        with EngineObservedSession("initialize"):
            self._transition_phase(EnginePhase.INITIALIZING, "initialize")
            self.story_context = StoryContext(
                starting_prompt=self.starting_prompt,
                token_budget=self.broker.token_budget,
                token_counter=self.broker.provider.count_tokens,
            )

            # Reset extracted state
            self.state.reset()
            self._apply_initial_state()
            self._emit_runtime_event(Events.STATS_UPDATED, stats=dict(self.state.player_stats))
            self._emit_runtime_event(Events.INVENTORY_UPDATED, inventory=list(self.state.inventory))
            self._emit_runtime_event(Events.WORLD_STATE_UPDATED, state=self.state.get_world_state())

            self._emit_runtime_event(Events.ENGINE_STARTED)
            await self._generate_next()

    async def restart(self) -> None:
        """Restart the engine with the same configuration."""
        self._transition_phase(EnginePhase.INITIALIZING, "restart")
        await self._prepare_for_restart()
        await self.initialize()
        self._emit_runtime_event(Events.ENGINE_RESTARTED)

    async def make_choice(self, choice_text: str) -> None:
        """Process a player's choice and advance the story."""
        if not self.story_context or not self.state.current_node:
            logger.warning("Choice made before engine was ready.")
            return

        with EngineObservedSession("make_choice") as session:
            if session.span:
                session.span.set_attribute("choice.length", len(choice_text))

            # Snapshot for undo BEFORE making changes, including history because it belongs to story_context
            self.state.create_undo_snapshot(
                {"story_context_history": [msg.copy() for msg in self.story_context.history]}
            )

            self._emit_runtime_event(Events.CHOICE_MADE, choice_text=choice_text)

            choice_submission = choice_text
            choice_definition = self._find_choice_definition(choice_text)
            resolved_check = self._resolve_choice_check(choice_definition)
            self.state.last_resolved_choice_check = (
                resolved_check.model_copy() if resolved_check is not None else None
            )
            if resolved_check is not None:
                self._emit_choice_check_feedback(resolved_check)
                choice_submission = self._format_choice_submission(choice_text, resolved_check)

            # Update the LLM context (history and state)
            self.story_context.add_turn(
                self.state.current_node.narrative,
                choice_submission,
                self.state.inventory,
                self.state.player_stats,
            )

            self.state.last_choice_text = choice_text
            self.state.last_choice_submission = choice_submission
            self.state.turn_count += 1
            if choice_submission == choice_text:
                await self._generate_next(choice_text=choice_text)
            else:
                await self._generate_next(
                    choice_text=choice_submission,
                    persisted_choice_text=choice_text,
                )

    async def retry(self) -> None:
        """Re-run generation for the current context without advancing the turn."""
        with EngineObservedSession("retry"):
            choice_submission = self.state.last_choice_submission or self.state.last_choice_text
            if choice_submission is None:
                await self._generate_next()
            elif self.state.last_choice_submission and self.state.last_choice_text:
                await self._generate_next(
                    choice_text=choice_submission,
                    persisted_choice_text=self.state.last_choice_text,
                )
            else:
                await self._generate_next(choice_text=choice_submission)

    async def _prepare_generation_context(self) -> None:
        """Refresh retrieved memories and trigger non-blocking summarization when needed."""
        if not self.story_context:
            return

        await self.rag.retrieve_memories(self.state.current_node, self.story_context)

        if self.story_context.needs_summarization():
            self._emit_runtime_event(Events.SUMMARIZATION_STARTED)
            self._pending_summarization_task = asyncio.create_task(
                self._run_summarization_in_background(self.story_context)
            )

    def _get_cached_node(self, choice_text: str | None) -> StoryNode | None:
        """Look up a speculative node for the current scene and choice."""
        if not choice_text or not self.state.current_scene_id:
            return None
        return self.speculation_cache.get_node(self.state.current_scene_id, choice_text)

    async def _resolve_next_node(
        self,
        choice_text: str | None,
        on_token: Any,
        session: EngineObservedSession,
    ) -> StoryNode:
        """Use speculative cache when available, otherwise generate a fresh node."""
        cached_node = self._get_cached_node(choice_text)
        if cached_node:
            self._emit_runtime_event(
                Events.STATUS_MESSAGE, message="✨ Recalling future memories..."
            )
            await asyncio.sleep(0.1)
            if session.span:
                session.span.set_attribute("engine.cache_hit", True)
            record_provider_cache_state_restore(hit=True)
            return cached_node

        story_context = self.story_context
        if story_context is None:
            raise RuntimeError("Story context is not initialized.")

        node = await self.broker.generate_next_node_async(story_context, on_token_chunk=on_token)
        if session.span:
            session.span.set_attribute("engine.cache_hit", False)
        record_provider_cache_state_restore(hit=False)
        return node

    async def _set_story_title(self, node: StoryNode) -> None:
        """Create or derive the story title for the first generated node."""
        generated_title = node.title if node.title else "Untitled Adventure"
        if self.db:
            self.state.story_title = await asyncio.to_thread(
                self.db.create_story_node_and_get_title, generated_title
            )
        else:
            self.state.story_title = generated_title
        self._emit_runtime_event(Events.STORY_TITLE_GENERATED, title=self.state.story_title)

    async def _persist_generated_node(
        self,
        node: StoryNode,
        choice_text: str | None,
    ) -> None:
        """Save provider state, update local state, and persist/index the node."""
        state = await self.broker.save_state_async()
        if state and self.state.current_scene_id:
            self.speculation_cache.set_state(self.state.current_scene_id, state)

        self.state.apply_node_updates(node)
        self._sync_story_context_state()

        if self.state.turn_count == 1:
            await self._set_story_title(node)

        previous_scene_id = self.state.current_scene_id
        new_id = previous_scene_id or str(uuid.uuid4())
        await self.rag.index_node(new_id, node)

        if self.db and self.state.story_title:
            self.state.current_scene_id = await self.db.save_scene_async(
                narrative=node.narrative,
                available_choices=[choice.text for choice in node.choices],
                story_title=self.state.story_title,
                source_scene_id=previous_scene_id,
                choice_text=choice_text,
                player_stats=self.state.player_stats,
                inventory=self.state.inventory,
                mood=node.mood,
                lore_entries=[entry.model_dump() for entry in self.state.lore_entries],
                world_time=self.state.world_time.model_dump(),
            )
            return

        self.state.current_scene_id = new_id

    def _emit_generation_events(self, node: StoryNode) -> None:
        """Emit post-generation events in the established UI order."""
        self._emit_runtime_event(Events.NODE_COMPLETED, node=node)
        if node.is_ending:
            self._emit_runtime_event(Events.ENDING_REACHED, node=node)

    async def _generate_next(
        self,
        choice_text: str | None = None,
        *,
        persisted_choice_text: str | None = None,
    ) -> None:
        """Orchestrate the generation of the next story node, including RAG and DB saving."""
        if not self.story_context:
            return

        self._transition_phase(EnginePhase.GENERATING, "generate_next", choice_text=choice_text)
        self._emit_runtime_event(Events.NODE_GENERATING)
        await self._prepare_generation_context()

        def on_token(token: str) -> None:
            self._emit_runtime_event(Events.TOKEN_STREAMED, token=token)

        try:
            with EngineObservedSession("process_turn") as session:
                node = await self._resolve_next_node(choice_text, on_token, session)
                await self._persist_generated_node(
                    node,
                    persisted_choice_text if persisted_choice_text is not None else choice_text,
                )
                self._emit_generation_events(node)
                self._transition_phase(
                    EnginePhase.READY, "generation_completed", scene_id=self.state.current_scene_id
                )

        except Exception as e:
            logger.error(f"Story Engine error: {e}", exc_info=True)
            self._transition_phase(EnginePhase.ERROR, "generation_failed", error=str(e))
            self._emit_runtime_event(Events.ERROR_OCCURRED, error=str(e))

    async def _run_summarization_in_background(self, context: StoryContext) -> None:
        """Run hierarchical summarization as a fire-and-forget background task.

        This is intentionally decoupled from the main generation path so it
        never contributes to Time-to-First-Token latency. The updated summary
        will be available in `context` by the time the *next* turn is generated.
        """
        task = asyncio.current_task()
        try:
            self._emit_runtime_event(Events.STATUS_MESSAGE, message="📜 Archiving old chapters...")
            await self.broker.update_story_summaries_async(context)
            logger.debug("Background summarization completed successfully.")
        except asyncio.CancelledError:
            logger.debug("Background summarization cancelled.")
            raise
        except Exception as exc:
            # Failure is non-fatal — the next turn will simply run without a
            # fresh summary, which is preferable to blocking or crashing.
            logger.warning("Background summarization failed (non-fatal): %s", exc)
        finally:
            if self._pending_summarization_task is task:
                self._pending_summarization_task = None

    def shutdown(self) -> None:
        """Cancel engine-owned background work and release external resources."""
        self._transition_phase(EnginePhase.SHUTDOWN, "shutdown")
        task = self._pending_summarization_task
        self._pending_summarization_task = None
        if task is not None:
            task.cancel()

        self.rag.memory.close()
        self.rag.npc_memory.close()

        if self.db:
            self.db.close()

    def undo(self) -> bool:
        """Revert to the previous turn's state."""
        if not self.story_context:
            return False

        with EngineObservedSession("undo"):
            if not self.state.undo():
                return False
            snapshot = self.state._last_restored_snapshot
            if snapshot is not None:
                self.story_context.history = snapshot.story_context.to_payload()
            self._sync_story_context_state()
            return True

    def redo(self) -> bool:
        """Re-apply the most recently undone turn."""
        if not self.story_context:
            return False

        with EngineObservedSession("redo"):
            if not self.state.redo():
                return False
            snapshot = self.state._last_restored_snapshot
            if snapshot is not None:
                self.story_context.history = snapshot.story_context.to_payload()
            self._sync_story_context_state()
            return True

    def create_bookmark(self, name: str) -> bool:
        """Capture a named checkpoint for later restoration."""
        if not self.story_context:
            return False
        return self.state.create_bookmark(
            name,
            extra_data={
                "story_context_history": [msg.copy() for msg in self.story_context.history]
            },
        )

    def restore_bookmark(self, name: str) -> bool:
        """Restore a named checkpoint."""
        if not self.story_context:
            return False

        with EngineObservedSession("restore_bookmark"):
            if not self.state.restore_bookmark(name):
                return False
            snapshot = self.state._last_restored_snapshot
            if snapshot is not None:
                self.story_context.history = snapshot.story_context.to_payload()
            self._sync_story_context_state()
            return True

    def list_bookmarks(self) -> list[str]:
        """Return bookmark names available in the current run."""
        return self.state.list_bookmarks()

    def get_save_data(self) -> dict[str, Any]:
        """Produce a dictionary of the current state for saving."""
        if not self.story_context:
            return {}

        data = {
            "starting_prompt": self.starting_prompt,
            "context_history": [message.copy() for message in self.story_context.history],
            "prompt_config": {
                "goals": list(self.story_context.goals),
                "directives": list(self.story_context.directives),
            },
        }
        data.update(self.state.get_save_data())
        return data

    def load_save_data(self, data: dict[str, Any]) -> None:
        """Hydrate engine state from a save data dictionary."""
        self._transition_phase(EnginePhase.RESTORING, "load_save_data")
        self._prepare_for_load()

        # Hydrate state manager
        self.state.load_save_data(data)

        # Hydrate engine-level LLM context
        starting_prompt = data.get("starting_prompt")
        if not isinstance(starting_prompt, str) or not starting_prompt:
            starting_prompt = self.starting_prompt

        self.story_context = StoryContext(
            starting_prompt=starting_prompt,
            token_budget=self.broker.token_budget,
            token_counter=self.broker.provider.count_tokens,
        )
        context_history = data.get("context_history")
        self.story_context.history = context_history if isinstance(context_history, list) else []
        prompt_config = data.get("prompt_config")
        if isinstance(prompt_config, dict):
            goals = prompt_config.get("goals")
            directives = prompt_config.get("directives")
            if isinstance(goals, list):
                self.story_context.goals = [goal for goal in goals if isinstance(goal, str)]
            if isinstance(directives, list):
                self.story_context.directives = [
                    directive for directive in directives if isinstance(directive, str)
                ]
        self._sync_story_context_state()
        self._transition_phase(
            EnginePhase.READY, "load_completed", scene_id=self.state.current_scene_id
        )

    async def branch_to_scene(self, idx: int, history: dict[str, Any]) -> None:
        """Restore the engine state to a specific scene from the history."""
        self._transition_phase(EnginePhase.RESTORING, "branch_to_scene", target_index=idx)
        await self._prepare_for_history_restore()
        source_scene_id = self.state.current_scene_id

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
        self.state.last_choice_submission = self.state.last_choice_text
        self.state.last_resolved_choice_check = None
        self.state.turn_count = idx + 1
        self.state.inventory = list(target_scene.get("inventory", []))
        self.state.player_stats = dict(
            target_scene.get("player_stats", {"health": 100, "gold": 0, "reputation": 0})
        )
        lore_entries: list[LoreEntry] = []
        for raw in target_scene.get("lore_entries", []):
            if not isinstance(raw, dict):
                continue
            try:
                lore_entries.append(LoreEntry(**raw))
            except Exception:
                continue
        self.state.seed_world_state(
            lore_entries=lore_entries,
            world_time=target_scene.get("world_time"),
        )
        self.state.timeline_metadata.append(
            {
                "kind": "branch_restore",
                "source_scene_id": source_scene_id,
                "target_scene_id": self.state.current_scene_id,
                "restored_turn": idx + 1,
            }
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
        self._sync_story_context_state()

        # Emit events so UI can refresh stats/inventory/narrative
        self._emit_runtime_event(Events.STATS_UPDATED, stats=self.state.player_stats)
        self._emit_runtime_event(Events.INVENTORY_UPDATED, inventory=self.state.inventory)
        self._emit_runtime_event(Events.WORLD_STATE_UPDATED, state=self.state.get_world_state())
        self._emit_runtime_event(Events.NODE_COMPLETED, node=node)
        self._transition_phase(
            EnginePhase.READY, "branch_restore_completed", scene_id=self.state.current_scene_id
        )

    @staticmethod
    def _coerce_objective_seed(raw_values: object) -> list[Objective]:
        objectives: list[Objective] = []
        if not isinstance(raw_values, list):
            return objectives
        for raw in raw_values:
            if isinstance(raw, Objective):
                objectives.append(raw)
            elif isinstance(raw, dict):
                try:
                    objectives.append(Objective(**raw))
                except Exception:
                    continue
        return objectives

    @staticmethod
    def _coerce_lore_entry_seed(raw_values: object) -> list[LoreEntry]:
        lore_entries: list[LoreEntry] = []
        if not isinstance(raw_values, list):
            return lore_entries
        for raw in raw_values:
            if isinstance(raw, LoreEntry):
                lore_entries.append(raw)
            elif isinstance(raw, dict):
                try:
                    lore_entries.append(LoreEntry(**raw))
                except Exception:
                    continue
        return lore_entries

    @staticmethod
    def _coerce_companion_seed(raw_values: object) -> list[Companion]:
        companions: list[Companion] = []
        if not isinstance(raw_values, list):
            return companions
        for raw in raw_values:
            if isinstance(raw, Companion):
                companions.append(raw)
            elif isinstance(raw, dict):
                try:
                    companions.append(Companion(**raw))
                except Exception:
                    continue
        return companions

    def _apply_initial_state(self) -> None:
        self.state.seed_world_state(
            inventory=self.initial_world_state.get("inventory"),
            player_stats=self.initial_world_state.get("player_stats"),
            objectives=self._coerce_objective_seed(self.initial_world_state.get("objectives")),
            companions=self._coerce_companion_seed(self.initial_world_state.get("companions")),
            faction_reputation=self.initial_world_state.get("faction_reputation"),
            npc_affinity=self.initial_world_state.get("npc_affinity"),
            story_flags=set(self.initial_world_state.get("story_flags", [])),
            lore_entries=self._coerce_lore_entry_seed(self.initial_world_state.get("lore_entries")),
            world_time=self.initial_world_state.get("world_time"),
        )
        if self.story_context:
            goals = self.initial_prompt_config.get("goals")
            directives = self.initial_prompt_config.get("directives")
            persona = self.initial_prompt_config.get("persona")
            if isinstance(goals, list):
                self.story_context.goals = [goal for goal in goals if isinstance(goal, str)]
            if isinstance(directives, list):
                self.story_context.directives = [
                    directive for directive in directives if isinstance(directive, str)
                ]
            if isinstance(persona, str) and persona.strip():
                self.story_context.set_persona(persona)
        self._sync_story_context_state()

    def _sync_story_context_state(self) -> None:
        if not self.story_context:
            return
        self.story_context.sync_world_state(
            inventory=self.state.inventory,
            player_stats=self.state.player_stats,
            objectives=self.state.objectives,
            faction_reputation=self.state.faction_reputation,
            npc_affinity=self.state.npc_affinity,
            story_flags=self.state.story_flags,
            lore_entries=self.state.lore_entries,
            companions=self.state.companions,
            world_time=self.state.world_time,
        )

    def _find_choice_definition(self, choice_text: str) -> Choice | None:
        current_node = self.state.current_node
        if current_node is None:
            return None
        for choice in current_node.choices:
            if choice.text == choice_text:
                return choice
        return None

    def _resolve_choice_check(self, choice: Choice | None) -> ResolvedChoiceCheck | None:
        if choice is None or choice.check is None:
            return None

        stat_key = choice.check.stat
        stat_value = self.state.player_stats.get(stat_key, 0)
        roll = random.randint(1, 20)
        total = stat_value + roll
        return ResolvedChoiceCheck(
            stat=stat_key,
            stat_value=stat_value,
            difficulty=choice.check.difficulty,
            roll=roll,
            total=total,
            success=total >= choice.check.difficulty,
            stakes=choice.check.stakes,
        )

    def _emit_choice_check_feedback(self, resolved: ResolvedChoiceCheck) -> None:
        stat_label = resolved.stat_label()
        self._emit_runtime_event(
            Events.STATUS_MESSAGE,
            message=f"🎲 Testing {stat_label} against difficulty {resolved.difficulty}...",
        )
        outcome = "passed" if resolved.success else "failed"
        message = (
            f"{'✅' if resolved.success else '⚠️'} {stat_label} check {outcome}: "
            f"{resolved.roll} + {resolved.stat_value} = {resolved.total} vs {resolved.difficulty}"
        )
        if resolved.stakes and not resolved.success:
            message = f"{message}. Stakes triggered: {resolved.stakes}"
        elif resolved.stakes and resolved.success:
            message = f"{message}. Stakes avoided: {resolved.stakes}"
        self._emit_runtime_event(Events.STATUS_MESSAGE, message=message)

    @staticmethod
    def _format_choice_submission(choice_text: str, resolved: ResolvedChoiceCheck) -> str:
        outcome = "success" if resolved.success else "failure"
        lines = [
            choice_text,
            "",
            "[Resolved skill check]",
            f"Stat tested: {resolved.stat_label()}",
            f"Current stat value: {resolved.stat_value}",
            f"Roll: {resolved.roll}",
            f"Total: {resolved.total}",
            f"Difficulty: {resolved.difficulty}",
            f"Outcome: {outcome}",
        ]
        if resolved.stakes:
            lines.append(f"Stakes: {resolved.stakes}")
        return "\n".join(lines)
