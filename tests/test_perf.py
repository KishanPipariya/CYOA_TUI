import pytest

from cyoa.ui.app import CYOAApp


@pytest.mark.asyncio
async def test_stream_narrative_fills_queue():
    """Verify that _stream_narrative populates the typewriter queue correctly."""
    app = CYOAApp(model_path="dummy.gguf")
    app._loading_suffix_shown = False

    # Simulate streaming 10 tokens one by one
    for i in range(10):
        app._stream_narrative(f"char-{i}")

    assert app._typewriter_queue.qsize() == 10
    assert app._typewriter_queue.get_nowait() == "char-0"
