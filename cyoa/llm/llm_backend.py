import json
import os
import re
import logging
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
        self.max_turns: int = max_turns
        self.history: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    """You are a dark fantasy interactive fiction engine.
Describe the starting scenario where the player wakes up in a cold, unfamiliar dungeon cell.
Provide 2-3 choices for what they can do next.
You MUST provide a creative 'title' for this new adventure in the JSON response.
Manage the player's inventory using 'items_gained' and 'items_lost'. Track when they acquire or lose items. Create context-sensitive choices if they possess specific items!
Manage the player's stats (health, gold, reputation) using 'stat_updates'. Provide stat changes (e.g. {"health": -10, "gold": 50}) when the narrative dictates it. Low health should disable risky choices, high reputation unlocks dialogue.
When the story reaches a definitive conclusion (victory, death, escape, etc), set 'is_ending' to true and provide an empty choices list.
Ensure your output is strictly valid JSON matching the requested schema.
"""
                ),
            }
        ]
        self.starting_prompt = starting_prompt
        self.history.append({"role": "user", "content": starting_prompt})

    def add_turn(
        self,
        raw_narrative: str,
        user_choice: str,
        inventory: Optional[list[str]] = None,
        player_stats: Optional[dict[str, int]] = None,
    ) -> None:
        """Add an assistant turn (raw narrative) and user choice, trimming old turns."""
        self.history.append({"role": "assistant", "content": raw_narrative})
        inv_str = f"Current Inventory: {', '.join(inventory) if inventory else 'Empty'}"
        stats_str = f"Current Stats: {player_stats}" if player_stats else ""
        sys_note = (
            f"[System Note: {inv_str} | {stats_str}]"
            if stats_str
            else f"[System Note: {inv_str}]"
        )
        self.history.append(
            {"role": "user", "content": f"I choose: {user_choice}\n\n{sys_note}"}
        )

        # Sliding window: always keep system (0) + initial prompt (1)
        non_system = self.history[2:]
        if len(non_system) > self.max_turns * 2:
            self.history = self.history[:2] + non_system[2:]

    def inject_memory(self, memories: list[str]) -> None:
        """
        Fix #5: Insert or REPLACE a memory block in context history.
        Replaces any existing memory block rather than accumulating duplicates,
        which would inflate token count rapidly across many turns.
        """
        if not memories:
            return

        memory_text = "[Memory — relevant past scenes for context]\n" + "\n---\n".join(
            memories
        )
        new_block = {"role": "system", "content": memory_text}

        # Check if a memory block already exists; replace it in-place
        for i, msg in enumerate(self.history):
            if msg["role"] == "system" and msg["content"].startswith("[Memory"):
                self.history[i] = new_block
                return

        # No existing block — insert before the last user message
        insert_idx = len(self.history) - 1
        while insert_idx > 0 and self.history[insert_idx]["role"] != "user":
            insert_idx -= 1
        self.history.insert(insert_idx, new_block)


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
            messages=context.history,
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
