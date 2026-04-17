import asyncio
import logging
from typing import Any

from textual import work
from textual.containers import Container, VerticalScroll
from textual.widgets import Label, ListView, Markdown, Static, Tree

from cyoa.core import constants
from cyoa.ui.components import BranchScreen, ConfirmScreen, HelpScreen, JournalListItem
from cyoa.ui.mixins.contracts import as_mixin_host, as_textual_app

logger = logging.getLogger(__name__)

class NavigationMixin:
    """Mixin for app navigation and branching."""

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

        if scene_id == current_scene_id:
            return f"[b][reverse][{marker}] {preview}[/reverse][/b]{branch_marker}"
        return f"[{color}][{marker}][/{color}] {preview}{branch_marker}"

    @staticmethod
    def _trim_story_segments_for_undo(host: Any) -> None:
        """Drop the latest turn and its preceding branch/choice marker."""
        while host._story_segments and host._story_segments[-1].get("kind") == "story_turn":
            host._story_segments.pop()
            break
        if host._story_segments and host._story_segments[-1].get("kind") in {"player_choice", "branch_marker"}:
            host._story_segments.pop()

    @work(exclusive=True)
    async def action_restart(self) -> None:
        """Reset story state via the engine."""
        app = as_textual_app(self)
        host = as_mixin_host(self)
        if not host.engine or not host.is_runtime_active():
            return

        host.invalidate_scene_caches()

        host._current_story = constants.LOADING_ART
        host._current_turn_text = constants.LOADING_ART
        host._reset_story_segments(constants.LOADING_ART)

        container = app.query_one("#story-container", VerticalScroll)
        await container.query(Markdown).remove()

        new_turn = Markdown(constants.LOADING_ART, classes="story-turn", id="initial-turn")
        await container.mount(new_turn, before="#scene-art")
        host._current_turn_widget = new_turn

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
        except Exception as e:
            logger.debug("Failed to reset status display during restart: %s", e)
        await host.engine.restart()

    def action_request_restart(self) -> None:
        """Show a confirmation dialog before restarting the adventure."""
        app = as_textual_app(self)

        def on_confirm(confirmed: bool | None) -> None:
            if confirmed:
                self.action_restart()

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
        as_textual_app(self).push_screen(HelpScreen())

    def action_undo(self) -> None:
        """Restore the game state to before the last choice was made."""
        app = as_textual_app(self)
        host = as_mixin_host(self)
        if not host.engine:
            return

        # U4 Fix: Flush typewriter BEFORE DOM manipulation
        host.action_skip_typewriter()

        # Engine handles core state restoration
        if not host.engine.undo():
            app.notify("Nothing to undo.", severity="warning", timeout=2)
            return

        # Find the last separator and truncate back to it.
        sep = "\n\n> **You chose:**"
        last_choice_pos = host._current_story.rfind(sep)
        if last_choice_pos != -1:
            host._current_story = host._current_story[:last_choice_pos]

        # UI-specific restoration
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

        self._trim_story_segments_for_undo(host)
        if not host._story_segments:
            host._reset_story_segments(host._current_story)
        else:
            host._update_current_story_segment(host._current_turn_text)

        # U5 Fix: Re-mount choice buttons for the restored node
        choices_container = app.query_one("#choices-container", Container)
        choices_container.remove_children()
        if host.engine.state.current_node:
            host._mount_choice_buttons(
                host.engine.state.current_node,
                choices_container,
                is_error=False,
            )

        app.query_one("#loading", Static).add_class("hidden")
        host._scroll_to_bottom()

        # Remove the last journal entry
        journal_list = app.query_one("#journal-list", ListView)
        children = list(journal_list.children)
        if children:
            children[-1].remove()

        # U6 Fix: Update story map to reflect old position
        host.update_story_map()

        app.notify("↩ Undid last choice.", severity="information", timeout=2)

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
            app.query_one("#journal-list", ListView).scroll_end(animate=False)

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

        fracture_msg = f"\n\n***\n\n**[Time fractures... you return to Turn {idx + 1}]**"
        host._current_story += fracture_msg
        host._append_story_segment("branch_marker", f"**[Time fractures... you return to Turn {idx + 1}]**")
        host._append_story_segment("story_turn", "")

        container = app.query_one("#story-container", VerticalScroll)
        frac_md = Markdown(f"**[Time fractures... you return to Turn {idx + 1}]**", classes="player-choice")
        container.mount(frac_md, before="#scene-art")

        new_turn = Markdown("", classes="story-turn")
        container.mount(new_turn, before="#scene-art")
        host._current_turn_widget = new_turn
        host._current_turn_text = ""

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
        journal_list.scroll_end(animate=False)

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

        def add_children(parent_node: Any, scene_id: str) -> None:
            scene = nodes[scene_id]
            label = self._format_story_map_label(
                scene_id=scene_id,
                narrative=scene["narrative"],
                mood=scene.get("mood", "default"),
                current_scene_id=engine.state.current_scene_id,
                branch_targets=branch_targets,
            )

            tree_node = parent_node.add(label, expand=True)

            for edge in edges.get(scene_id, []):
                choice_text = edge["choice"]
                choice_preview = (
                    choice_text[: 15] + "..."
                    if len(choice_text) > 15
                    else choice_text
                )
                choice_label = f"[dim]↳ {choice_preview}[/dim]"
                choice_node = tree_node.add(choice_label, expand=True)
                add_children(choice_node, edge["target_id"])

        tree.root.label = "Adventure Map"
        tree.root.expand()
        if root_id in nodes:
            add_children(tree.root, root_id)
