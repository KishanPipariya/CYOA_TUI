import json
import os
import re
import logging
import pathlib
import jinja2
from typing import Callable, Optional
from llama_cpp import Llama  # type: ignore
from cyoa.core.models import StoryNode

__all__ = ["StoryContext", "StoryGenerator"]

# Configurable via .env / environment — defaults used if not set
MAX_CONTEXT_TURNS = int(os.getenv("LLM_MAX_TURNS", "10"))

# Regex to find the start of the "narrative" value in streaming JSON
_NARRATIVE_START_RE = re.compile(r'"narrative"\s*:\s*"')
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

        template_dir = pathlib.Path(__file__).parent / "templates"
        self.jinja_env = jinja2.Environment(loader=jinja2.FileSystemLoader(template_dir))
        self.system_template = self.jinja_env.get_template("system_prompt.j2")

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

    def get_messages(self) -> list[dict[str, str]]:
        system_content = self.system_template.render(
            inventory=self.inventory, stats=self.player_stats, memories=self.memories
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
    ) -> str:  # noqa: C901, PLR0912
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
        in_narrative = False
        narrative_done = False
        escape_next = False
        search_offset = 0

        while True:
            msg_type, val = await q.get()
            if msg_type == "error":
                logger.error("Error in LLM generator: %s", val)
                break
            if msg_type == "done":
                break

            token = val
            buffer += token

            if not in_narrative and not narrative_done:
                search_from = max(0, search_offset - 15)
                match = _NARRATIVE_START_RE.search(buffer, search_from)
                if match:
                    in_narrative = True
                    tail = buffer[match.end() :]
                    chunk_buf = ""
                    for ch in tail:
                        if escape_next:
                            escape_next = False
                            chunk_buf += ch
                        elif ch == "\\":
                            escape_next = True
                            chunk_buf += ch
                        elif ch == '"':
                            in_narrative = False
                            narrative_done = True
                            break
                        else:
                            chunk_buf += ch
                    if chunk_buf:
                        on_token_chunk(chunk_buf)
                else:
                    search_offset = len(buffer)
            elif in_narrative:
                chunk_buf = ""
                for ch in token:
                    if escape_next:
                        escape_next = False
                        chunk_buf += ch
                    elif ch == "\\":
                        escape_next = True
                        chunk_buf += ch
                    elif ch == '"':
                        in_narrative = False
                        narrative_done = True
                        break
                    else:
                        chunk_buf += ch
                if chunk_buf:
                    on_token_chunk(chunk_buf)

        return buffer
