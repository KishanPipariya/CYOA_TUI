import logging
from uuid import uuid4
from textual.app import App
from textual.widgets import Markdown, Static, Button
from textual.containers import Container
from cyoa.core import constants
from cyoa.core.models import StoryNode
from cyoa.ui.ascii_art import SCENE_ART
from cyoa.ui.components import StatusDisplay

logger = logging.getLogger(__name__)

def _detect_scene_art(narrative: str) -> str | None:
    """Return ASCII art matching keywords found in the narrative, or None."""
    lower = narrative.lower()
    for scene_key, keywords in constants.SCENE_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return SCENE_ART.get(scene_key)
    return None

class RenderingMixin:
    """Mixin for story narrative rendering and UI updates."""

    def _stream_narrative(self, partial: str) -> None:
        """Streaming callback: feeds the typewriter queue or updates UI immediately."""
        assert isinstance(self, App)
        if self._loading_suffix_shown:
            # First token batch arrived — strip the loading placeholder
            suffix = "\n\n*(The ancient texts are shifting...)*"
            if self._current_story.endswith(suffix):
                self._current_story = self._current_story[: -len(suffix)]
            if self._current_turn_text.endswith(suffix):
                self._current_turn_text = self._current_turn_text[: -len(suffix)]
            self._loading_suffix_shown = False
            self.query_one("#loading").add_class("hidden")

            if self._current_story == constants.LOADING_ART:
                self._current_story = ""
                self._current_turn_text = ""

        if not self.typewriter_enabled:
            self._current_story += partial
            self._current_turn_text += partial
            if hasattr(self, "_current_turn_widget"):
                self._current_turn_widget.update(self._current_turn_text)
            if self._is_at_bottom():
                self._scroll_to_bottom(animate=False)
        else:
            self._typewriter_queue.put_nowait(partial)

    def show_loading(self, selected_button_id: str | None = None) -> None:
        """Clear choice buttons, show spinner, append 'shifting' text."""
        assert isinstance(self, App)
        choices_container = self.query_one("#choices-container")
        if selected_button_id is not None:
            # Keep only the selected button, disable and dim it
            for btn in list(choices_container.query(Button)):
                if btn.id != selected_button_id:
                    btn.remove()
                else:
                    btn.disabled = True
                    btn.variant = "default"
        else:
            choices_container.remove_children()
        self.query_one("#loading").remove_class("hidden")

        if not self._loading_suffix_shown:
            suffix = "\n\n*(The ancient texts are shifting...)*"
            self._current_story += suffix
            self._current_turn_text += suffix
            self._loading_suffix_shown = True
            if hasattr(self, "_current_turn_widget"):
                self._current_turn_widget.update(self._current_turn_text)
            self._scroll_to_bottom()

    def _is_at_bottom(self) -> bool:
        """Return True if the story container is near its bottom edge."""
        assert isinstance(self, App)
        try:
            container = self.query_one("#story-container")
            return container.scroll_y >= container.max_scroll_y - 8
        except Exception as e:
            logger.debug("Failed to check if at bottom: %s", e)
            return True

    def _scroll_to_bottom(self, animate: bool = True) -> None:
        """Scroll the story container to the end after the next refresh."""
        assert isinstance(self, App)
        try:
            container = self.query_one("#story-container")
            self.call_after_refresh(lambda: container.scroll_end(animate=animate))
        except Exception as e:
            logger.debug("Failed to scroll to bottom: %s", e)

    def display_node(self, node: StoryNode) -> None:
        """Render a newly generated StoryNode to the UI (after streaming completes)."""
        assert isinstance(self, App)
        self.query_one("#loading").add_class("hidden")
        self.mood = getattr(node, "mood", "default")

        is_error = node.narrative.startswith(constants.ERROR_NARRATIVE_PREFIX)

        # 1. Update ASCII art
        self._update_scene_art(node.narrative, is_error)

        # 2. Sync narrative text
        self._sync_narrative(node.narrative)

        # 3. Add error message if necessary
        if is_error and "⚠️" not in node.narrative:
            error_msg = "\n\n> ⚠️ **An error occurred.** The story engine could not generate a valid response."
            self._current_story += error_msg
            self._current_turn_text += error_msg

        # 4. Update the widget
        try:
            if hasattr(self, "_current_turn_widget"):
                self._current_turn_widget.update(self._current_turn_text)
        except Exception as e:
            logger.debug("Failed to update current turn widget: %s", e)

        # 5. Update UI stats from engine state
        self._update_ui_stats()

        # 6. Mount choices
        choices_container = self.query_one("#choices-container", Container)
        choices_container.remove_children()
        self._mount_choice_buttons(node, choices_container, is_error)

        # 7. Trigger speculation
        if hasattr(self, "speculate_all_choices"):
             self.speculate_all_choices(node)

        # 8. Scroll
        self._scroll_to_bottom()

    def _update_scene_art(self, narrative: str, is_error: bool) -> None:
        """Detect and update the separate ASCII art widget."""
        assert isinstance(self, App)
        art = _detect_scene_art(narrative) if not is_error else None
        art_widget = self.query_one("#scene-art", Static)
        if art:
            art_widget.update(art)
            art_widget.remove_class("hidden")
        else:
            art_widget.update("")
            art_widget.add_class("hidden")

    def _sync_narrative(self, narrative: str) -> None:
        """Synchronize the narrative text, handling fallback/cache hit vs streaming."""
        if self._loading_suffix_shown:
            self._loading_suffix_shown = False
            suffix = "\n\n*(The ancient texts are shifting...)*"
            if self._current_story.endswith(suffix):
                self._current_story = self._current_story[: -len(suffix)]
            if self._current_turn_text.endswith(suffix):
                self._current_turn_text = self._current_turn_text[: -len(suffix)]

            if self._current_story == constants.LOADING_ART:
                self._current_story = ""
                self._current_turn_text = ""

            if not self.typewriter_enabled:
                self._current_story += narrative
                self._current_turn_text += narrative
            else:
                self._typewriter_queue.put_nowait(narrative)
        else:
            # Streaming happened. Sync to the finalized narrative.
            self.action_skip_typewriter()

            last_sep = self._current_story.rfind("\n\n---\n\n")
            if last_sep != -1:
                prefix = self._current_story[: last_sep + len("\n\n---\n\n")]
                self._current_story = prefix + narrative
            else:
                self._current_story = narrative

            self._current_turn_text = narrative

    def _update_ui_stats(self) -> None:
        """Update UI stats from engine state."""
        assert isinstance(self, App)
        if self.engine:
            try:
                status = self.query_one(StatusDisplay)
                status.health = self.engine.state.player_stats.get("health", 100)
                status.gold = self.engine.state.player_stats.get("gold", 0)
                status.reputation = self.engine.state.player_stats.get("reputation", 0)
                status.inventory = list(self.engine.state.inventory)
            except Exception as e:
                logger.debug("Failed to update status display from engine: %s", e)

    def _mount_choice_buttons(
        self, node: StoryNode, choices_container: Container, is_error: bool
    ) -> None:
        """Mount choice buttons based on the node state."""
        # Error UX: show a Retry button alongside the fallback choice
        if is_error:
            choices_container.mount(
                Button("🔄 Retry Generation", id="btn-retry", variant="warning")
            )
            for i, choice in enumerate(node.choices):
                # Unique ID per mount to avoid collisions if previous buttons haven't fully unmounted
                btn_id = f"choice-t{self.turn_count}-{uuid4().hex[:6]}-{i}"
                btn = Button(f"[b]{i + 1}[/b]  {choice.text}", id=btn_id, variant="default")
                choices_container.mount(btn)
        elif node.is_ending:
            end_btn = Button("✦ Start a New Adventure", id="btn-new-adventure", variant="success")
            choices_container.mount(end_btn)
        else:
            for i, choice in enumerate(node.choices):
                # Unique ID per mount to avoid collisions if previous buttons haven't fully unmounted
                btn_id = f"choice-t{self.turn_count}-{uuid4().hex[:6]}-{i}"
                btn = Button(f"[b]{i + 1}[/b]  {choice.text}", id=btn_id, variant="primary")
                choices_container.mount(btn)

        self._focus_first_choice_button(choices_container)

    def _focus_first_choice_button(self, choices_container: Container) -> None:
        """Focus first available choice button for faster keyboard play."""
        assert isinstance(self, App)
        buttons = [btn for btn in choices_container.query(Button) if not btn.disabled]
        if not buttons:
            return
        self.call_after_refresh(buttons[0].focus)

    async def _trigger_choice(self, choice_idx: int, selected_button_id: str | None = None) -> None:
        """Handle choice selection and delegate to the engine."""
        assert isinstance(self, App)
        if (
            not self.engine
            or not self.engine.state.current_node
            or choice_idx >= len(self.engine.state.current_node.choices)
        ):
            return

        choice = self.engine.state.current_node.choices[choice_idx]
        choice_text = choice.text

        # 1. Instant UI feedback
        self.action_skip_typewriter()
        self._current_story += f"\n\n> **You chose:** {choice_text}"
        self._current_story += "\n\n---\n\n"

        container = self.query_one("#story-container")
        choice_md = Markdown(f"**You chose:** {choice_text}", classes="player-choice")
        container.mount(choice_md, before="#scene-art")

        new_turn = Markdown("", classes="story-turn")
        container.mount(new_turn, before="#scene-art")
        self._current_turn_widget = new_turn
        self._current_turn_text = ""

        self.show_loading(selected_button_id=selected_button_id)

        # 2. Journal update
        from textual.widgets import ListView, ListItem, Label
        journal_list = self.query_one("#journal-list", ListView)
        narrative_preview = self.engine.state.current_node.narrative[:60].replace("\n", " ").strip()
        if len(self.engine.state.current_node.narrative) > 60:
            narrative_preview += "…"
        journal_entry = f"Turn {self.engine.state.turn_count}: {choice_text} → {narrative_preview}"
        journal_list.append(ListItem(Label(journal_entry)))
        # U2 Fix: Scroll after refresh to ensure layout size is updated
        self.call_after_refresh(lambda: journal_list.scroll_end(animate=False))

        # 3. Cancel speculations and let the engine handle the rest
        self.workers.cancel_group(self, "speculation")
        await self.engine.make_choice(choice_text)
