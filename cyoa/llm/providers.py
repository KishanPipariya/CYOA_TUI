import abc
import json
import logging
import os
import threading
from collections.abc import AsyncIterator, Iterator
from typing import Any, cast

import httpx
from llama_cpp import Llama

from cyoa.core.observability import LLMObservedSession

logger = logging.getLogger(__name__)


class LLMProvider(abc.ABC):
    @abc.abstractmethod
    def count_tokens(self, text: str) -> int:
        """Count the number of tokens in the given text."""
        ...

    def count_tokens_in_messages(self, messages: list[dict[str, str]]) -> int:
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
        messages: list[dict[str, str]],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> str:
        """Generate a plain text response."""
        ...

    @abc.abstractmethod
    async def generate_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> str:
        """Generate a JSON response conforming to the schema."""
        ...

    @abc.abstractmethod
    def stream_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Stream a JSON response chunk by chunk."""
        ...

    async def save_state(self) -> Any:
        """Save the provider's internal state (e.g. KV cache) if supported."""
        return None

    async def load_state(self, state: Any) -> None:  # noqa: B027
        """Load the provider's internal state (e.g. KV cache) if supported."""
        ...

    def close(self) -> None:  # noqa: B027
        """Release resources (optional)."""
        ...


class LlamaCppProvider(LLMProvider):
    def __init__(self, model_path: str, n_ctx: int = 4096):
        cpu_threads = max(1, (os.cpu_count() or 8) // 2)
        self.model_path = model_path
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
            # Fix: Use non-blocking acquire to avoid hanging the UI thread if the
            # LLM is currently busy generating a response in another thread.
            if self._lock.acquire(blocking=False):
                try:
                    # self.llm.tokenize returns a list of token IDs
                    return len(self.llm.tokenize(text.encode("utf-8"), add_bos=False))
                finally:
                    self._lock.release()
            else:
                # Fallback to estimate if the model is busy
                return len(text) // 4
        except Exception as e:
            logger.warning("LlamaCpp tokenization failed: %s — using fallback.", e)
            return len(text) // 4

    def _prepare_stream_params(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any] | None,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        """Prepare parameters for Llama.create_chat_completion."""
        params: dict[str, Any] = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if schema:
            params["response_format"] = {"type": "json_object", "schema": schema}
        return params

    async def _run_cancellable_stream(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any] | None = None,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        import asyncio

        loop = asyncio.get_running_loop()
        q: asyncio.Queue[str | None] = asyncio.Queue()
        cancel_event = threading.Event()

        def producer() -> None:
            session = None
            try:
                session = LLMObservedSession(model_name=self.model_path, task="generation").start()
                if cancel_event.is_set():
                    session.end(success=False)
                    return

                with self._lock:
                    if cancel_event.is_set():
                        session.end(success=False)
                        return

                    params = self._prepare_stream_params(messages, schema, max_tokens, temperature)
                    stream = self.llm.create_chat_completion(**cast(Any, params))
                    for chunk in cast(Iterator[Any], stream):
                        if cancel_event.is_set():
                            break
                        delta = chunk["choices"][0].get("delta", {})
                        token = delta.get("content", "")
                        if token:
                            session.report_first_token()
                            session.report_token(self.count_tokens(token))
                            loop.call_soon_threadsafe(q.put_nowait, token)
                session.end(success=True)
            except Exception as e:
                logger.error("Error in LlamaCppProvider stream producer: %s", e)
                if session:
                    session.end(success=False)
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
            cancel_event.set()

    async def generate_text(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> str:
        content = []
        async for token in self._run_cancellable_stream(
            messages=messages, max_tokens=max_tokens, temperature=temperature
        ):
            content.append(token)
        return "".join(content)

    async def generate_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> str:
        content = []
        async for token in self._run_cancellable_stream(
            messages=messages,
            schema=schema,
            max_tokens=max_tokens,
            temperature=temperature,
        ):
            content.append(token)
        return "".join(content)

    async def stream_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        async for token in self._run_cancellable_stream(
            messages=messages,
            schema=schema,
            max_tokens=max_tokens,
            temperature=temperature,
        ):
            yield token

    async def save_state(self) -> Any:
        """Save the current KV-cache state of the LLM."""
        import asyncio

        def _run() -> Any:
            with self._lock:
                return self.llm.save_state()

        try:
            return await asyncio.to_thread(_run)
        except Exception as e:
            logger.warning("Failed to save LlamaCpp state: %s", e)
            return None

    async def load_state(self, state: Any) -> None:
        """Load a previously saved KV-cache state."""
        import asyncio

        def _run() -> None:
            with self._lock:
                self.llm.load_state(state)

        try:
            await asyncio.to_thread(_run)
        except Exception as e:
            logger.warning("Failed to load LlamaCpp state: %s", e)

    def close(self) -> None:
        """Clear the LLM instance to release memory/threads."""
        # Use a short timeout to avoid hanging the UI shutdown if a thread is stuck
        acquired = self._lock.acquire(timeout=0.5)
        try:
            if hasattr(self, "llm"):
                # If we couldn't get the lock, it means a thread is still running.
                # Since we are likely shutting down the app, we prefer to skip
                # the explicit 'del' rather than hanging the process.
                if acquired:
                    del self.llm
                else:
                    logger.warning(
                        "LlamaCpp lock held during close(); skipping explicit cleanup to avoid hang."
                    )
        finally:
            if acquired:
                self._lock.release()


class OllamaProvider(LLMProvider):
    def __init__(self, model: str, base_url: str = "http://localhost:11434"):
        self.tokenizer: Any | None = None
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
        messages: list[dict[str, str]],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> str:
        session = LLMObservedSession(model_name=self.model, task="generate_text").start()
        try:
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
                data = response.json()
                content = str(data["message"]["content"])

                # For non-streaming, TTFT is the same as total time in this simplistic view
                session.report_first_token()
                session.report_token(self.count_tokens(content))
                session.end(success=True)
                return content
        except Exception:
            session.end(success=False)
            raise

    async def generate_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> str:
        session = LLMObservedSession(model_name=self.model, task="generate_json").start()
        try:
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
                data = response.json()
                content = str(data["message"]["content"])

                session.report_first_token()
                session.report_token(self.count_tokens(content))
                session.end(success=True)
                return content
        except Exception:
            session.end(success=False)
            raise

    async def stream_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        session = LLMObservedSession(model_name=self.model, task="stream_json").start()
        try:
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
                                session.report_first_token()
                                content = chunk["message"]["content"]
                                session.report_token(self.count_tokens(content))
                                yield content
                            if chunk.get("done", False):
                                break
                        except json.JSONDecodeError:
                            logger.warning("Failed to decode Ollama stream chunk: %s", line)
                            continue
            session.end(success=True)
        except Exception:
            session.end(success=False)
            raise
