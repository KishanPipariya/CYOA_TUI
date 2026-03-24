from unittest.mock import patch

import pytest

from cyoa.ui.app import CYOAApp


@pytest.mark.asyncio
async def test_debug_stats():
    with patch("cyoa.ui.app.ModelBroker"), patch("cyoa.ui.app.CYOAGraphDB"):
        app = CYOAApp(model_path="dummy")
        async with app.run_test():
            app.player_stats["health"] = 0
            app._update_status_bar()
            label = app.query_one("#stats-display")
            print(f"\nDEBUG: RAW RENDER: {repr(label.render())}")
            print(f"\nDEBUG: PLAIN: {repr(label.render().plain)}")
            assert "DEAD" in label.render().plain
