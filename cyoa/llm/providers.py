import abc
import asyncio
import json
import logging
import os
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from typing import Any, cast

import httpx
from llama_cpp import Llama

from cyoa.core.observability import LLMObservedSession

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    streaming_json: bool = True
    structured_json: bool = True
    state_transfer: bool = False


def _fallback_token_estimate(text: str) -> int:
    return len(text) // 4


def count_messages_tokens(messages: list[dict[str, str]], token_counter: Callable[[str], int]) -> int:
    """Helper to count tokens in a list of chat messages using a counter function.

    Summing tokens from role and content is a good approximation for most models.
    """
    total = 0
    for msg in messages:
        total += token_counter(msg.get("role", ""))
        total += token_counter(msg.get("content", ""))
    return total


class LLMProvider(abc.ABC):
    def capabilities(self) -> ProviderCapabilities:
        """Return a normalized provider feature map used by the broker/UI."""
        return ProviderCapabilities()

    @abc.abstractmethod
    def count_tokens(self, text: str) -> int:
        """Count the number of tokens in the given text."""
        ...

    def count_tokens_in_messages(self, messages: list[dict[str, str]]) -> int:
        """Count the number of tokens in a list of chat messages."""
        return count_messages_tokens(messages, self.count_tokens)

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


class _InterruptionLogitsProcessor:
    """Check a threading.Event and force-stop generation by emitting the EOS token.
    This allows truly interrupting the C++ generation loop in llama.cpp.
    """

    def __init__(self, cancel_event: threading.Event, eos_token_id: int):
        self.cancel_event = cancel_event
        self.eos_token_id = eos_token_id

    def __call__(self, input_ids: Any, scores: Any) -> Any:
        if self.cancel_event.is_set():
            # Force selecting the EOS token by setting its score to a very large
            # value and all others to very small. This breaks the C++ generation loop.
            import numpy as np

            # Note: scores is typically a numpy array in modern llama-cpp-python
            scores.fill(-np.inf)
            scores[self.eos_token_id] = 0.0
        return scores


class ProviderResponseError(RuntimeError):
    """Raised when a provider returns a structurally invalid payload."""


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
        self._stream_events_lock = threading.Lock()
        self._active_cancel_events: set[threading.Event] = set()
        self._closing = False

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
            # Fallback to estimate if the model is busy
            return _fallback_token_estimate(text)
        except Exception as e:
            logger.warning("LlamaCpp tokenization failed: %s — using fallback.", e)
            return _fallback_token_estimate(text)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming_json=True, structured_json=True, state_transfer=True)

    def _prepare_stream_params(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any] | None,
        max_tokens: int,
        temperature: float,
        cancel_event: threading.Event | None = None,
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

        if cancel_event:
            # Inject the interruption processor to stop generation mid-token
            params["logits_processor"] = [
                _InterruptionLogitsProcessor(cancel_event, self.llm.token_eos())
            ]
        return params

    def _start_generation_session(self) -> LLMObservedSession:
        return LLMObservedSession(model_name=self.model_path, task="generation").start()

    def _register_cancel_event(self, cancel_event: threading.Event) -> None:
        with self._stream_events_lock:
            self._active_cancel_events.add(cancel_event)
            if self._closing:
                cancel_event.set()

    def _unregister_cancel_event(self, cancel_event: threading.Event) -> None:
        with self._stream_events_lock:
            self._active_cancel_events.discard(cancel_event)

    def _signal_active_streams(self) -> None:
        with self._stream_events_lock:
            self._closing = True
            active_events = tuple(self._active_cancel_events)
        for cancel_event in active_events:
            cancel_event.set()

    def _stream_completion(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any] | None,
        max_tokens: int,
        temperature: float,
        cancel_event: threading.Event,
    ) -> Iterator[Any]:
        params = self._prepare_stream_params(messages, schema, max_tokens, temperature, cancel_event)
        return cast(Iterator[Any], self.llm.create_chat_completion(**cast(Any, params)))

    def _extract_stream_token(self, chunk: Any) -> str:
        if not isinstance(chunk, dict):
            logger.warning("Ignoring unexpected llama.cpp stream chunk type: %s", type(chunk).__name__)
            return ""
        try:
            delta = chunk["choices"][0].get("delta", {})
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning("Ignoring malformed llama.cpp stream chunk: %s", exc)
            return ""
        content = delta.get("content", "")
        return content if isinstance(content, str) else ""

    def _publish_stream_token(
        self,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue[str | None],
        session: LLMObservedSession,
        token: str,
    ) -> None:
        session.report_first_token()
        session.report_token(self.count_tokens(token))
        loop.call_soon_threadsafe(queue.put_nowait, token)

    def _run_stream_producer(
        self,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue[str | None],
        messages: list[dict[str, str]],
        schema: dict[str, Any] | None,
        max_tokens: int,
        temperature: float,
        cancel_event: threading.Event,
    ) -> None:
        session: LLMObservedSession | None = None
        success = False
        try:
            session = self._start_generation_session()
            if cancel_event.is_set():
                return

            with self._lock:
                if cancel_event.is_set():
                    return
                for chunk in self._stream_completion(
                    messages, schema, max_tokens, temperature, cancel_event
                ):
                    if cancel_event.is_set():
                        break
                    token = self._extract_stream_token(chunk)
                    if token:
                        self._publish_stream_token(loop, queue, session, token)
            success = not cancel_event.is_set()
        except Exception as exc:
            logger.exception("Error in LlamaCppProvider stream producer: %s", exc)
        finally:
            if session is not None:
                session.end(success=success)
            loop.call_soon_threadsafe(queue.put_nowait, None)

    async def _run_cancellable_stream(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any] | None = None,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[str | None] = asyncio.Queue()
        cancel_event = threading.Event()
        self._register_cancel_event(cancel_event)

        loop.run_in_executor(
            None,
            self._run_stream_producer,
            loop,
            q,
            messages,
            schema,
            max_tokens,
            temperature,
            cancel_event,
        )

        try:
            while True:
                token = await q.get()
                if token is None:
                    break
                yield token
        finally:
            cancel_event.set()
            self._unregister_cancel_event(cancel_event)

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
        def _run() -> None:
            with self._lock:
                self.llm.load_state(state)

        try:
            await asyncio.to_thread(_run)
        except Exception as e:
            logger.warning("Failed to load LlamaCpp state: %s", e)

    def close(self) -> None:
        """Clear the LLM instance to release memory/threads."""
        self._signal_active_streams()

        # Give interrupted generation a short window to exit before tearing down the model.
        acquired = self._lock.acquire(timeout=2.0)
        try:
            if hasattr(self, "llm"):
                if acquired:
                    del self.llm
                else:
                    logger.warning(
                        "LlamaCpp lock held during close(); active generation did not stop in time."
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
        return _fallback_token_estimate(text)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming_json=True, structured_json=True, state_transfer=False)

    def _build_payload(
        self,
        messages: list[dict[str, str]],
        *,
        stream: bool,
        schema: dict[str, Any] | None,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
            },
        }
        if schema is not None:
            payload["format"] = schema
        return payload

    def _extract_ollama_content(self, data: dict[str, Any]) -> str:
        try:
            content = data["message"]["content"]
        except KeyError as exc:
            raise ProviderResponseError("Ollama response missing message content") from exc
        if not isinstance(content, str):
            raise ProviderResponseError("Ollama response content must be a string")
        return content

    async def _post_json(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(self.base_url, json=payload)
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict):
            raise ProviderResponseError("Ollama response body must be a JSON object")
        return data

    async def _generate_non_streaming(
        self,
        task: str,
        messages: list[dict[str, str]],
        schema: dict[str, Any] | None,
        max_tokens: int,
        temperature: float,
    ) -> str:
        session = LLMObservedSession(model_name=self.model, task=task).start()
        try:
            payload = self._build_payload(
                messages,
                stream=False,
                schema=schema,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            data = await self._post_json(payload)
            content = self._extract_ollama_content(data)
        except (httpx.HTTPError, ValueError, ProviderResponseError):
            session.end(success=False)
            raise

        session.report_first_token()
        session.report_token(self.count_tokens(content))
        session.end(success=True)
        return content

    def _parse_stream_line(self, line: str) -> dict[str, Any] | None:
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Failed to decode Ollama stream chunk: %s", line)
            return None
        if not isinstance(chunk, dict):
            raise ProviderResponseError("Ollama stream chunk must decode to a JSON object")
        return chunk

    async def generate_text(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> str:
        return await self._generate_non_streaming(
            "generate_text",
            messages,
            None,
            max_tokens,
            temperature,
        )

    async def generate_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> str:
        return await self._generate_non_streaming(
            "generate_json",
            messages,
            schema,
            max_tokens,
            temperature,
        )

    async def stream_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        session = LLMObservedSession(model_name=self.model, task="stream_json").start()
        try:
            payload = self._build_payload(
                messages,
                stream=True,
                schema=schema,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream("POST", self.base_url, json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        chunk = self._parse_stream_line(line)
                        if chunk is None:
                            continue
                        if chunk.get("done", False):
                            break
                        content = self._extract_ollama_content(chunk)
                        session.report_first_token()
                        session.report_token(self.count_tokens(content))
                        yield content
            session.end(success=True)
        except (httpx.HTTPError, ValueError, ProviderResponseError):
            session.end(success=False)
            raise


class MockProvider(LLMProvider):
    """A lightweight mock provider that returns canned responses.
    Useful for testing and development when the heavy model files are missing.
    """

    def __init__(self, model_name: str = "mock-model"):
        self.model_name = model_name

    def count_tokens(self, text: str) -> int:
        return _fallback_token_estimate(text)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(streaming_json=True, structured_json=True, state_transfer=False)

    async def generate_text(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> str:
        session = LLMObservedSession(model_name=self.model_name, task="generate_text").start()
        # Simple summary fallback
        if "summar" in str(messages[-1].get("content", "")).lower():
            content = "The journey continues through the digital mists, where reality is but a memory."
        else:
            content = "This is a mock narrative generated because the real model is unavailable. You are in a safe, simulated environment."

        session.report_first_token()
        session.report_token(self.count_tokens(content))
        session.end(success=True)
        return content

    async def generate_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> str:
        session = LLMObservedSession(model_name=self.model_name, task="generate_json").start()

        # Determine which phase we are in based on the schema
        required = schema.get("required", [])

        if "narrative" in required:
            # Narrator phase
            data = {
                "narrative": "You are standing in a digital void. The real model weights were not found, so you are talking to a ghost in the machine. Around you, fragments of data shimmer like stars.",
                "title": "The Ghost in the Machine",
                "npcs_present": ["The Mockingbird"],
                "choices": [
                    {"text": "Search for reality."},
                    {"text": "Accept the simulation."},
                ],
                "is_ending": False,
                "mood": "mysterious",
            }
        elif "stat_updates" in required:
            # Judge / Extraction phase
            data = {
                "items_gained": ["Static Spark"],
                "items_lost": [],
                "stat_updates": {"reputation": 1},
            }
        else:
            # Fallback (StoryNode or unknown) — include legacy strings for test compatibility
            data = {
                "narrative": "You are standing in a digital void. Mock narrative.",
                "title": "Mock Title",
                "items_gained": ["Static Spark"],
                "items_lost": [],
                "npcs_present": ["The Mockingbird"],
                "stat_updates": {"sanity": -1},
                "choices": [{"text": "Choice 1"}, {"text": "Choice 2"}],
                "is_ending": False,
                "mood": "default",
            }

        content = json.dumps(data)
        session.report_first_token()
        session.report_token(self.count_tokens(content))
        session.end(success=True)
        return content

    async def stream_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        full_json = await self.generate_json(messages, schema, max_tokens, temperature)
        # Yield in chunks to simulate streaming
        chunk_size = 20
        for i in range(0, len(full_json), chunk_size):
            yield full_json[i : i + chunk_size]
            import asyncio

            await asyncio.sleep(0.005)
