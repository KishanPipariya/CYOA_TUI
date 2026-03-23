import jiter
import json
import os
import logging
import pathlib
import jinja2
from typing import Callable, Optional, List, Dict, Any, Union

from cyoa.core.models import StoryNode
from cyoa.llm.providers import LLMProvider, LlamaCppProvider, OllamaProvider

__all__ = ["StoryContext", "ModelBroker", "StoryGenerator"]

# Configurable via .env / environment — defaults used if not set
DEFAULT_TOKEN_BUDGET = int(os.getenv("LLM_TOKEN_BUDGET", "2048"))

# Rolling summarization fires when the number of stored turn *pairs* reaches
# this fraction of token_budget. At 0.8 we still have 20% headroom before the
# hard sliding-window truncation kicks in.
SUMMARIZATION_THRESHOLD = float(os.getenv("LLM_SUMMARY_THRESHOLD", "0.8"))

# Rough characters-per-token estimate (conservative for English prose).
_CHARS_PER_TOKEN = 4

logger = logging.getLogger(__name__)


class StoryContext:
    def __init__(
        self,
        starting_prompt: str,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
        token_counter: Optional[Callable[[str], int]] = None,
    ) -> None:
        self.token_budget = token_budget
        self.token_counter = token_counter or (lambda x: len(x) // _CHARS_PER_TOKEN)
        self.starting_prompt = starting_prompt
        self.history: list[dict[str, str]] = [
            {"role": "user", "content": starting_prompt}
        ]
        self.inventory: list[str] = []
        self.player_stats: dict[str, int] = {}
        self.memories: list[str] = []
        # Rolling summarization: paragraph produced by compressing old turns.
        self.rolling_summary: Optional[str] = None

        template_dir = pathlib.Path(__file__).parent / "templates"
        self.jinja_env = jinja2.Environment(loader=jinja2.FileSystemLoader(template_dir))
        self.system_template = self.jinja_env.get_template("system_prompt.j2")

    # ------------------------------------------------------------------
    # Turn management
    # ------------------------------------------------------------------

    def add_turn(
        self,
        raw_narrative: str,
        user_choice: str,
        inventory: Optional[list[str]] = None,
        player_stats: Optional[dict[str, int]] = None,
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

    def set_rolling_summary(self, summary: str) -> None:
        """Store a compressed narrative summary produced by the summarization agent.

        After summarization the oldest summarized turns are pruned to reclaim context space.
        """
        self.rolling_summary = summary
        # Pruning is handled by the regular _prune_history or add_turn logic,
        # but here we can be more aggressive to clear out summarized content.
        # We'll drop approximately half of the dynamic history if we're over budget.
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
        system_content = self.system_template.render(
            inventory=self.inventory,
            stats=self.player_stats,
            memories=self.memories,
            rolling_summary=self.rolling_summary,
        )
        return [{"role": "system", "content": system_content}] + self.history


class ModelBroker:
    def __init__(
        self,
        model_path: Optional[str] = None,
        n_ctx: Optional[int] = None,
        provider: Optional[LLMProvider] = None,
    ) -> None:
        if provider:
            self.provider = provider
        else:
            self.provider = self._create_provider_from_env(model_path, n_ctx)

        # Token budget for StoryContext is half of the provider's context window
        # to leave plenty of room for generation and system overhead.
        default_budget = (n_ctx or 4096) // 2
        self.token_budget = int(os.getenv("LLM_TOKEN_BUDGET", str(default_budget)))

        self._schema = StoryNode.model_json_schema()
        self._temperature = float(os.getenv("LLM_TEMPERATURE", "0.6"))
        self._max_tokens = int(os.getenv("LLM_MAX_TOKENS", "512"))
        # Maximum tokens for the "Story So Far" summary paragraph.
        self._summary_max_tokens = int(os.getenv("LLM_SUMMARY_MAX_TOKENS", "200"))

    def _create_provider_from_env(
        self, model_path: Optional[str] = None, n_ctx: Optional[int] = None
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

            n_ctx_val = n_ctx or int(os.getenv("LLM_N_CTX", "4096"))
            return LlamaCppProvider(model_path=m_path, n_ctx=n_ctx_val)

    async def generate_summary_async(self, turns_to_compress: list[dict[str, str]]) -> str:
        """Compress a sequence of (assistant, user) turn messages into a dense
        narrative paragraph suitable for a rolling context window.
        """
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
                    "Summarise the following story events into a single paragraph:\n\n"
                    f"{turns_blob}"
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
            return " ".join(
                msg["content"][:80] for msg in turns_to_compress if msg.get("content")
            )

    async def generate_next_node_async(
        self,
        context: StoryContext,
        on_token_chunk: Optional[Callable[[str], None]] = None,
    ) -> StoryNode:
        """
        Generate the next story node asynchronously.
        """
        stream = on_token_chunk is not None

        if stream and on_token_chunk is not None:
            content = await self._stream_with_callback_async(
                context.get_messages(), on_token_chunk
            )
        else:
            content = await self.provider.generate_json(
                messages=context.get_messages(),
                schema=self._schema,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )

        try:
            data = json.loads(content)
            return StoryNode(**data)
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            logger.error("Failed to parse LLM output: %s\nOutput was: %s", e, content)
            return StoryNode(
                narrative=(
                    "The universe encounters an anomaly (LLM failed to format its response). "
                    "You find yourself back where you started."
                ),
                choices=[{"text": "Try doing something different."}],
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
            schema=self._schema,
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
            except (ValueError, jiter.JiterError):
                # JSON not yet parseable at all, or "narrative" key not yet fully present.
                continue

        return buffer


# Alias for backward compatibility during transition
StoryGenerator = ModelBroker
