import asyncio
import logging
from typing import Any, cast

from textual import work
from textual.containers import Container, VerticalScroll
from textual.widgets import Button, Label, ListView, Markdown, Static, Tree

from cyoa.ui.commands import RedoCommand, RestartCommand, UICommandContext, UndoCommand
from cyoa.ui.components import (
    BranchScreen,
    ConfirmScreen,
    HelpScreen,
    JournalListItem,
    NotificationHistoryScreen,
)
from cyoa.ui.mixins.contracts import (
    as_command_host,
    as_mixin_host,
    as_persistence_owner,
    as_textual_app,
)
from cyoa.ui.presenters import format_branch_restore_text

logger = logging.getLogger(__name__)

class NavigationMixin:
    """Mixin for app navigation and branching."""

    @staticmethod
    def _focus_first_available_choice(app: object) -> None:
        buttons = [
            button
            for button in as_textual_app(app).query("#choices-container Button")
            if isinstance(button, Button) and not button.disabled
        ]
        if buttons:
            as_textual_app(app).call_after_refresh(buttons[0].focus)

    @staticmethod
    def _collect_branch_targets(timeline_metadata: list[dict[str, Any]]) -> dict[str, list[int]]:
        """Group branch restore metadata by target scene id for story-map markers."""
        branch_targets: dict[str, list[int]] = {}
        for entry in timeline_metadata:
            if entry.get("kind") != "branch_restore":
                continue
            target_scene_id = entry.get("target_scene_id")
            restored_turn = entry.get("restored_turn")
            if isinstance(target_scene_id, str) and isinstance(restored_turn, int):
                branch_targets.setdefault(target_scene_id, []).append(restored_turn)
        return branch_targets

    @staticmethod
    def _format_story_map_label(
        scene_id: str,
        narrative: str,
        mood: str,
        current_scene_id: str | None,
        branch_targets: dict[str, list[int]],
        *,
        turn: int,
        depth: int,
        is_ending: bool,
    ) -> str:
        """Render a map label with mood and branch restore markers."""
        mood_map = {
            "mysterious": ("M", "magenta"),
            "heroic": ("H", "yellow"),
            "combat": ("C", "red"),
            "ethereal": ("E", "cyan"),
            "dark": ("D", "gray"),
            "grimy": ("G", "green"),
            "default": ("N", "white"),
        }
        marker, color = mood_map.get(mood, mood_map["default"])
        preview = narrative[:20].replace("\\n", " ").strip() + "..."
        branch_marker = ""
        restored_turns = branch_targets.get(scene_id, [])
        if restored_turns:
            unique_turns = ", ".join(str(turn) for turn in sorted(set(restored_turns)))
            branch_marker = f" [cyan]⟲ T{unique_turns}[/cyan]"
        meta = f" [dim]T{turn}·D{depth}[/dim]"
        ending_marker = " [red]✦[/red]" if is_ending else ""

        if scene_id == current_scene_id:
            return f"[b][reverse][{marker}] {preview}[/reverse][/b]{meta}{branch_marker}{ending_marker}"
        return f"[{color}][{marker}][/{color}] {preview}{meta}{branch_marker}{ending_marker}"

    @staticmethod
    def _trim_story_segments_for_undo(host: Any) -> None:
        """Drop the latest turn and its preceding branch/choice marker."""
        while host._story_segments and host._story_segments[-1].get("kind") == "story_turn":
            host._story_segments.pop()
            break
        if host._story_segments and host._story_segments[-1].get("kind") in {"player_choice", "branch_marker"}:
            host._story_segments.pop()

    async def action_restart(self) -> None:
        """Reset story state via the engine."""
        await RestartCommand().execute(
            UICommandContext(
                app=as_textual_app(self),
                host=as_command_host(self),
                owner=as_persistence_owner(self),
            )
        )

    def action_request_restart(self) -> None:
        """Show a confirmation dialog before restarting the adventure."""
        app = as_textual_app(self)

        def on_confirm(confirmed: bool | None) -> None:
            if confirmed:
                app.run_worker(self.action_restart(), exclusive=True)

        app.push_screen(
            ConfirmScreen("[b]Restart the adventure?[/b]\n\nAll progress will be lost."),
            on_confirm,
        )

    def action_request_quit(self) -> None:
        """Show a confirmation dialog before quitting."""
        app = as_textual_app(self)

        def on_confirm(confirmed: bool | None) -> None:
            if confirmed:
                app.exit()

        app.push_screen(
            ConfirmScreen("[b]Quit the game?[/b]\n\nUnsaved progress will be lost."),
            on_confirm,
        )

    def action_show_help(self) -> None:
        """Show the help screen with keybindings and game mechanics."""
        as_textual_app(self).push_screen(
            HelpScreen(screen_reader_mode=as_mixin_host(self).screen_reader_mode)
        )

    def action_show_notification_history(self) -> None:
        """Show a modal list of recent notifications without altering game state."""
        app = as_textual_app(self)
        app.push_screen(NotificationHistoryScreen(as_mixin_host(self).get_notification_history_lines()))

    def action_undo(self) -> None:
        """Restore the game state to before the last choice was made."""
        UndoCommand().execute(
            UICommandContext(
                app=as_textual_app(self),
                host=as_command_host(self),
                owner=as_persistence_owner(self),
            )
        )

    def action_redo(self) -> None:
        """Re-apply the most recently undone turn."""
        RedoCommand().execute(
            UICommandContext(
                app=as_textual_app(self),
                host=as_command_host(self),
                owner=as_persistence_owner(self),
            )
        )

    def action_create_bookmark(self) -> None:
        """Prompt for a bookmark name and save the current checkpoint."""
        app = as_textual_app(self)
        host = as_mixin_host(self)
        persistence = cast(Any, self)
        if not host.engine or not host.engine.state.current_node:
            app.notify("Nothing to bookmark yet.", severity="warning", timeout=2)
            return

        from cyoa.ui.components import TextPromptScreen

        def on_saved(value: str | None) -> None:
            if value:
                host._bookmark_payloads[value] = persistence._build_save_payload(host, app)
                app.notify(f"Saved bookmark: {value}", severity="information", timeout=2)

        app.push_screen(
            TextPromptScreen(
                "[b]Create Bookmark[/b]",
                value=f"Turn {host.engine.state.turn_count}",
                placeholder="Checkpoint name",
            ),
            on_saved,
        )

    def action_restore_bookmark(self) -> None:
        """Restore a named checkpoint from the current run."""
        app = as_textual_app(self)
        host = as_mixin_host(self)
        persistence = cast(Any, self)
        if not host.engine:
            return

        from cyoa.ui.components import OptionListScreen

        def on_selected(name: str | None) -> None:
            payload = host._bookmark_payloads.get(name or "")
            if name and payload:
                persistence._restore_from_payload(payload, source_label=f"Restored bookmark {name}")
                app.notify(f"Restored bookmark: {name}", severity="information", timeout=3)

        app.push_screen(
            OptionListScreen(
                "[b]Restore Bookmark[/b]",
                list(host._bookmark_payloads),
                empty_message="No bookmarks yet.",
            ),
            on_selected,
        )

    def action_toggle_journal(self) -> None:
        """Slide the journal panel in/out."""
        app = as_textual_app(self)
        panel = app.query_one("#journal-panel", Container)
        if as_mixin_host(self).compact_layout and panel.has_class("panel-collapsed"):
            # In compact mode, keep only one side panel open at a time.
            app.query_one("#story-map-panel", Container).add_class("panel-collapsed")
        panel.toggle_class("panel-collapsed")
        # Ensure scroll to end if opening
        if not panel.has_class("panel-collapsed"):
            journal_list = app.query_one("#journal-list", ListView)
            journal_list.scroll_end(animate=not as_mixin_host(self).reduced_motion)
            app.call_after_refresh(journal_list.focus)
            return
        self._focus_first_available_choice(app)

    def action_toggle_story_map(self) -> None:
        """Toggle the visibility of the story map panel."""
        app = as_textual_app(self)
        panel = app.query_one("#story-map-panel", Container)
        if as_mixin_host(self).compact_layout and panel.has_class("panel-collapsed"):
            # In compact mode, keep only one side panel open at a time.
            app.query_one("#journal-panel", Container).add_class("panel-collapsed")
        panel.toggle_class("panel-collapsed")
        if not panel.has_class("panel-collapsed"):
            as_mixin_host(self).update_story_map()
            app.call_after_refresh(app.query_one("#story-map-tree", Tree).focus)
            return
        self._focus_first_available_choice(app)

    @work(exclusive=True)
    async def action_branch_past(self) -> None:
        host = as_mixin_host(self)
        app = as_textual_app(self)
        if not host.engine or not host.engine.db or not host.engine.state.current_scene_id:
            return

        scene_id = host.engine.state.current_scene_id
        history = host.get_cached_story_history(scene_id)
        if history is None:
            history = await asyncio.to_thread(
                host.engine.db.get_scene_history_path,
                scene_id,
            )
            if history:
                host.cache_story_history(scene_id, history)
        if not history or not history.get("scenes"):
            return

        def check_branch(idx: int | None) -> None:
            if idx is not None:
                self.restore_to_scene(idx, history)

        app.push_screen(BranchScreen(history["scenes"], history["choices"]), check_branch)

    @work(exclusive=True)
    async def restore_to_scene(self, idx: int, history: dict[str, Any]) -> None:
        """Hand off restoration to the engine and update UI state."""
        app = as_textual_app(self)
        host = as_mixin_host(self)
        if not host.engine or not host.is_runtime_active():
            return

        # 1. UI Preparation
        app.query_one("#choices-container", Container).remove_children()
        app.query_one("#loading", Static).remove_class("hidden")

        fracture_label = format_branch_restore_text(
            idx,
            screen_reader_mode=host.screen_reader_mode,
        )
        fracture_msg = f"\n\n***\n\n{fracture_label}"
        host._current_story += fracture_msg
        host._append_story_segment("branch_marker", fracture_label)
        host._append_story_segment("story_turn", "")

        container = app.query_one("#story-container", VerticalScroll)
        frac_md = Markdown(fracture_label, classes="player-choice")
        container.mount(frac_md, before="#scene-art")

        new_turn = Markdown("", classes="story-turn")
        container.mount(new_turn, before="#scene-art")
        host._current_turn_widget = new_turn
        host._current_turn_text = ""
        host._refresh_story_timeline_classes()
        host.apply_ui_theme()

        host._scroll_to_bottom()

        # 2. Journal Sync
        journal_list = app.query_one("#journal-list", ListView)
        journal_list.clear()
        for i in range(idx):
            journal_list.append(
                JournalListItem(
                    Label(f"Turn {i + 1}: {history['choices'][i]}"),
                    scene_index=i,
                    entry_kind="choice",
                    label_text=f"Turn {i + 1}: {history['choices'][i]}",
                )
            )
        journal_list.append(
            JournalListItem(
                Label(f"Timeline fracture → resumed from Turn {idx + 1}"),
                scene_index=host._current_story_turn_index(),
                entry_kind="branch",
                label_text=f"Timeline fracture → resumed from Turn {idx + 1}",
            )
        )
        journal_list.scroll_end(animate=not host.reduced_motion)

        # 3. Hand off the core logic to the engine
        # Engine events (STATS_UPDATED, INVENTORY_UPDATED, NODE_COMPLETED) will refresh the UI
        if not host.is_runtime_active():
            return
        await host.engine.branch_to_scene(idx, history)

    @work(exclusive=True)
    async def update_story_map(self) -> None:
        app = as_textual_app(self)
        host = as_mixin_host(self)
        engine = host.engine
        if not engine or not engine.db or not engine.state.story_title:
            return

        current_scene_id = engine.state.current_scene_id
        tree_data = host.get_cached_story_map(current_scene_id)
        if tree_data is None:
            tree_data = await asyncio.to_thread(
                engine.db.get_story_tree,
                engine.state.story_title,
            )
            if tree_data:
                host.cache_story_map(current_scene_id, tree_data)
        if not tree_data:
            return

        try:
            tree = app.query_one("#story-map-tree", Tree)
        except Exception as e:  # noqa: BLE001
            logger.debug("Story map tree widget not found: %s", e)
            return
        tree.clear()

        nodes = tree_data.get("nodes", {})
        edges = tree_data.get("edges", {})
        root_id = tree_data.get("root_id")
        branch_targets = self._collect_branch_targets(engine.state.timeline_metadata)

        if not root_id:
            return

        def add_children(parent_node: Any, scene_id: str, depth: int, turn: int) -> None:
            scene = nodes[scene_id]
            label = self._format_story_map_label(
                scene_id=scene_id,
                narrative=scene["narrative"],
                mood=scene.get("mood", "default"),
                current_scene_id=engine.state.current_scene_id,
                branch_targets=branch_targets,
                turn=turn,
                depth=depth,
                is_ending=not bool(scene.get("available_choices")),
            )

            tree_node = parent_node.add(
                label,
                expand=True,
                data={
                    "scene_id": scene_id,
                    "narrative": scene["narrative"],
                    "mood": scene.get("mood", "default"),
                    "turn": turn,
                    "depth": depth,
                    "is_ending": not bool(scene.get("available_choices")),
                },
            )

            for edge in edges.get(scene_id, []):
                choice_text = edge["choice"]
                choice_preview = (
                    choice_text[: 15] + "..."
                    if len(choice_text) > 15
                    else choice_text
                )
                choice_label = f"[dim]↳ {choice_preview}[/dim]"
                choice_node = tree_node.add(choice_label, expand=True)
                add_children(choice_node, edge["target_id"], depth + 1, turn + 1)

        tree.root.label = "Adventure Map"
        tree.root.expand()
        if root_id in nodes:
            add_children(tree.root, root_id, 0, 1)
