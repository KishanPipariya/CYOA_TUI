from unittest.mock import patch

import pytest

from cyoa.core.models import StoryNode
from cyoa.ui.app import CYOAApp


@pytest.mark.asyncio
async def test_debug_stats():
    from unittest.mock import MagicMock, AsyncMock
    mock_gen = MagicMock()
    mock_gen.token_budget = 2048
    mock_provider = MagicMock()
    mock_provider.count_tokens = MagicMock(return_value=10)
    mock_gen.provider = mock_provider
    mock_gen.generate_next_node_async = AsyncMock(return_value=StoryNode(narrative="Test", choices=[], is_ending=True))
    mock_gen.save_state_async = AsyncMock(return_value=None)

    with patch("cyoa.ui.app.ModelBroker", return_value=mock_gen), patch("cyoa.ui.app.CYOAGraphDB"):
        app = CYOAApp(model_path="dummy")
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            if app.engine:
                app.engine.player_stats["health"] = 0
            app._update_status_bar()
            label = app.query_one("#stats-display")
            print(f"\nDEBUG: RAW RENDER: {repr(label.render())}")
            print(f"\nDEBUG: PLAIN: {repr(label.render().plain)}")
            assert "DEAD" in label.render().plain
