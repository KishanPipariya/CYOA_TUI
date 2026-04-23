import logging
from collections.abc import Callable
from typing import Any

from cyoa.core.events import Events, bus
from cyoa.core.models import StoryNode
from cyoa.core.support import open_private_text_file

logger = logging.getLogger(__name__)


class StoryLogger:
    """
    Independent system responsible for maintaining the local 'story.md' persistent log.
    Listens to the EventBus instead of being hardcoded into the App.
    """

    def __init__(self, filepath: str = "story.md") -> None:
        self.filepath = filepath
        self._file_handle: Any | None = None
        self._unsubscribers: list[Callable[[], None]] = []

        # Subscribe to the current engine event contract.
        self._unsubscribers.append(
            bus.subscribe(Events.STORY_TITLE_GENERATED, self.on_story_title_generated)
        )
        self._unsubscribers.append(bus.subscribe(Events.CHOICE_MADE, self.on_choice_made))
        self._unsubscribers.append(bus.subscribe(Events.NODE_COMPLETED, self.on_node_completed))

    def start_new_log(self, title: str) -> None:
        """Initialize or clear the story log file."""
        if self._file_handle:
            self._file_handle.close()

        with open_private_text_file(self.filepath, "w") as f:
            f.write(f"# {title}\n\n")

        self._file_handle = open_private_text_file(self.filepath, "a")

    def write_append(self, content: str) -> None:
        if self._file_handle:
            self._file_handle.write(content)
            self._file_handle.flush()

    def on_story_title_generated(self, **kwargs: Any) -> None:
        title = kwargs.get("title", "Untitled Adventure")
        self.start_new_log(title)

    def on_choice_made(self, **kwargs: Any) -> None:
        choice_text = kwargs.get("choice_text")
        if choice_text:
            self.write_append(f"> **You chose:** {choice_text}\n\n---\n\n")

    def on_node_completed(self, **kwargs: Any) -> None:
        node = kwargs.get("node")
        if isinstance(node, StoryNode):
            self.write_append(f"{node.narrative}\n\n")

    def close(self) -> None:
        if self._file_handle:
            self._file_handle.close()
            self._file_handle = None

        # Clean up EventBus subscriptions to prevent memory leaks
        for unsub in self._unsubscribers:
            unsub()
        self._unsubscribers.clear()
