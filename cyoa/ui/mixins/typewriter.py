import asyncio
import logging
from textual import work
from textual.app import App
from cyoa.core import constants, utils

logger = logging.getLogger(__name__)

class TypewriterMixin:
    """Mixin for character-by-character text rendering."""

    @work(group="typewriter", exclusive=True)
    async def _typewriter_worker(self) -> None:
        """Background worker that smoothly reveals narrative text from the queue."""
        assert isinstance(self, App)
        last_refresh = 0.0
        # Throttle Markdown re-renders to ~30fps max to avoid UI lag on long stories
        REFRESH_LIMIT = 0.033

        while True:
            # wait for text chunks
            chunk = await self._typewriter_queue.get()
            self._is_typing = True
            self._typewriter_active_chunk = list(chunk)

            while self._typewriter_active_chunk:
                self._handle_typewriter_batch()

                # Throttled UI update
                now = asyncio.get_event_loop().time()
                if now - last_refresh >= REFRESH_LIMIT or not self._typewriter_active_chunk:
                    if hasattr(self, "_current_turn_widget"):
                        self._current_turn_widget.update(self._current_turn_text)
                    if self._is_at_bottom():
                        self._scroll_to_bottom(animate=False)
                    last_refresh = now

                if self._typewriter_active_chunk:
                    delay = constants.TYPEWRITER_SPEEDS.get(self.typewriter_speed, 0.02)
                    if delay > 0:
                        await asyncio.sleep(delay)

            if self._typewriter_queue.empty():
                self._is_typing = False

    def _handle_typewriter_batch(self) -> None:
        """Process a batch of characters from the active chunk, handling catchup."""
        q_size = self._typewriter_queue.qsize()
        batch_size = 1
        if q_size > constants.TYPEWRITER_CATCHUP_THRESHOLD:
            # Extreme catchup: grab everything and exit loops
            to_add = "".join(self._typewriter_active_chunk)
            self._current_story += to_add
            self._current_turn_text += to_add
            self._typewriter_active_chunk.clear()
            while not self._typewriter_queue.empty():
                to_add = self._typewriter_queue.get_nowait()
                self._current_story += to_add
                self._current_turn_text += to_add
        elif q_size > 10:
            batch_size = constants.TYPEWRITER_MAX_BATCH

        if self._typewriter_active_chunk:
            to_add = "".join(self._typewriter_active_chunk[:batch_size])
            self._typewriter_active_chunk = self._typewriter_active_chunk[batch_size:]
            self._current_story += to_add
            self._current_turn_text += to_add

    def action_skip_typewriter(self) -> None:
        """Instantly reveal all pending text in the typewriter queue."""
        if not self._is_typing and self._typewriter_queue.empty():
            return

        # Flush active chunk
        if hasattr(self, "_typewriter_active_chunk") and self._typewriter_active_chunk:
            to_add = "".join(self._typewriter_active_chunk)
            self._current_story += to_add
            self._current_turn_text += to_add
            self._typewriter_active_chunk.clear()

        # Flush queue
        while not self._typewriter_queue.empty():
            try:
                to_add = self._typewriter_queue.get_nowait()
                self._current_story += to_add
                self._current_turn_text += to_add
            except asyncio.QueueEmpty:
                break
        self._is_typing = False
        try:
            if hasattr(self, "_current_turn_widget"):
                self._current_turn_widget.update(self._current_turn_text)
            self._scroll_to_bottom()
        except Exception as e:
            logger.debug("Failed to update UI after skipping typewriter: %s", e)

    def action_toggle_typewriter(self) -> None:
        """Toggle character-by-character animation and persist choice."""
        assert isinstance(self, App)
        self.typewriter_enabled = not self.typewriter_enabled
        status = "Enabled" if self.typewriter_enabled else "Disabled"
        self.notify(f"Typewriter Narrator: {status}")

        # If disabling mid-animation, finish instantly
        if not self.typewriter_enabled:
            self.action_skip_typewriter()

        config = utils.load_config()
        config["typewriter"] = self.typewriter_enabled
        utils.save_config(config)

    def action_cycle_typewriter_speed(self) -> None:
        """Cycle through narrator speeds (slow, normal, fast, instant)."""
        assert isinstance(self, App)
        speeds = list(constants.TYPEWRITER_SPEEDS.keys())
        current_idx = speeds.index(self.typewriter_speed)
        new_speed = speeds[(current_idx + 1) % len(speeds)]
        self.typewriter_speed = new_speed
        self.notify(f"Typewriter Speed: {new_speed.capitalize()}")

        config = utils.load_config()
        config["typewriter_speed"] = self.typewriter_speed
        utils.save_config(config)
