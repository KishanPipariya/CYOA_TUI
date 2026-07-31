import asyncio
import threading
import time
from unittest.mock import patch

import pytest

np = pytest.importorskip("numpy")
providers = pytest.importorskip("cyoa.llm.providers")
LlamaCppProvider = providers.LlamaCppProvider
_InterruptionLogitsProcessor = providers._InterruptionLogitsProcessor

# ── Logits Processor Tests ───────────────────────────────────────────────────


def test_interruption_logits_processor():
    """Verify that the logits processor correctly forces EOS when signaled."""
    cancel_event = threading.Event()
    eos_token_id = 2
    processor = _InterruptionLogitsProcessor(cancel_event, eos_token_id)

    # Case 1: No interruption
    scores = np.array([10.0, 5.0, 1.0, 8.0], dtype=np.float32)
    original_scores = scores.copy()
    result = processor(None, scores)
    assert np.array_equal(result, original_scores), (
        "Scores should be unchanged when event is not set."
    )

    # Case 2: Interrupted
    cancel_event.set()
    scores = np.array([10.0, 5.0, 1.0, 8.0], dtype=np.float32)
    result = processor(None, scores)

    assert result[eos_token_id] == 0.0, (
        "EOS token should have maximum relative probability (0.0 logit)."
    )
    assert result[0] == -np.inf, "Other tokens should be suppressed to -inf."
    assert result[1] == -np.inf
    assert result[3] == -np.inf


# ── Integration Tests ────────────────────────────────────────────────────────


@pytest.fixture
def mock_llama():
    with patch("cyoa.llm.providers.Llama") as mock:
        instance = mock.return_value
        instance.token_eos.return_value = 2
        instance.tokenize.return_value = [1, 2, 3]

        # Default behavior: immediate return
        instance.create_chat_completion.return_value = [
            {"choices": [{"delta": {"content": "test"}}]}
        ]
        yield instance


@pytest.mark.asyncio
async def test_llama_cpp_interruption_signal_flow(mock_llama):
    """Verify that canceling a stream task correctly triggers the interruption flag."""
    provider = LlamaCppProvider(model_path="dummy.gguf")
    messages = [{"role": "user", "content": "hi"}]
    first_token_emitted = threading.Event()

    # Define a generator that simulates slow C++ work
    def slow_gen(*args, **kwargs):
        first_token_emitted.set()
        yield {"choices": [{"delta": {"content": "first"}}]}
        # In a real scenario, this is where the C++ thread would be stuck
        # before checking the logits processor for the next token.
        import time

        for _ in range(50):  # Wait up to 5 seconds
            time.sleep(0.1)
            yield {"choices": [{"delta": {"content": "..."}}]}

    mock_llama.create_chat_completion.side_effect = slow_gen

    # Start the stream in a task
    async def consume():
        async for _ in provider.stream_json(messages, {}):
            pass

    task = asyncio.create_task(consume())

    assert await asyncio.to_thread(first_token_emitted.wait, 1.0)

    # Cancel the consumer task
    task.cancel()

    try:
        await task
    except asyncio.CancelledError:
        pass

    # Verification:
    # 1. create_chat_completion was called with the processor
    args, kwargs = mock_llama.create_chat_completion.call_args
    assert "logits_processor" in kwargs
    processor = kwargs["logits_processor"][0]
    assert isinstance(processor, _InterruptionLogitsProcessor)

    # 2. The cancellation event shared with the processor MUST be set
    # because the async generator's 'finally' block ran.
    assert processor.cancel_event.is_set(), (
        "The cancellation event should be signaled to the C++ thread."
    )


@pytest.mark.asyncio
async def test_speculative_interruption_resets_lock(mock_llama):
    """Ensure that an interrupted generation actually releases the lock."""
    provider = LlamaCppProvider(model_path="dummy.gguf")

    # This test is harder to time perfectly, but we can verify that the
    # producer thread exits its 'with self._lock' block when the event is set.

    producer_blocked = threading.Event()

    def lock_tracking_gen(*args, **kwargs):
        yield {"choices": [{"delta": {"content": "start"}}]}
        processor = kwargs["logits_processor"][0]
        producer_blocked.set()
        while not processor.cancel_event.is_set():
            time.sleep(0.01)
        yield {"choices": [{"delta": {"content": "stopped"}}]}

    mock_llama.create_chat_completion.side_effect = lock_tracking_gen

    async def run_gen():
        try:
            async for _ in provider.stream_json([], {}):
                pass
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(run_gen())
    assert await asyncio.to_thread(producer_blocked.wait, 1.0)

    # At this point, the producer thread is inside the lock and stuck in the generator.
    assert not provider._lock.acquire(blocking=False)

    # Cancel the task. This triggers the 'finally' which sets the cancel_event.
    task.cancel()

    # Get the processor to signal our mock generator
    args, kwargs = mock_llama.create_chat_completion.call_args
    assert "logits_processor" in kwargs
    processor = kwargs["logits_processor"][0]

    assert await asyncio.to_thread(processor.cancel_event.wait, 1.0)
    assert await asyncio.to_thread(provider._lock.acquire, True, 1.0)
    provider._lock.release()
    await asyncio.wait_for(task, timeout=1.0)
