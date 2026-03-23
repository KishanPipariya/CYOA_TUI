import pytest
from unittest.mock import MagicMock, patch
from cyoa.ui.app import _adaptive_throttle, CYOAApp
from cyoa.core import constants

def test_adaptive_throttle_logic():
    """Verify throttle values at different story lengths."""
    # Small story (< 2000)
    assert _adaptive_throttle(100) == constants.STREAM_RENDER_THROTTLE_BASE
    
    # Mid-sized story (< 5000)
    assert _adaptive_throttle(3000) == 16
    
    # Large story (< 10000)
    assert _adaptive_throttle(8000) == 32
    
    # Very large story (> 10000)
    assert _adaptive_throttle(15000) == constants.STREAM_RENDER_THROTTLE_MAX

@pytest.mark.asyncio
async def test_stream_narrative_throttle_efficiency():
    """Verify that _stream_narrative doesn't update the UI too frequently on long stories."""
    app = CYOAApp(model_path="dummy.gguf")
    # Mock the Markdown widget update
    mock_md = MagicMock()
    app.query_one = MagicMock(return_value=mock_md)
    app._loading_suffix_shown = False # Already shown first token in this hypothetical
    
    # Set story length so throttle is 32 (from _adaptive_throttle(8000))
    app._current_story = "A" * 8000
    app._stream_token_buffer = 0
    
    # Simulate streaming 10 tokens one by one
    for _ in range(10):
        app._stream_narrative("X")
    
    # With throttle 32, 10 characters should NOT trigger an update yet
    # Subtracting calls made during initialization/loading if any
    # But we mocked the entire query_one and our app state starts fresh
    mock_md.update.assert_not_called()
    
    # Stream another 25 tokens (total 35), should trigger 1 update
    for _ in range(25):
        app._stream_narrative("Y")
        
    assert mock_md.update.call_count == 1
