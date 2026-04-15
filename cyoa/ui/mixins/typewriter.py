import asyncio
import logging

from textual import work

from cyoa.core import constants, utils
from cyoa.ui.mixins.contracts import as_mixin_host, as_textual_app

logger = logging.getLogger(__name__)

class TypewriterMixin:
    """Mixin for character-by-character text rendering."""

    def _stop_typewriter(self) -> None:
        host = as_mixin_host(self)
        host._is_typing = False
        host._typewriter_active_chunk.clear()

    def _should_stop_typewriter(self) -> bool:
        host = as_mixin_host(self)
        if host.is_runtime_active():
            return False
        self._stop_typewriter()
        return True

    def _refresh_typewriter_ui(self) -> bool:
        host = as_mixin_host(self)
        try:
            host._current_turn_widget.update(host._current_turn_text)
            if host._is_at_bottom():
                host._scroll_to_bottom(animate=False)
        except Exception as e:  # noqa: BLE001
            logger.debug("Typewriter worker UI update failed: %s", e)
            self._stop_typewriter()
            return False
        return True

    async def _drain_typewriter_chunk(self, refresh_limit: float, last_refresh: float) -> float | None:
        host = as_mixin_host(self)
        while host._typewriter_active_chunk:
            if self._should_stop_typewriter():
                return None

            self._handle_typewriter_batch()

            now = asyncio.get_event_loop().time()
            if now - last_refresh >= refresh_limit or not host._typewriter_active_chunk:
                if not self._refresh_typewriter_ui():
                    return None
                last_refresh = now

            if host._typewriter_active_chunk:
                delay = constants.TYPEWRITER_SPEEDS.get(host.typewriter_speed, 0.02)
                if delay > 0:
                    await asyncio.sleep(delay)

        if host._typewriter_queue.empty():
            host._is_typing = False
        return last_refresh

    @work(group="typewriter", exclusive=True)
    async def _typewriter_worker(self) -> None:
        """Background worker that smoothly reveals narrative text from the queue."""
        host = as_mixin_host(self)
        last_refresh = 0.0
        # Throttle Markdown re-renders to ~30fps max to avoid UI lag on long stories
        REFRESH_LIMIT = 0.033

        try:
            while True:
                if self._should_stop_typewriter():
                    return

                # wait for text chunks
                chunk = await host._typewriter_queue.get()
                if self._should_stop_typewriter():
                    return

                host._is_typing = True
                host._typewriter_active_chunk = list(chunk)
                updated_refresh = await self._drain_typewriter_chunk(REFRESH_LIMIT, last_refresh)
                if updated_refresh is None:
                    return
                last_refresh = updated_refresh
        except asyncio.CancelledError:
            self._stop_typewriter()
            raise

    def _handle_typewriter_batch(self) -> None:
        """Process a batch of characters from the active chunk, handling catchup."""
        host = as_mixin_host(self)
        q_size = host._typewriter_queue.qsize()
        batch_size = 1
        if q_size > constants.TYPEWRITER_CATCHUP_THRESHOLD:
            # Extreme catchup: grab everything and exit loops
            to_add = "".join(host._typewriter_active_chunk)
            host._current_story += to_add
            host._current_turn_text += to_add
            host._update_current_story_segment(host._current_turn_text)
            host._typewriter_active_chunk.clear()
            while not host._typewriter_queue.empty():
                to_add = host._typewriter_queue.get_nowait()
                host._current_story += to_add
                host._current_turn_text += to_add
                host._update_current_story_segment(host._current_turn_text)
        elif q_size > 10:
            batch_size = constants.TYPEWRITER_MAX_BATCH

        if host._typewriter_active_chunk:
            to_add = "".join(host._typewriter_active_chunk[:batch_size])
            host._typewriter_active_chunk = host._typewriter_active_chunk[batch_size:]
            host._current_story += to_add
            host._current_turn_text += to_add
            host._update_current_story_segment(host._current_turn_text)

    def action_skip_typewriter(self) -> None:
        """Instantly reveal all pending text in the typewriter queue."""
        host = as_mixin_host(self)
        if not host._is_typing and host._typewriter_queue.empty():
            return

        # Flush active chunk
        if host._typewriter_active_chunk:
            to_add = "".join(host._typewriter_active_chunk)
            host._current_story += to_add
            host._current_turn_text += to_add
            host._update_current_story_segment(host._current_turn_text)
            host._typewriter_active_chunk.clear()

        # Flush queue
        while not host._typewriter_queue.empty():
            try:
                to_add = host._typewriter_queue.get_nowait()
                host._current_story += to_add
                host._current_turn_text += to_add
                host._update_current_story_segment(host._current_turn_text)
            except asyncio.QueueEmpty:
                break
        host._is_typing = False
        try:
            host._current_turn_widget.update(host._current_turn_text)
            host._scroll_to_bottom()
        except Exception as e:
            logger.debug("Failed to update UI after skipping typewriter: %s", e)

    def action_toggle_typewriter(self) -> None:
        """Toggle character-by-character animation and persist choice."""
        app = as_textual_app(self)
        host = as_mixin_host(self)
        host.typewriter_enabled = not host.typewriter_enabled
        status = "Enabled" if host.typewriter_enabled else "Disabled"
        app.notify(f"Typewriter Narrator: {status}")

        # If disabling mid-animation, finish instantly
        if not host.typewriter_enabled:
            host.action_skip_typewriter()

        config = utils.load_config()
        config["typewriter"] = host.typewriter_enabled
        utils.save_config(config)

    def action_cycle_typewriter_speed(self) -> None:
        """Cycle through narrator speeds (slow, normal, fast, instant)."""
        app = as_textual_app(self)
        host = as_mixin_host(self)
        speeds = list(constants.TYPEWRITER_SPEEDS.keys())
        current_idx = speeds.index(host.typewriter_speed)
        new_speed = speeds[(current_idx + 1) % len(speeds)]
        host.typewriter_speed = new_speed
        app.notify(f"Typewriter Speed: {new_speed.capitalize()}")

        config = utils.load_config()
        config["typewriter_speed"] = host.typewriter_speed
        utils.save_config(config)
