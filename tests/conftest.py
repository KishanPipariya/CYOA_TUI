import sys
import os
import pytest

# Add the project root to sys.path so tests can import app, models, etc.
# This MUST happen before importing any project-level modules (cyoa.*)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cyoa.core.events import bus

@pytest.fixture(autouse=True)
def reset_event_bus():
    """Clear all global event bus subscribers before each test to ensure isolation."""
    bus.clear()
    yield
    bus.clear()
