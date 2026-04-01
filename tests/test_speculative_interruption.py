import asyncio
import threading
import time
from unittest.mock import patch

import numpy as np
import pytest

from cyoa.llm.providers import LlamaCppProvider, _InterruptionLogitsProcessor

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
    assert np.array_equal(result, original_scores), "Scores should be unchanged when event is not set."

    # Case 2: Interrupted
    cancel_event.set()
    scores = np.array([10.0, 5.0, 1.0, 8.0], dtype=np.float32)
    result = processor(None, scores)

    assert result[eos_token_id] == 0.0, "EOS token should have maximum relative probability (0.0 logit)."
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

    # Define a generator that simulates slow C++ work
    def slow_gen():
        yield {"choices": [{"delta": {"content": "first"}}]}
        # In a real scenario, this is where the C++ thread would be stuck
        # before checking the logits processor for the next token.
        import time
        for _ in range(50): # Wait up to 5 seconds
            time.sleep(0.1)
            yield {"choices": [{"delta": {"content": "..."}}]}

    mock_llama.create_chat_completion.side_effect = slow_gen

    # Start the stream in a task
    async def consume():
        async for _ in provider.stream_json(messages, {}):
            pass

    task = asyncio.create_task(consume())

    # Wait for the first token to be processed
    await asyncio.sleep(0.2)

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
    assert processor.cancel_event.is_set(), "The cancellation event should be signaled to the C++ thread."


@pytest.mark.asyncio
async def test_speculative_interruption_resets_lock(mock_llama):
    """Ensure that an interrupted generation actually releases the lock."""
    provider = LlamaCppProvider(model_path="dummy.gguf")

    # This test is harder to time perfectly, but we can verify that the
    # producer thread exits its 'with self._lock' block when the event is set.

    ev = threading.Event()
    lock_released = threading.Event()

    def lock_tracking_gen(*args, **kwargs):
        yield {"choices": [{"delta": {"content": "start"}}]}
        # Wait until we are told to stop or timeout to avoid infinite test hang
        start = time.time()
        while not ev.is_set() and (time.time() - start < 2.0):
            time.sleep(0.01)
        yield {"choices": [{"delta": {"content": "stopped"}}]}

    mock_llama.create_chat_completion.side_effect = lock_tracking_gen

    async def run_gen():
        try:
            async for _ in provider.stream_json([], {}):
                pass
        except asyncio.CancelledError:
            pass
        # Once the generator finishes/is cancelled, the provider lock is released
        lock_released.set()

    task = asyncio.create_task(run_gen())
    # Give the producer thread more time to acquire the lock AND start generating
    await asyncio.sleep(0.3)

    # At this point, the producer thread is inside the lock and stuck in the generator.
    assert not lock_released.is_set(), "Lock should still be held while C++ inference is running."

    # Cancel the task. This triggers the 'finally' which sets the cancel_event.
    task.cancel()

    # Give the cancellation signal time to propagate through the queue finally block
    await asyncio.sleep(0.1)

    # Get the processor to signal our mock generator
    args, kwargs = mock_llama.create_chat_completion.call_args
    assert "logits_processor" in kwargs
    processor = kwargs["logits_processor"][0]

    # In a real model, the logits processor would stop the generator.
    # Here we simulate that by manually setting our control event.
    if processor.cancel_event.is_set():
        ev.set()

    # The run_gen task should now finish and release the lock because the producer exited.
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except (TimeoutError, asyncio.CancelledError):
        pass

    # Wait for the producer thread's release logic to complete
    await asyncio.sleep(0.1)
    assert lock_released.is_set(), "The provider lock should be released after interruption."
