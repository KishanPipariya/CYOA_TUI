from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from textual.app import App
from textual.containers import Container, VerticalScroll
from textual.widgets import ListView, Markdown, Static

from cyoa.core import constants
from cyoa.ui.mixins.contracts import PersistenceCommandOwner, UICommandHost
from cyoa.ui.presenters import loading_story_text

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class UICommandContext:
    app: App[object]
    host: UICommandHost
    owner: PersistenceCommandOwner


class RestartCommand:
    async def execute(self, context: UICommandContext) -> None:
        app = context.app
        host = context.host
        if not host.engine or not host.is_runtime_active():
            return

        host.invalidate_scene_caches()
        host._redo_payloads.clear()
        loading_text = loading_story_text(screen_reader_mode=host.screen_reader_mode)
        host._current_story = loading_text
        host._current_turn_text = loading_text
        host._reset_story_segments(loading_text)

        container = app.query_one("#story-container", VerticalScroll)
        await container.query(Markdown).remove()

        new_turn = Markdown(loading_text, classes="story-turn", id="initial-turn")
        await container.mount(new_turn, before="#scene-art")
        host._current_turn_widget = new_turn
        host._refresh_story_timeline_classes()

        app.query_one("#scene-art", Static).update("")
        app.query_one("#scene-art", Static).add_class("hidden")
        app.query_one("#choices-container", Container).remove_children()
        app.query_one("#journal-list", ListView).clear()

        try:
            from cyoa.ui.components import StatusDisplay

            status = app.query_one(StatusDisplay)
            status.health = 100
            status.gold = 0
            status.reputation = 0
            status.inventory = []
        except Exception as exc:
            logger.debug("Failed to reset status display during restart: %s", exc)
        await host.engine.restart()


class UndoCommand:
    def execute(self, context: UICommandContext) -> None:
        app = context.app
        host = context.host
        owner = context.owner
        if not host.engine:
            return

        host.action_skip_typewriter()
        host._redo_payloads.append(owner._build_save_payload(host, app))

        if not host.engine.undo():
            host._redo_payloads.pop()
            app.notify("Nothing to undo.", severity="warning", timeout=2)
            return

        sep = "\n\n> **You chose:**"
        last_choice_pos = host._current_story.rfind(sep)
        if last_choice_pos != -1:
            host._current_story = host._current_story[:last_choice_pos]

        container = app.query_one("#story-container")
        turns = list(container.query(Markdown))
        choices = list(container.query(".player-choice"))

        if len(turns) > 1:
            turns[-1].remove()
            if choices:
                choices[-1].remove()
            host._current_turn_widget = turns[-2]
            host._current_turn_text = (
                host.engine.state.current_node.narrative if host.engine.state.current_node else ""
            )
        else:
            host._current_turn_text = host._current_story
            host._current_turn_widget.update(host._current_turn_text)

        host._refresh_story_timeline_classes()

        host._trim_story_segments_for_undo(host)
        if not host._story_segments:
            host._reset_story_segments(host._current_story)
        else:
            host._update_current_story_segment(host._current_turn_text)

        choices_container = app.query_one("#choices-container", Container)
        choices_container.remove_children()
        if host.engine.state.current_node:
            host._mount_choice_buttons(host.engine.state.current_node, choices_container, is_error=False)

        app.query_one("#loading", Static).add_class("hidden")
        host._scroll_to_bottom()

        journal_list = app.query_one("#journal-list", ListView)
        children = list(journal_list.children)
        if children:
            children[-1].remove()

        host.update_story_map()
        app.notify("↩ Undid last choice.", severity="information", timeout=2)


class RedoCommand:
    def execute(self, context: UICommandContext) -> None:
        app = context.app
        host = context.host
        owner = context.owner
        if not host._redo_payloads:
            app.notify("Nothing to redo.", severity="warning", timeout=2)
            return
        payload = host._redo_payloads.pop()
        owner._restore_from_payload(payload, source_label="Redid turn")
        app.notify("↪ Reapplied turn.", severity="information", timeout=2)


class SaveGameCommand:
    def execute(self, context: UICommandContext) -> None:
        app = context.app
        host = context.host
        owner = context.owner
        if not host.engine or not host.engine.state.current_node:
            app.notify("Nothing to save yet.", severity="warning", timeout=2)
            return

        save_title = owner._resolve_save_title(host)
        if not save_title:
            app.notify("Nothing to save yet.", severity="warning", timeout=2)
            return

        os.makedirs(constants.SAVES_DIR, exist_ok=True)
        safe_title = "".join(c if c.isalnum() or c in " _-" else "_" for c in save_title)
        save_path = os.path.join(constants.SAVES_DIR, f"{safe_title}_turn{host.engine.state.turn_count}.json")
        save_data = owner._build_save_payload(host, app)

        try:
            owner._write_json_payload(save_path, save_data)
            host._last_manual_save_turn = host.engine.state.turn_count
            host._last_manual_save_scene_id = host.engine.state.current_scene_id
            owner._discard_autosave()
            app.notify(f"Game saved to {save_path}", severity="information", timeout=3)
        except OSError as exc:
            app.notify(f"Save failed: {exc}", severity="error", timeout=3)


class ExportStoryCommand:
    def execute(self, context: UICommandContext) -> None:
        app = context.app
        host = context.host
        owner = context.owner
        if not host.engine or host.engine.state.current_node is None:
            app.notify("Nothing to export yet.", severity="warning", timeout=2)
            return

        payload = owner._build_save_payload(host, app)
        markdown_path, json_path = owner._write_export_files(
            payload, owner._resolve_save_title(host) or "adventure"
        )
        app.notify(f"Exported story to {markdown_path} and {json_path}", severity="information", timeout=4)
