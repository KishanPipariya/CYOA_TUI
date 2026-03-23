import jiter
import json
import os
import logging
import pathlib
import jinja2
from typing import Callable, Optional
from llama_cpp import Llama  # type: ignore
from cyoa.core.models import StoryNode

__all__ = ["StoryContext", "StoryGenerator"]

# Configurable via .env / environment — defaults used if not set
MAX_CONTEXT_TURNS = int(os.getenv("LLM_MAX_TURNS", "10"))

# Rolling summarization fires when the number of stored turn *pairs* reaches
# this fraction of max_turns. At 0.8 we still have 20% headroom before the
# hard sliding-window truncation kicks in.
SUMMARIZATION_THRESHOLD = float(os.getenv("LLM_SUMMARY_THRESHOLD", "0.8"))

# Rough characters-per-token estimate (conservative for English prose).
_CHARS_PER_TOKEN = 4

logger = logging.getLogger(__name__)


class StoryContext:
    def __init__(
        self, starting_prompt: str, max_turns: int = MAX_CONTEXT_TURNS
    ) -> None:
        self.max_turns = max_turns
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

        # Sliding window: keep only initial prompt (0) and max_turns tail
        if len(self.history) > self.max_turns * 2 + 1:
            self.history = [self.history[0]] + self.history[-(self.max_turns * 2):]

    def inject_memory(self, memories: list[str]) -> None:
        """Store memories to be injected dynamically into the system prompt."""
        self.memories = memories

    def set_rolling_summary(self, summary: str) -> None:
        """Store a compressed narrative summary produced by the summarization agent.

        After summarization the oldest turn *pairs* (excluding the initial
        prompt) are pruned to reclaim context space.  The half that were
        summarized is removed; the remaining recent half stays intact so the
        LLM always has raw dialogue for the freshest turns.
        """
        self.rolling_summary = summary

        # Drop the oldest half of the turn pairs (everything except the
        # initial prompt and the freshest max_turns//2 pairs).
        keep_pairs = max(1, self.max_turns // 2)
        # history[0] is the initial user prompt; turn pairs start at index 1.
        tail = self.history[1:]  # list of (assistant, user) pairs flattened
        if len(tail) > keep_pairs * 2:
            self.history = [self.history[0]] + tail[-(keep_pairs * 2):]

    # ------------------------------------------------------------------
    # Summarization trigger
    # ------------------------------------------------------------------

    def needs_summarization(self, threshold: float = SUMMARIZATION_THRESHOLD) -> bool:
        """Return True when stored turn pairs reach *threshold* fraction of max_turns.

        The check is based purely on message count, not token count, which
        keeps it fast and free of tokenizer dependencies.  At 0.8 (default)
        we still have 20 % headroom before the hard sliding-window truncation
        would fire.
        """
        # Number of complete turn pairs currently in history (excluding the initial prompt).
        turn_pairs = (len(self.history) - 1) // 2
        return turn_pairs >= int(self.max_turns * threshold)

    def get_turns_for_summary(self) -> list[dict[str, str]]:
        """Return the oldest turn pairs that should be compressed.

        We hand the *older* half to the summariser and keep the *newer* half
        as raw dialogue.  This guarantees the LLM always has the freshest
        turns verbatim while very old context is compressed.
        """
        tail = self.history[1:]  # exclude opening user prompt
        keep_pairs = max(1, self.max_turns // 2)
        summarise_tail = tail[: -(keep_pairs * 2)] if keep_pairs * 2 < len(tail) else tail
        return summarise_tail

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


class StoryGenerator:
    def __init__(self, model_path: str, n_ctx: Optional[int] = None) -> None:
        n_ctx_val = n_ctx or int(os.getenv("LLM_N_CTX", "4096"))
        cpu_threads = max(1, (os.cpu_count() or 8) // 2)
        self.llm = Llama(
            model_path=model_path,
            n_ctx=n_ctx_val,
            n_threads=cpu_threads,
            n_gpu_layers=-1,
            flash_attn=True,
            verbose=False,
        )
        self._schema = StoryNode.model_json_schema()
        self._temperature = float(os.getenv("LLM_TEMPERATURE", "0.6"))
        self._max_tokens = int(os.getenv("LLM_MAX_TOKENS", "512"))
        # Maximum tokens for the "Story So Far" summary paragraph.
        self._summary_max_tokens = int(os.getenv("LLM_SUMMARY_MAX_TOKENS", "200"))

    async def generate_summary_async(self, turns_to_compress: list[dict[str, str]]) -> str:
        """Compress a sequence of (assistant, user) turn messages into a dense
        narrative paragraph suitable for a rolling context window.

        The resulting text is injected as the ``<rolling_summary>`` block in
        the system prompt, replacing the raw turns that it covers.  This keeps
        narrative momentum without consuming precious context tokens.

        Parameters
        ----------
        turns_to_compress:
            A flat list of ``{role, content}`` dicts representing the old turns
            (obtained from :meth:`StoryContext.get_turns_for_summary`).

        Returns
        -------
        str
            A concise paragraph of ≤ ``_summary_max_tokens`` tokens describing
            the summarised story arc.  Falls back to a joined plaintext
            representation of the turns if the LLM call fails.
        """
        import asyncio

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
            response = await asyncio.to_thread(
                self.llm.create_chat_completion,
                messages=summarizer_messages,
                temperature=0.3,  # Lower temp for factual fidelity
                max_tokens=self._summary_max_tokens,
                stream=False,
            )
            summary = response["choices"][0]["message"]["content"].strip()
            logger.info("Rolling summary generated (%d chars).", len(summary))
            return summary
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

        If `on_token_chunk` is provided, stream tokens and call it with chunked text
        of the narrative as it streams in (typewriter effect).
        The complete JSON is still assembled and validated after streaming.
        """
        stream = on_token_chunk is not None
        import asyncio

        response = await asyncio.to_thread(
            self.llm.create_chat_completion,
            messages=context.get_messages(),
            response_format={
                "type": "json_object",
                "schema": self._schema,
            },
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            stream=stream,
        )

        if stream and on_token_chunk is not None:
            content = await self._stream_with_callback_async(response, on_token_chunk)
        else:
            content = response["choices"][0]["message"]["content"]

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
        stream_iter,
        on_token_chunk: Callable[[str], None],
    ) -> str:
        """
        Consume the streaming response in a background thread to prevent blocking
        the asyncio event loop, and queue chunks back for processing.
        """
        import asyncio
        loop = asyncio.get_running_loop()
        q = asyncio.Queue()

        def producer():
            try:
                for chunk in stream_iter:
                    delta = chunk["choices"][0].get("delta", {})
                    token = delta.get("content", "")
                    if token:
                        loop.call_soon_threadsafe(q.put_nowait, ("token", token))
                loop.call_soon_threadsafe(q.put_nowait, ("done", None))
            except Exception as e:
                loop.call_soon_threadsafe(q.put_nowait, ("error", e))

        _ = asyncio.create_task(asyncio.to_thread(producer))

        buffer = ""
        last_sent_narrative_len = 0

        while True:
            msg_type, val = await q.get()
            if msg_type == "error":
                logger.error("Error in LLM generator: %s", val)
                break
            if msg_type == "done":
                break

            token = val
            buffer += token

            try:
                # We use partial_mode="trailing-strings" so jiter doesn't truncate
                # the string we're currently receiving. It returns what's been
                # parsed so far as a Python dict/object.
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
                # This is normal during the first several tokens of the stream.
                continue

        return buffer
