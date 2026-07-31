import asyncio
import os
import sys
from contextlib import suppress

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


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Close a loop left in the worker's policy after Textual test teardown.

    Textual's headless test driver can register a selector loop in a pytest-xdist
    worker even after pytest-asyncio has finished its function-scoped loops.
    An unclosed selector loop retains its self-pipe sockets until garbage
    collection, which produces shutdown ResourceWarnings on Python 3.13.
    """
    del session, exitstatus
    policy = asyncio.get_event_loop_policy()
    # Avoid get_event_loop(): Python 3.13 warns when no loop is registered.
    loop = getattr(getattr(policy, "_local", None), "_loop", None)
    if loop is not None and not loop.is_running() and not loop.is_closed():
        loop.close()
    with suppress(RuntimeError):
        policy.set_event_loop(None)
