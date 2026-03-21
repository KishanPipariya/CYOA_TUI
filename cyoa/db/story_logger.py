import logging
from typing import Optional, Any
from cyoa.core.events import bus

logger = logging.getLogger(__name__)

class StoryLogger:
    """
    Independent system responsible for maintaining the local 'story.md' persistent log.
    Listens to the EventBus instead of being hardcoded into the App.
    """
    
    def __init__(self, filepath: str = "story.md") -> None:
        self.filepath = filepath
        self._file_handle: Optional[Any] = None
        
        # Subscribe to events
        bus.subscribe("story_started", self.on_story_started)
        bus.subscribe("choice_made", self.on_choice_made)
        bus.subscribe("scene_generated", self.on_scene_generated)
        
    def start_new_log(self, title: str) -> None:
        """Initialize or clear the story log file."""
        if self._file_handle:
            self._file_handle.close()
            
        with open(self.filepath, "w", encoding="utf-8") as f:
            f.write(f"# {title}\n\n")
            
        self._file_handle = open(self.filepath, "a", encoding="utf-8")
        
    def write_append(self, content: str) -> None:
        if self._file_handle:
            self._file_handle.write(content)
            self._file_handle.flush()

    def on_story_started(self, **kwargs: Any) -> None:
        title = kwargs.get("title", "Untitled Adventure")
        self.start_new_log(title)
        
    def on_choice_made(self, **kwargs: Any) -> None:
        choice_text = kwargs.get("choice_text")
        if choice_text:
            self.write_append(f"> **You chose:** {choice_text}\n\n---\n\n")
            
    def on_scene_generated(self, **kwargs: Any) -> None:
        # Currently handled by the UI since we don't dump raw scene texts redundantly, 
        # but the skeleton is here if we want to log it in the future.
        pass
        
    def close(self) -> None:
        if self._file_handle:
            self._file_handle.close()
            self._file_handle = None
