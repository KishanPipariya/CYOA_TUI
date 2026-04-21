import logging
from typing import Any
from uuid import uuid4

from textual.containers import Container, VerticalScroll
from textual.widgets import Button, Markdown, Static

from cyoa.core import constants
from cyoa.core.models import StoryNode
from cyoa.ui.ascii_art import SCENE_ART
from cyoa.ui.components import StatusDisplay
from cyoa.ui.mixins.contracts import as_mixin_host, as_textual_app
from cyoa.ui.presenters import build_choice_label

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
        app = as_textual_app(self)
        host = as_mixin_host(self)
        if not host.is_runtime_active():
            return
        if host._loading_suffix_shown:
            # First token batch arrived — loading state is visual-only (spinner).
            host._loading_suffix_shown = False
            app.query_one("#loading", Static).add_class("hidden")
            app.query_one("#story-container", VerticalScroll).remove_class("loading-state")

            if host._current_story == constants.LOADING_ART:
                host._current_story = ""
                host._current_turn_text = ""
                host._reset_story_segments("")

        if not host.typewriter_enabled:
            host._current_story += partial
            host._current_turn_text += partial
            host._update_current_story_segment(host._current_turn_text)
            host._current_turn_widget.update(host._current_turn_text)
            if host._is_at_bottom():
                host._scroll_to_bottom(animate=False)
        else:
            host._typewriter_queue.put_nowait(partial)

    def show_loading(self, selected_button_id: str | None = None) -> None:
        """Clear choice buttons and show spinner while generation is in progress."""
        app = as_textual_app(self)
        host = as_mixin_host(self)
        if not host.is_runtime_active():
            return
        choices_container = app.query_one("#choices-container", Container)
        app.query_one("#story-container", VerticalScroll).add_class("loading-state")
        if selected_button_id is not None:
            choices_container.remove_class("loading-state")
            # Keep only the selected button, disable and dim it
            for btn in list(choices_container.query(Button)):
                if btn.id != selected_button_id:
                    btn.remove()
                else:
                    btn.disabled = True
                    btn.variant = "default"
        else:
            choices_container.remove_children()
            choices_container.add_class("loading-state")
        app.query_one("#loading", Static).remove_class("hidden")

        host._loading_suffix_shown = True

    def _is_at_bottom(self) -> bool:
        """Return True if the story container is near its bottom edge."""
        app = as_textual_app(self)
        try:
            container = app.query_one("#story-container", VerticalScroll)
            return bool(container.scroll_y >= container.max_scroll_y - 8)
        except Exception as e:
            logger.debug("Failed to check if at bottom: %s", e)
            return True

    def _scroll_to_bottom(self, animate: bool = True) -> None:
        """Scroll the story container to the end after the next refresh."""
        app = as_textual_app(self)
        try:
            container = app.query_one("#story-container", VerticalScroll)
            app.call_after_refresh(lambda: container.scroll_end(animate=animate))
        except Exception as e:
            logger.debug("Failed to scroll to bottom: %s", e)

    def display_node(self, node: StoryNode) -> None:
        """Render a newly generated StoryNode to the UI (after streaming completes)."""
        app = as_textual_app(self)
        host = as_mixin_host(self)
        if not host.is_runtime_active():
            return
        app.query_one("#loading", Static).add_class("hidden")
        app.query_one("#story-container", VerticalScroll).remove_class("loading-state")
        host.mood = getattr(node, "mood", "default")

        is_error = node.narrative.startswith(constants.ERROR_NARRATIVE_PREFIX)

        # 1. Update ASCII art
        self._update_scene_art(node.narrative, is_error)

        # 2. Sync narrative text
        self._sync_narrative(node.narrative)

        # 3. Add error message if necessary
        if is_error and "⚠️" not in node.narrative:
            error_msg = "\n\n> ⚠️ **An error occurred.** The story engine could not generate a valid response."
            host._current_story += error_msg
            host._current_turn_text += error_msg
            host._update_current_story_segment(host._current_turn_text)

        # 4. Update the widget
        try:
            host._current_turn_widget.update(host._current_turn_text)
        except Exception as e:
            logger.debug("Failed to update current turn widget: %s", e)

        # 5. Update UI stats from engine state
        self._update_ui_stats()

        # 6. Mount choices
        choices_container = app.query_one("#choices-container", Container)
        choices_container.remove_class("loading-state")
        choices_container.remove_children()
        self._mount_choice_buttons(node, choices_container, is_error)
        host.apply_ui_theme()

        # 7. Trigger speculation
        host.speculate_all_choices(node)

        # 8. Scroll
        self._scroll_to_bottom()

    def _update_scene_art(self, narrative: str, is_error: bool) -> None:
        """Detect and update the separate ASCII art widget."""
        app = as_textual_app(self)
        art = _detect_scene_art(narrative) if not is_error else None
        art_widget = app.query_one("#scene-art", Static)
        if art:
            art_widget.update(art)
            art_widget.remove_class("hidden")
        else:
            art_widget.update("")
            art_widget.add_class("hidden")

    def _sync_narrative(self, narrative: str) -> None:
        """Synchronize the narrative text, handling fallback/cache hit vs streaming."""
        host = as_mixin_host(self)
        if host._loading_suffix_shown:
            host._loading_suffix_shown = False

            if host._current_story == constants.LOADING_ART:
                host._current_story = ""
                host._current_turn_text = ""
                host._reset_story_segments("")

            if not host.typewriter_enabled:
                host._current_story += narrative
                host._current_turn_text += narrative
                host._update_current_story_segment(host._current_turn_text)
            else:
                host._typewriter_queue.put_nowait(narrative)
        else:
            # Streaming happened. Sync to the finalized narrative.
            host.action_skip_typewriter()

            last_sep = host._current_story.rfind("\n\n---\n\n")
            if last_sep != -1:
                prefix = host._current_story[: last_sep + len("\n\n---\n\n")]
                host._current_story = prefix + narrative
            else:
                host._current_story = narrative

            host._current_turn_text = narrative
            host._update_current_story_segment(host._current_turn_text)

    def _update_ui_stats(self) -> None:
        """Update UI stats from engine state."""
        app = as_textual_app(self)
        host = as_mixin_host(self)
        if not host.engine or not host.is_runtime_active():
            return
        status = app.query_one(StatusDisplay)
        status.health = host.engine.state.player_stats.get("health", 100)
        status.gold = host.engine.state.player_stats.get("gold", 0)
        status.reputation = host.engine.state.player_stats.get("reputation", 0)
        status.inventory = list(host.engine.state.inventory)
        status.objectives = [
            objective.text
            for objective in host.engine.state.objectives
            if objective.status == "active"
        ]

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
                btn_id = f"choice-t{as_mixin_host(self).turn_count}-{uuid4().hex[:6]}-{i}"
                btn = Button(build_choice_label(i, choice.text), id=btn_id, variant="default")
                btn.add_class("choice-card")
                btn.add_class("choice-card-error")
                choices_container.mount(btn)
        elif node.is_ending:
            end_btn = Button(
                "✦ Start a New Adventure",
                id="btn-new-adventure",
                variant="success",
                action="restart",
            )
            end_btn.add_class("choice-card")
            end_btn.add_class("choice-card-ending")
            choices_container.mount(end_btn)
        else:
            for i, choice in enumerate(node.choices):
                # Unique ID per mount to avoid collisions if previous buttons haven't fully unmounted
                btn_id = f"choice-t{as_mixin_host(self).turn_count}-{uuid4().hex[:6]}-{i}"
                disabled_reason = None
                host = as_mixin_host(self)
                if host.engine:
                    disabled_reason = choice.availability_reason(
                        host.engine.state.inventory,
                        host.engine.state.player_stats,
                        host.engine.state.story_flags,
                )
                label = build_choice_label(i, choice.text, disabled_reason)
                btn = Button(label, id=btn_id, variant="primary", disabled=disabled_reason is not None)
                btn.add_class("choice-card")
                if disabled_reason is None:
                    btn.add_class("choice-card-available")
                else:
                    btn.add_class("choice-card-locked")
                choices_container.mount(btn)

        self._focus_first_choice_button(choices_container)

    def _focus_first_choice_button(self, choices_container: Container) -> None:
        """Focus first available choice button for faster keyboard play."""
        app = as_textual_app(self)
        buttons = [btn for btn in choices_container.query(Button) if not btn.disabled]
        if not buttons:
            return
        app.call_after_refresh(buttons[0].focus)

    async def _trigger_choice(self, choice_idx: int, selected_button_id: str | None = None) -> None:
        """Handle choice selection and delegate to the engine."""
        app = as_textual_app(self)
        host = as_mixin_host(self)
        if (
            not host.is_runtime_active()
            or not host.engine
            or not host.engine.state.current_node
            or choice_idx >= len(host.engine.state.current_node.choices)
        ):
            return

        choice = host.engine.state.current_node.choices[choice_idx]
        disabled_reason = choice.availability_reason(
            host.engine.state.inventory,
            host.engine.state.player_stats,
            host.engine.state.story_flags,
        )
        if disabled_reason:
            app.notify(disabled_reason, severity="warning", timeout=2)
            return
        choice_text = choice.text
        rendered_turn_index = host._current_story_turn_index()

        # 1. Instant UI feedback
        host.action_skip_typewriter()
        host._current_story += f"\n\n> **You chose:** {choice_text}"
        host._current_story += "\n\n---\n\n"
        host._append_story_segment("player_choice", f"**You chose:** {choice_text}")
        host._append_story_segment("story_turn", "")

        container = app.query_one("#story-container", VerticalScroll)
        choice_md = Markdown(f"**You chose:** {choice_text}", classes="player-choice")
        container.mount(choice_md, before="#scene-art")

        new_turn = Markdown("", classes="story-turn")
        container.mount(new_turn, before="#scene-art")
        host._current_turn_widget = new_turn
        host._current_turn_text = ""
        host._refresh_story_timeline_classes()
        host.apply_ui_theme()

        self.show_loading(selected_button_id=selected_button_id)

        # 2. Journal update
        from textual.widgets import Label, ListView

        from cyoa.ui.components import JournalListItem

        journal_list = app.query_one("#journal-list", ListView)
        current_node = host.engine.state.current_node
        assert current_node is not None
        narrative_preview = current_node.narrative[:60].replace("\n", " ").strip()
        if len(current_node.narrative) > 60:
            narrative_preview += "…"
        journal_entry = f"Turn {host.engine.state.turn_count}: {choice_text} → {narrative_preview}"
        journal_list.append(
            JournalListItem(
                Label(journal_entry),
                scene_index=rendered_turn_index,
                entry_kind="choice",
                label_text=journal_entry,
            )
        )
        # U2 Fix: Scroll after refresh to ensure layout size is updated
        app.call_after_refresh(lambda: journal_list.scroll_end(animate=False))

        # 3. Cancel speculations and let the engine handle the rest
        host._redo_payloads.clear()
        app.workers.cancel_group(app, "speculation")
        await host.engine.make_choice(choice_text)
        host.action_skip_typewriter()
        persistence: Any = self
        persistence._create_autosave(host, app)
