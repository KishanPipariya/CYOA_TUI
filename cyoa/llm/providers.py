import abc
import json
import logging
import os
import threading
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Protocol

import httpx
from llama_cpp import Llama # type: ignore

logger = logging.getLogger(__name__)

class LLMProvider(abc.ABC):
    @abc.abstractmethod
    def count_tokens(self, text: str) -> int:
        """Count the number of tokens in the given text."""
        ...

    def count_tokens_in_messages(self, messages: List[Dict[str, str]]) -> int:
        """Count the number of tokens in a list of chat messages."""
        # Baseline implementation: sum up tokens in all parts of the message.
        # This is a good approximation for most models.
        total = 0
        for msg in messages:
            total += self.count_tokens(msg.get("role", ""))
            total += self.count_tokens(msg.get("content", ""))
        return total

    @abc.abstractmethod
    async def generate_text(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> str:
        """Generate a plain text response."""
        ...

    @abc.abstractmethod
    async def generate_json(
        self,
        messages: List[Dict[str, str]],
        schema: Dict[str, Any],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> str:
        """Generate a JSON response conforming to the schema."""
        ...

    @abc.abstractmethod
    def stream_json(
        self,
        messages: List[Dict[str, str]],
        schema: Dict[str, Any],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Stream a JSON response chunk by chunk."""
        ...

    async def save_state(self) -> Optional[bytes]:
        """Save the provider's internal state (e.g. KV cache) if supported."""
        return None

    async def load_state(self, state: bytes) -> None:
        """Load the provider's internal state (e.g. KV cache) if supported."""
        pass

    def close(self) -> None:
        """Release resources (optional)."""
        pass


class LlamaCppProvider(LLMProvider):
    def __init__(self, model_path: str, n_ctx: int = 4096):
        import os
        cpu_threads = max(1, (os.cpu_count() or 8) // 2)
        self.llm = Llama(
            model_path=model_path,
            n_ctx=n_ctx,
            n_threads=cpu_threads,
            n_gpu_layers=-1,
            flash_attn=True,
            verbose=False,
        )
        self._lock = threading.Lock()

    def count_tokens(self, text: str) -> int:
        """Measure tokens exactly using the GGUF's own tokenizer."""
        if not text:
            return 0
        try:
            with self._lock:
                # self.llm.tokenize returns a list of token IDs
                return len(self.llm.tokenize(text.encode("utf-8"), add_bos=False))
        except Exception as e:
            logger.warning("LlamaCpp tokenization failed: %s — using fallback.", e)
            return len(text) // 4

    async def generate_text(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> str:
        import asyncio

        def _run():
            with self._lock:
                return self.llm.create_chat_completion(
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=False,
                )

        response = await asyncio.to_thread(_run)
        return response["choices"][0]["message"]["content"]

    async def generate_json(
        self,
        messages: List[Dict[str, str]],
        schema: Dict[str, Any],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> str:
        import asyncio

        def _run():
            with self._lock:
                return self.llm.create_chat_completion(
                    messages=messages,
                    response_format={"type": "json_object", "schema": schema},
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=False,
                )

        response = await asyncio.to_thread(_run)
        return response["choices"][0]["message"]["content"]

    async def stream_json(
        self,
        messages: List[Dict[str, str]],
        schema: Dict[str, Any],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        import asyncio

        loop = asyncio.get_running_loop()
        q: asyncio.Queue[Optional[str]] = asyncio.Queue()
        # Fix: use threading.Event to signal cancellation back to the producer thread
        cancel_event = threading.Event()

        def producer():
            try:
                # We check the event BEFORE locking to avoid long waits if already cancelled
                if cancel_event.is_set():
                    return

                with self._lock:
                    stream = self.llm.create_chat_completion(
                        messages=messages,
                        response_format={"type": "json_object", "schema": schema},
                        max_tokens=max_tokens,
                        temperature=temperature,
                        stream=True,
                    )
                    for chunk in stream:
                        if cancel_event.is_set():
                            break
                        delta = chunk["choices"][0].get("delta", {})
                        token = delta.get("content", "")
                        if token:
                            loop.call_soon_threadsafe(q.put_nowait, token)
            except Exception as e:
                logger.error("Error in LlamaCppProvider stream producer: %s", e)
            finally:
                loop.call_soon_threadsafe(q.put_nowait, None)

        loop.run_in_executor(None, producer)

        try:
            while True:
                token = await q.get()
                if token is None:
                    break
                yield token
        finally:
            # Signal the producer thread to stop if we stop consuming tokens (e.g. cancellation)
            cancel_event.set()
    async def save_state(self) -> Optional[bytes]:
        """Save the current KV-cache state of the LLM."""
        import asyncio

        def _run():
            with self._lock:
                return self.llm.save_state()

        try:
            return await asyncio.to_thread(_run)
        except Exception as e:
            logger.warning("Failed to save LlamaCpp state: %s", e)
            return None

    async def load_state(self, state: bytes) -> None:
        """Load a previously saved KV-cache state."""
        import asyncio

        def _run():
            with self._lock:
                self.llm.load_state(state)

        try:
            await asyncio.to_thread(_run)
        except Exception as e:
            logger.warning("Failed to load LlamaCpp state: %s", e)

    def close(self) -> None:
        """Clear the LLM instance to release memory/threads."""
        with self._lock:
            if hasattr(self, "llm"):
                del self.llm


class OllamaProvider(LLMProvider):
    def __init__(self, model: str, base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = f"{base_url.rstrip('/')}/api/chat"
        # Tiktoken fallback for high-precision estimation without model weights
        try:
            import tiktoken
            self.tokenizer = tiktoken.get_encoding("cl100k_base")
        except ImportError:
            self.tokenizer = None

    def count_tokens(self, text: str) -> int:
        if not text:
            return 0
        if self.tokenizer:
            return len(self.tokenizer.encode(text))
        # Fallback to rough estimate if tiktoken is missing
        return len(text) // 4

    async def generate_text(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> str:
        async with httpx.AsyncClient(timeout=120.0) as client:
            payload = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "options": {
                    "num_predict": max_tokens,
                    "temperature": temperature,
                },
            }
            response = await client.post(self.base_url, json=payload)
            response.raise_for_status()
            return response.json()["message"]["content"]

    async def generate_json(
        self,
        messages: List[Dict[str, str]],
        schema: Dict[str, Any],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> str:
        async with httpx.AsyncClient(timeout=120.0) as client:
            payload = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "format": schema,
                "options": {
                    "num_predict": max_tokens,
                    "temperature": temperature,
                },
            }
            response = await client.post(self.base_url, json=payload)
            response.raise_for_status()
            return response.json()["message"]["content"]

    async def stream_json(
        self,
        messages: List[Dict[str, str]],
        schema: Dict[str, Any],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        async with httpx.AsyncClient(timeout=120.0) as client:
            payload = {
                "model": self.model,
                "messages": messages,
                "stream": True,
                "format": schema,
                "options": {
                    "num_predict": max_tokens,
                    "temperature": temperature,
                },
            }
            async with client.stream("POST", self.base_url, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                        if "message" in chunk and "content" in chunk["message"]:
                            yield chunk["message"]["content"]
                        if chunk.get("done", False):
                            break
                    except json.JSONDecodeError:
                        logger.warning("Failed to decode Ollama stream chunk: %s", line)
                        continue
