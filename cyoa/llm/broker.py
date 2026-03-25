import json
import logging
import os
import pathlib
from collections.abc import Callable
from typing import Any

import jinja2
import jiter

from cyoa.core.constants import (
    CHARS_PER_TOKEN,
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_LLM_N_CTX,
    DEFAULT_LLM_REPAIR_ATTEMPTS,
    DEFAULT_LLM_SUMMARY_MAX_TOKENS,
    DEFAULT_LLM_SUMMARY_THRESHOLD,
    DEFAULT_LLM_TEMPERATURE,
)
from cyoa.core.models import Choice, ExtractionNode, NarratorNode, StoryNode
from cyoa.core.observability import EngineObservedSession, record_repair_attempt
from cyoa.llm.pipeline import (
    DirectiveComponent,
    GoalComponent,
    HistoryComponent,
    MemoryComponent,
    PersonaComponent,
    PlayerSheetComponent,
    PromptPipeline,
    SummarizationComponent,
)
from cyoa.llm.providers import LlamaCppProvider, LLMProvider, MockProvider, OllamaProvider

__all__ = ["StoryContext", "ModelBroker", "SpeculationCache"]

# Configurable via .env / environment — defaults used if not set
DEFAULT_TOKEN_BUDGET = int(os.getenv("LLM_TOKEN_BUDGET", "2048"))

# Rolling summarization fires when the number of stored turn *pairs* reaches
# this fraction of token_budget. At 0.8 we still have 20% headroom before the
# hard sliding-window truncation kicks in.
SUMMARIZATION_THRESHOLD = float(
    os.getenv("LLM_SUMMARY_THRESHOLD", str(DEFAULT_LLM_SUMMARY_THRESHOLD))
)


logger = logging.getLogger(__name__)


class StoryContext:
    def __init__(
        self,
        starting_prompt: str,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
        token_counter: Callable[[str], int] | None = None,
    ) -> None:
        self.token_budget = token_budget
        self.token_counter = token_counter or (lambda x: len(x) // CHARS_PER_TOKEN)
        self.starting_prompt = starting_prompt
        self.history: list[dict[str, str]] = [{"role": "user", "content": starting_prompt}]
        self.inventory: list[str] = []
        self.player_stats: dict[str, int] = {}
        self.memories: list[str] = []
        # Hierarchical Context Compression
        self.scene_summary: str | None = None
        self.chapter_summary: str | None = None
        self.arc_summary: str | None = None
        # User/System goals and directives (injected into prompt via pipeline)
        self.goals: list[str] = []
        self.directives: list[str] = []
        # Hierarchy tracking
        self._scene_turn_count: int = 0
        self._chapter_scene_count: int = 0

        template_dir = pathlib.Path(__file__).parent / "templates"
        self.jinja_env = jinja2.Environment(loader=jinja2.FileSystemLoader(template_dir))
        self.system_template = self.jinja_env.get_template("system_prompt.j2")

        # Initialize the modular prompt pipeline
        # We can still extract the base text from the template if we want,
        # but for now we'll define a default persona and use modular components.
        self.pipeline = PromptPipeline(
            [
                PersonaComponent(),
                GoalComponent(),
                DirectiveComponent(),
                PlayerSheetComponent(),
                MemoryComponent(),
                SummarizationComponent(),
                HistoryComponent(),
            ]
        )

    def set_persona(self, persona_text: str) -> None:
        """Override the default persona in the pipeline."""
        for i, comp in enumerate(self.pipeline.components):
            if isinstance(comp, PersonaComponent):
                self.pipeline.components[i] = PersonaComponent(persona_text)
                break

    # ------------------------------------------------------------------
    # Turn management
    # ------------------------------------------------------------------

    def add_turn(
        self,
        raw_narrative: str,
        user_choice: str,
        inventory: list[str] | None = None,
        player_stats: dict[str, int] | None = None,
    ) -> None:
        """Add an assistant turn (raw narrative) and user choice, trimming old turns."""
        self.history.append({"role": "assistant", "content": raw_narrative})
        self.history.append({"role": "user", "content": f"I choose: {user_choice}"})

        if inventory is not None:
            self.inventory = inventory
        if player_stats is not None:
            self.player_stats = player_stats

        # Sliding window: keep only initial prompt (0) and as many tail turns as fit in budget
        self._prune_history()

    def _prune_history(self) -> None:
        """Prune story history and memories to fit within the token budget.

        Prioritizes:
        1. System Prompt & Persona
        2. Rolling Summary
        3. Latest Turn pair
        4. Top 1 RAG Memory
        5. Oldest turns (dropped first)
        """
        # Phase 1: Prune oldest turns from history (always keep initial prompt and latest pair)
        while len(self.history) > 3:
            if self.count_total_tokens() <= self.token_budget:
                break
            # history[0] is opening prompt; pop(1) twice removes the oldest Turn pair (assistant + user)
            self.history.pop(1)
            self.history.pop(1)

        # Phase 2: Dynamic RAG Scaling (Prune memories if still over budget)
        while len(self.memories) > 1:
            if self.count_total_tokens() <= self.token_budget:
                break
            # Remove lowest priority memories (keep only the first one if necessary)
            self.memories.pop()

    def count_total_tokens(self) -> int:
        """Calculate the total token count of the current message stack."""
        messages = self.get_messages()
        total = 0
        for msg in messages:
            # We count roles too for a more accurate estimate
            total += self.token_counter(msg.get("role", ""))
            total += self.token_counter(msg.get("content", ""))
        return total

    def inject_memory(self, memories: list[str]) -> None:
        """Store memories to be injected dynamically into the system prompt."""
        self.memories = memories

    def set_hierarchical_summary(
        self,
        scene: str | None = None,
        chapter: str | None = None,
        arc: str | None = None,
    ) -> None:
        """Update the hierarchical summaries of the narrative."""
        if scene is not None:
            self.scene_summary = scene
        if chapter is not None:
            self.chapter_summary = chapter
        if arc is not None:
            self.arc_summary = arc
        # Still run prune to ensure we stay within budget if summaries increased
        self._prune_history()

    def set_rolling_summary(self, summary: str) -> None:
        """Backward compatibility for the old rolling summary. Sets the scene summary."""
        self.scene_summary = summary
        self._prune_history()

    # ------------------------------------------------------------------
    # Summarization trigger
    # ------------------------------------------------------------------

    def needs_summarization(self, threshold: float = SUMMARIZATION_THRESHOLD) -> bool:
        """Return True when token count reaches *threshold* fraction of token_budget."""
        return self.count_total_tokens() >= int(self.token_budget * threshold)

    def get_turns_for_summary(self) -> list[dict[str, str]]:
        """Return the older turn pairs that should be compressed.

        We keep the 3 most recent turn pairs verbatim and summarize everything else
        in the dynamic history tail (excluding the opening prompt).
        """
        tail = self.history[1:]  # exclude opening user prompt
        keep_count = 6  # 3 pairs
        if len(tail) <= keep_count:
            return []
        return tail[:-keep_count]

    # ------------------------------------------------------------------
    # Message assembly
    # ------------------------------------------------------------------

    def get_messages(self) -> list[dict[str, str]]:
        """Assemble the full message stack using the component pipeline."""
        return self.pipeline.process(self)

    def clone(self) -> "StoryContext":
        """Return a deep copy of the context data, but reuse the model/template refs."""
        new_ctx = StoryContext(
            starting_prompt=self.starting_prompt,
            token_budget=self.token_budget,
            token_counter=self.token_counter,
        )
        # Deep copy the mutable history and stats
        new_ctx.history = [msg.copy() for msg in self.history]
        new_ctx.inventory = list(self.inventory)
        new_ctx.player_stats = dict(self.player_stats)
        new_ctx.memories = list(self.memories)
        new_ctx.scene_summary = self.scene_summary
        new_ctx.chapter_summary = self.chapter_summary
        new_ctx.arc_summary = self.arc_summary
        new_ctx.goals = list(self.goals)
        new_ctx.directives = list(self.directives)
        new_ctx._scene_turn_count = self._scene_turn_count
        new_ctx._chapter_scene_count = self._chapter_scene_count
        # Reuse the same pipeline (shallow copy of list is fine if components are stateless or handled)
        new_ctx.pipeline = PromptPipeline(list(self.pipeline.components))
        return new_ctx


class SpeculationCache:
    """Stores pre-calculated story nodes to reduce perceived latency."""

    def __init__(self) -> None:
        self._nodes: dict[str, StoryNode] = {}
        # We also store the KV states if available
        self._states: dict[str, Any] = {}

    def get_node(self, scene_id: str, choice_text: str) -> StoryNode | None:
        return self._nodes.get(f"{scene_id}:{choice_text}")

    def set_node(self, scene_id: str, choice_text: str, node: StoryNode) -> None:
        self._nodes[f"{scene_id}:{choice_text}"] = node

    def get_state(self, scene_id: str) -> Any:
        return self._states.get(scene_id)

    def set_state(self, scene_id: str, state: Any) -> None:
        self._states[scene_id] = state

    def clear_nodes(self) -> None:
        self._nodes.clear()

    def clear_all(self) -> None:
        self._nodes.clear()
        self._states.clear()


class ModelBroker:
    def __init__(
        self,
        model_path: str | None = None,
        n_ctx: int | None = None,
        provider: LLMProvider | None = None,
    ) -> None:
        if provider:
            self.provider = provider
        else:
            self.provider = self._create_provider_from_env(model_path, n_ctx)

        # Token budget for StoryContext is half of the provider's context window
        # to leave plenty of room for generation and system overhead.
        default_budget = (n_ctx or 4096) // 2
        self.token_budget = int(os.getenv("LLM_TOKEN_BUDGET", str(default_budget)))

        self._narrator_schema = NarratorNode.model_json_schema()
        self._extraction_schema = ExtractionNode.model_json_schema()
        self._schema = StoryNode.model_json_schema()

        self._temperature = float(os.getenv("LLM_TEMPERATURE", str(DEFAULT_LLM_TEMPERATURE)))
        self._max_tokens = int(os.getenv("LLM_MAX_TOKENS", str(DEFAULT_LLM_MAX_TOKENS)))
        # Maximum tokens for the "Story So Far" summary paragraph.
        self._summary_max_tokens = int(
            os.getenv("LLM_SUMMARY_MAX_TOKENS", str(DEFAULT_LLM_SUMMARY_MAX_TOKENS))
        )
        # Number of recursive repair attempts if JSON structure or schema fails
        self._repair_attempts = int(
            os.getenv("LLM_REPAIR_ATTEMPTS", str(DEFAULT_LLM_REPAIR_ATTEMPTS))
        )

    def _create_provider_from_env(
        self, model_path: str | None = None, n_ctx: int | None = None
    ) -> LLMProvider:
        provider_type = os.getenv("LLM_PROVIDER", "llama_cpp").lower()
        if provider_type == "ollama":
            model = os.getenv("LLM_MODEL", model_path or "llama3")
            base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
            return OllamaProvider(model=model, base_url=base_url)
        else:
            m_path = model_path or os.getenv("LLM_MODEL_PATH")
            if not m_path:
                m_path = "models/mistral-7b-instruct-v0.2.Q4_K_M.gguf"
                logger.warning("No model path provided, using default: %s", m_path)

            n_ctx_val = n_ctx or int(os.getenv("LLM_N_CTX", str(DEFAULT_LLM_N_CTX)))
            if not os.path.exists(m_path):
                logger.warning(
                    f"Model file '{m_path}' not found. Falling back to MockProvider for development."
                )
                return MockProvider(model_name=m_path)
            return LlamaCppProvider(model_path=m_path, n_ctx=n_ctx_val)

    async def generate_summary_async(self, turns_to_compress: list[dict[str, str]]) -> str:
        """Deprecated legacy wrapper. Use update_story_summaries_async(context) instead."""
        # This will only be called if some third-party code uses it.
        # We can't easily update a context we don't have, so we use the legacy logic.
        return await self.generate_legacy_summary_async(turns_to_compress)

    async def update_story_summaries_async(self, context: StoryContext) -> None:
        """The core of Hierarchical Context Compression.

        Compresses pruned history into a Scene Summary (last 10 turns),
        Chapter Summary (last 5 scenes), and Arc Summary (global plot goals).
        """
        turns_to_compress = context.get_turns_for_summary()
        if not turns_to_compress:
            return

        pair_count = len(turns_to_compress) // 2

        # New buffers for the update
        new_scene = context.scene_summary
        new_chapter = context.chapter_summary
        new_arc = context.arc_summary

        # PHASE 1: Update Scene Summary
        if context._scene_turn_count + pair_count <= 10:
            # Still within the ~10 turn scene window
            new_scene = await self._generate_dense_summary(
                turns_to_compress, context.scene_summary, level="scene"
            )
            context._scene_turn_count += pair_count
        else:
            # Scene window full — promote existing Scene Summary to Chapter level
            logger.info("Hierarchical Summarization: Promoting Scene to Chapter.")

            # Incorporate old scene summary into chapter summary
            new_chapter = await self._generate_dense_summary(
                [], context.chapter_summary, previous_summary=context.scene_summary, level="chapter"
            )
            context._chapter_scene_count += 1

            # Start a brand new Scene Summary with the new turns
            new_scene = await self._generate_dense_summary(turns_to_compress, level="scene")
            context._scene_turn_count = pair_count

            # PHASE 2: Check for Chapter -> Arc promotion
            if context._chapter_scene_count >= 5:
                logger.info("Hierarchical Summarization: Promoting Chapter to Arc.")
                new_arc = await self._generate_dense_summary(
                    [], context.arc_summary, previous_summary=context.chapter_summary, level="arc"
                )
                # Reset chapter buffer
                new_chapter = ""
                context._chapter_scene_count = 0

        # Apply the updates to the context (also triggers pruning)
        with EngineObservedSession("update_summaries"):
            context.set_hierarchical_summary(scene=new_scene, chapter=new_chapter, arc=new_arc)

    async def _generate_dense_summary(
        self,
        turns: list[dict[str, str]],
        existing: str | None = None,
        previous_summary: str | None = None,
        level: str = "scene",
    ) -> str:
        """Helper to call the LLM for a specific hierarchy level."""

        level_map = {
            "scene": ("Scene Summary", "last 10 turns", "2-3 sentences of plot events"),
            "chapter": (
                "Chapter Summary",
                "last 5 scenes",
                "a dense overview of the local mission or region",
            ),
            "arc": (
                "Arc Summary",
                "global plot goals",
                "a high-level synopsis of the overarching journey",
            ),
        }
        title, context_desc, goal = level_map.get(level, ("Summary", "", ""))

        # Assemble the input text
        input_bits = []
        if existing:
            input_bits.append(f"Current {title}: {existing}")
        if previous_summary:
            input_bits.append(f"Newly Finished Lower-Level Context: {previous_summary}")

        if turns:
            compact_turns = []
            for msg in turns:
                role = "Story" if msg["role"] == "assistant" else "Player"
                compact_turns.append(f"[{role}]: {msg['content']}")
            input_bits.append("Recent Turn History:\n" + "\n".join(compact_turns))

        input_text = "\n\n".join(input_bits)

        summarizer_messages = [
            {
                "role": "system",
                "content": (
                    f"You are a narrative archivist. Your task is to maintain the '{title}'. "
                    f"This level of summary covers {context_desc}. "
                    f"Write {goal}. "
                    "Focus on facts, character decisions, and significant world changes. "
                    "Past tense, third person. No meta-commentary."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Update the following {title} with the new information provided:\n\n"
                    f"{input_text}"
                ),
            },
        ]

        try:
            summary = await self.provider.generate_text(
                messages=summarizer_messages,
                temperature=0.3,
                max_tokens=self._summary_max_tokens,
            )
            return summary.strip()
        except Exception as exc:
            logger.warning("Hierarchical summary update failed for %s: %s", level, exc)
            return existing or ""

    async def generate_legacy_summary_async(self, turns_to_compress: list[dict[str, str]]) -> str:
        """The original summarization logic."""
        if not turns_to_compress:
            return ""

        # Build a compact textual representation of the turns to compress.
        compressed_text_parts: list[str] = []
        for msg in turns_to_compress:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "assistant":
                compressed_text_parts.append(f"[Story]: {content}")
            elif role == "user":
                compressed_text_parts.append(f"[Player]: {content}")
        turns_blob = "\n".join(compressed_text_parts)

        summarizer_messages = [
            {
                "role": "system",
                "content": (
                    "You are a precise narrative archivist. "
                    "Given a sequence of story events and player choices, "
                    "write a single concise paragraph (2-4 sentences) summarising "
                    "the key plot events, character state, and decisions made. "
                    "Focus on facts and actions — no embellishment. "
                    "Write in past tense, third person."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Summarise the following story events into a single paragraph:\n\n{turns_blob}"
                ),
            },
        ]

        try:
            summary = await self.provider.generate_text(
                messages=summarizer_messages,
                temperature=0.3,
                max_tokens=self._summary_max_tokens,
            )
            logger.info("Rolling summary generated (%d chars).", len(summary))
            return summary.strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Rolling summarization failed: %s — using plaintext fallback.", exc)
            # Graceful fallback: join turns into a very short plain-text blob
            return " ".join(msg["content"][:80] for msg in turns_to_compress if msg.get("content"))

    async def generate_next_node_async(
        self,
        context: StoryContext,
        on_token_chunk: Callable[[str], None] | None = None,
    ) -> StoryNode:
        """
        Generate the next story node asynchronously using the 'Judge' pattern.
        
        Phase 1: Narrator - Generate narrative, choices, and atmospheric tags.
        Phase 2: Judge - Extract state changes (items, stats) from the narrative.
        """
        # PHASE 1: NARRATOR
        stream = on_token_chunk is not None
        messages = context.get_messages()
        attempts = 0
        max_attempts = self._repair_attempts + 1

        last_error = None
        content = ""
        narrator_node = None

        while attempts < max_attempts:
            try:
                if attempts == 0 and stream and on_token_chunk is not None:
                    content = await self._stream_with_callback_async(messages, on_token_chunk)
                else:
                    content = await self.provider.generate_json(
                        messages=messages,
                        schema=self._narrator_schema,
                        temperature=self._temperature if attempts == 0 else 0.2,
                        max_tokens=self._max_tokens,
                    )

                data = json.loads(content)
                narrator_node = NarratorNode(**data)
                break

            except (json.JSONDecodeError, TypeError, ValueError, Exception) as e:
                attempts += 1
                last_error = e
                if attempts >= max_attempts:
                    break

                logger.warning("Narrator repair attempt %d/%d for error: %s", attempts, self._repair_attempts, e)
                record_repair_attempt(model_name="narrator", error_type=type(e).__name__)

                messages = list(messages)
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": f"Your previous output was invalid JSON. Fix the following error: {e}. Respond with ONLY the corrected JSON narrative."
                })

        if not narrator_node:
            logger.error("Narrator phase failed: %s", last_error)
            return self._get_fallback_node()

        # PHASE 2: JUDGE (Extraction)
        extraction_node = await self._extract_state_delta_async(narrator_node.narrative)

        # Combine into final StoryNode
        return StoryNode(
            narrative=narrator_node.narrative,
            title=narrator_node.title,
            npcs_present=narrator_node.npcs_present,
            choices=narrator_node.choices,
            is_ending=narrator_node.is_ending,
            mood=narrator_node.mood,
            items_gained=extraction_node.items_gained,
            items_lost=extraction_node.items_lost,
            stat_updates=extraction_node.stat_updates,
        )

    async def _extract_state_delta_async(self, narrative: str) -> ExtractionNode:
        """
        Secondary phase: A focused LLM call to extract structured state changes 
        from the provided narrative text.
        """
        judge_messages = [
            {
                "role": "system",
                "content": (
                    "You are the 'Judge' for a role-playing game. Your task is to extract "
                    "player state changes from the narrative text provided. "
                    "Extract ONLY items gained, items lost, and stat updates (health, gold, reputation). "
                    "If the narrative doesn't mention a change, return an empty list or 0 for that field."
                )
            },
            {
                "role": "user",
                "content": f"Extract state changes from this narrative:\n\n{narrative}"
            }
        ]

        try:
            content = await self.provider.generate_json(
                messages=judge_messages,
                schema=self._extraction_schema,
                temperature=0.0,  # Strict extraction
                max_tokens=256,
            )
            data = json.loads(content)
            return ExtractionNode(**data)
        except Exception as e:
            logger.warning("Judge extraction failed: %s. Returning empty state delta.", e)
            return ExtractionNode()

    def _get_fallback_node(self) -> StoryNode:
        return StoryNode(
            narrative=(
                "The universe encounters an anomaly (LLM failed to format its response). "
                "You find yourself back where you started."
            ),
            choices=[
                Choice(text="Try doing something different."),
                Choice(text="Observe your surroundings"),
            ],
        )

    async def _stream_with_callback_async(
        self,
        messages: list[dict[str, str]],
        on_token_chunk: Callable[[str], None],
    ) -> str:
        """
        Consume the streaming response and call back for narrative updates.
        """
        buffer = ""
        last_sent_narrative_len = 0

        async for token in self.provider.stream_json(
            messages=messages,
            schema=self._narrator_schema,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        ):

            buffer += token

            try:
                # We use partial_mode="trailing-strings" so jiter doesn't truncate
                # the string we're currently receiving.
                parsed = jiter.from_json(buffer.encode(), partial_mode="trailing-strings")

                if isinstance(parsed, dict) and "narrative" in parsed:
                    current_narrative = parsed["narrative"]
                    if isinstance(current_narrative, str):
                        # Only send the *new* part of the narrative to the UI.
                        new_content = current_narrative[last_sent_narrative_len:]
                        if new_content:
                            on_token_chunk(new_content)
                            last_sent_narrative_len = len(current_narrative)
            except (ValueError, AttributeError):
                # JSON not yet parseable at all, or "narrative" key not yet fully present.
                continue

        return buffer

    async def save_state_async(self) -> Any:
        """Save the provider's internal state (KV cache)."""
        return await self.provider.save_state()

    async def load_state_async(self, state: Any) -> None:
        """Load a previously saved state (KV cache)."""
        await self.provider.load_state(state)

    def close(self) -> None:
        """Shut down the underlying model provider."""
        self.provider.close()
