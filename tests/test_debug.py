from unittest.mock import patch

import pytest

from cyoa.core.models import StoryNode
from cyoa.ui.app import CYOAApp


@pytest.mark.asyncio
async def test_debug_stats():
    from unittest.mock import AsyncMock, MagicMock
    mock_gen = MagicMock()
    mock_gen.token_budget = 2048
    mock_provider = MagicMock()
    mock_provider.count_tokens = MagicMock(return_value=10)
    mock_gen.provider = mock_provider
    mock_gen.generate_next_node_async = AsyncMock(return_value=StoryNode(narrative="Test", choices=[], is_ending=True))
    mock_gen.save_state_async = AsyncMock(return_value=None)

    with patch("cyoa.ui.app.ModelBroker", return_value=mock_gen), patch("cyoa.ui.app.CYOAGraphDB") as mock_db_cls:
        mock_db = mock_db_cls.return_value
        mock_db.verify_connectivity_async = AsyncMock(return_value=True)
        mock_db.create_story_node_and_get_title.return_value = "Test Story"
        mock_db.get_story_tree.return_value = None
        mock_db.save_scene_async = AsyncMock(return_value="sid")
        
        app = CYOAApp(model_path="dummy")
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            if app.engine:
                from cyoa.ui.components import StatusDisplay
                app.query_one(StatusDisplay).health = 0
            await pilot.pause(0.1)
            label = app.query_one("#stats-text")
            print(f"\nDEBUG: RAW RENDER: {repr(label.render())}")
            print(f"\nDEBUG: PLAIN: {repr(label.render().plain)}")
            assert "0%" in label.render().plain
