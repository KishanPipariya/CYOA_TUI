import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, call

import pytest
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.theme import Theme
from textual.widgets import Label, ListView, ProgressBar

from cyoa.core import constants
from cyoa.core.models import Choice, ChoiceRequirement, StoryNode
from cyoa.ui.app import BufferedNotification, CYOAApp
from cyoa.ui.components import BranchScreen, LoadGameScreen, StatusDisplay, ThemeSpinner
from cyoa.ui.mixins.events import EventsMixin
from cyoa.ui.mixins.navigation import NavigationMixin
from cyoa.ui.mixins.persistence import PersistenceMixin
from cyoa.ui.mixins.rendering import RenderingMixin, _detect_scene_art
from cyoa.ui.mixins.theme import ThemeMixin
from cyoa.ui.mixins.typewriter import TypewriterMixin


class DummyTypewriterHost(TypewriterMixin):
    def __init__(self) -> None:
        self.runtime_active = True
        self._is_typing = True
        self._typewriter_active_chunk: list[str] = []
        self._typewriter_queue: asyncio.Queue[str] = asyncio.Queue()
        self._current_story = ""
        self._current_turn_text = ""
        self._current_turn_widget = MagicMock()
        self.typewriter_speed = "instant"
        self.typewriter_enabled = True
        self.segment_updates: list[str] = []
        self.notifications: list[str] = []
        self.scroll_calls: list[bool] = []

    def is_runtime_active(self) -> bool:
        return self.runtime_active

    def _is_at_bottom(self) -> bool:
        return True

    def _scroll_to_bottom(self, animate: bool = True) -> None:
        self.scroll_calls.append(animate)

    def _update_current_story_segment(self, text: str) -> None:
        self.segment_updates.append(text)

    def notify(self, message: str, **_: object) -> None:
        self.notifications.append(message)


class DummyThemeHost(ThemeMixin):
    def __init__(self) -> None:
        self.dark = True
        self.theme = "textual-dark"
        self.registered_theme_names: list[str] = []
        self.container = MagicMock()
        self.spinner = MagicMock()

    def register_theme(self, theme: Theme) -> None:
        self.registered_theme_names.append(theme.name)

    def query_one(self, selector: str, *_args: object) -> object:
        if selector == "#main-container":
            return self.container
        if selector == "#loading":
            return self.spinner
        raise AssertionError(selector)

    def update(self, *_args: object, **_kwargs: object) -> None:
        return None


class DummyRenderingHost(RenderingMixin):
    def __init__(self) -> None:
        self.runtime_active = True
        self.typewriter_enabled = False
        self._loading_suffix_shown = True
        self._current_story = constants.LOADING_ART
        self._current_turn_text = constants.LOADING_ART
        self._current_turn_widget = MagicMock()
        self._typewriter_queue: asyncio.Queue[str] = asyncio.Queue()
        self.reset_calls: list[str] = []
        self.segment_updates: list[str] = []
        self.scroll_calls: list[bool] = []
        self.loading_widget = MagicMock()

    def is_runtime_active(self) -> bool:
        return self.runtime_active

    def query_one(self, selector: str, *_args: object) -> object:
        if selector == "#loading":
            return self.loading_widget
        raise AssertionError(selector)

    def _reset_story_segments(self, initial_text: str) -> None:
        self.reset_calls.append(initial_text)

    def _update_current_story_segment(self, text: str) -> None:
        self.segment_updates.append(text)

    def _is_at_bottom(self) -> bool:
        return True

    def _scroll_to_bottom(self, animate: bool = True) -> None:
        self.scroll_calls.append(animate)

    def action_skip_typewriter(self) -> None:
        self._current_story = "old prefix\n\n---\n\nstale"


class DummyChoiceContainer:
    def __init__(self) -> None:
        self.mounted: list[object] = []
        self.removed = False

    def mount(self, widget: object) -> None:
        self.mounted.append(widget)

    def remove_children(self) -> None:
        self.removed = True
        self.mounted.clear()

    def query(self, widget_type: type[object]) -> list[object]:
        if getattr(widget_type, "__name__", "") == "Button":
            return list(self.mounted)
        return [widget for widget in self.mounted if isinstance(widget, widget_type)]


class DummyChoiceButton:
    def __init__(self, button_id: str) -> None:
        self.id = button_id
        self.disabled = False
        self.variant = "primary"
        self.removed = False

    def remove(self) -> None:
        self.removed = True


class DummyRenderingChoiceHost(RenderingMixin):
    def __init__(self) -> None:
        self.turn_count = 7
        self._loading_suffix_shown = False
        self.engine = SimpleNamespace(
            state=SimpleNamespace(
                inventory=["torch"],
                player_stats={"health": 100, "gold": 0, "reputation": 0},
                story_flags={},
            )
        )
        self.pending_refresh_callbacks: list[object] = []

    def call_after_refresh(self, callback: object) -> None:
        self.pending_refresh_callbacks.append(callback)


class DummyPersistenceHost:
    def __init__(self) -> None:
        self.engine = SimpleNamespace(
            state=SimpleNamespace(
                story_title="",
                current_node=SimpleNamespace(title=""),
            )
        )
        self._story_segments = [{"kind": "story_turn", "text": "segment"}]
        self._current_story = "flattened story"
        self._is_typing = True
        self._typewriter_active_chunk = ["a"]
        self._typewriter_queue: asyncio.Queue[str] = asyncio.Queue()


class DummyPersistenceApp:
    def __init__(self) -> None:
        self.workers = SimpleNamespace(cancel_group=MagicMock())


class DummyEventsHost(EventsMixin):
    def __init__(self) -> None:
        self.runtime_active = True
        self._last_stats_snapshot = {"health": 100, "gold": 1, "reputation": 0}
        self.status_display = SimpleNamespace(health=0, gold=0, reputation=0, objectives=[])
        self.notifications: list[tuple[str, str]] = []

    def is_runtime_active(self) -> bool:
        return self.runtime_active

    def query_one(self, selector: object, *_args: object) -> object:
        return self.status_display

    def queue_notification(
        self,
        message: str,
        *,
        severity: str = "information",
        timeout: float = 3,
        batch: bool = True,
    ) -> None:
        self.notifications.append((message, severity))


class DummyTypewriterSettingsHost(DummyTypewriterHost):
    def __init__(self) -> None:
        super().__init__()
        self.saved_config: dict[str, object] = {}


class DummyAppTimer:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class StatusDisplayHarness(App[None]):
    def compose(self) -> ComposeResult:
        yield StatusDisplay()


class SpinnerHarness(App[None]):
    def compose(self) -> ComposeResult:
        yield ThemeSpinner(["[a]", "[b]"], id="spinner")


class BranchScreenHarness(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.screen_ref = BranchScreen(
            scenes=[{"narrative": "A very long scene " * 20, "available_choices": ["A"], "inventory": ["Torch"]}],
            choices=["Take torch"],
        )

    def compose(self) -> ComposeResult:
        yield Container()

    async def on_mount(self) -> None:
        self.push_screen(self.screen_ref)


class LoadScreenHarness(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.screen_ref = LoadGameScreen(["test_adventure_turn2.json"])

    def compose(self) -> ComposeResult:
        yield Container()

    async def on_mount(self) -> None:
        self.push_screen(self.screen_ref)


def test_typewriter_batch_catchup_consumes_active_chunk_and_queue():
    host = DummyTypewriterHost()
    host._typewriter_active_chunk = list("abc")
    for _ in range(constants.TYPEWRITER_CATCHUP_THRESHOLD + 1):
        host._typewriter_queue.put_nowait("x")

    host._handle_typewriter_batch()

    assert host._typewriter_active_chunk == []
    assert host._typewriter_queue.empty()
    assert host._current_story.startswith("abc")
    assert host._current_turn_text == host._current_story
    assert host.segment_updates


def _render_text(widget: Label | ThemeSpinner) -> str:
    return str(widget.render())


def test_skip_typewriter_flushes_pending_text_and_updates_widget():
    host = DummyTypewriterHost()
    host._typewriter_active_chunk = list("hi")
    host._typewriter_queue.put_nowait(" there")

    host.action_skip_typewriter()

    assert host._current_story == "hi there"
    assert host._current_turn_text == "hi there"
    assert host._is_typing is False
    host._current_turn_widget.update.assert_called_once_with("hi there")
    assert host.scroll_calls == [True]


def test_should_stop_typewriter_clears_state_when_runtime_is_inactive():
    host = DummyTypewriterHost()
    host.runtime_active = False
    host._typewriter_active_chunk = list("late")

    assert host._should_stop_typewriter() is True
    assert host._is_typing is False
    assert host._typewriter_active_chunk == []


def test_theme_toggle_dark_and_apply_custom_accent(monkeypatch: pytest.MonkeyPatch):
    host = DummyThemeHost()
    config: dict[str, object] = {}

    monkeypatch.setattr("cyoa.ui.mixins.theme.utils.load_config", lambda: config)
    monkeypatch.setattr("cyoa.ui.mixins.theme.utils.save_config", lambda payload: config.update(payload))

    host.action_toggle_dark()
    host._apply_custom_accent("#123456")

    assert host.dark is False
    assert config["dark"] is False
    assert "cyoa-custom" in host.registered_theme_names
    assert host.theme == "cyoa-custom"


def test_stream_narrative_replaces_loading_art_without_typewriter():
    host = DummyRenderingHost()

    host._stream_narrative("Hello")

    host.loading_widget.add_class.assert_called_once_with("hidden")
    assert host.reset_calls == [""]
    assert host._current_story == "Hello"
    assert host._current_turn_text == "Hello"
    host._current_turn_widget.update.assert_called_once_with("Hello")
    assert host.scroll_calls == [False]


def test_sync_narrative_replaces_finalized_streamed_turn():
    host = DummyRenderingHost()
    host._loading_suffix_shown = False

    host._sync_narrative("Fresh ending")

    assert host._current_story == "old prefix\n\n---\n\nFresh ending"
    assert host._current_turn_text == "Fresh ending"
    assert host.segment_updates[-1] == "Fresh ending"


def test_detect_scene_art_matches_known_keywords():
    assert _detect_scene_art("A dragon looms over the cavern.") is not None
    assert _detect_scene_art("A plain hallway with no obvious cues.") is None


def test_persistence_helpers_resolve_fallback_title_and_snapshot_story():
    host = DummyPersistenceHost()
    persistence = PersistenceMixin()

    resolved = persistence._resolve_save_title(host)
    snapshot = persistence._snapshot_story_segments(host)

    assert resolved == "Untitled Adventure"
    assert host.engine.state.story_title == "Untitled Adventure"
    assert snapshot == [{"kind": "story_turn", "text": "flattened story"}]


def test_persistence_clear_restore_runtime_state_drains_queue():
    host = DummyPersistenceHost()
    app = DummyPersistenceApp()
    host._typewriter_queue.put_nowait("pending")

    PersistenceMixin._clear_restore_runtime_state(host, app)

    app.workers.cancel_group.assert_called_once_with(app, "speculation")
    assert host._is_typing is False
    assert host._typewriter_active_chunk == []
    assert host._typewriter_queue.empty()


def test_navigation_helpers_collect_branch_targets_and_trim_story_segments():
    timeline_metadata = [
        {"kind": "branch_restore", "target_scene_id": "scene-2", "restored_turn": 3},
        {"kind": "branch_restore", "target_scene_id": "scene-2", "restored_turn": 1},
        {"kind": "other"},
    ]
    branch_targets = NavigationMixin._collect_branch_targets(timeline_metadata)
    label = NavigationMixin._format_story_map_label(
        scene_id="scene-2",
        narrative="A restored scene",
        mood="heroic",
        current_scene_id="scene-2",
        branch_targets=branch_targets,
        turn=3,
        depth=1,
        is_ending=False,
    )
    host = SimpleNamespace(
        _story_segments=[
            {"kind": "player_choice", "text": "Go north"},
            {"kind": "story_turn", "text": "Result"},
        ]
    )

    NavigationMixin._trim_story_segments_for_undo(host)

    assert branch_targets == {"scene-2": [3, 1]}
    assert "⟲ T1, 3" in label
    assert "T3·D1" in label
    assert host._story_segments == []


def test_events_stats_and_world_state_handlers_emit_notifications():
    host = DummyEventsHost()

    host._handle_stats_updated({"health": 85, "gold": 4, "reputation": 2})
    host._handle_world_state_updated(
        {
            "objectives": [
                {"text": "Escape", "status": "active"},
                {"text": "Ignore", "status": "completed"},
            ]
        }
    )

    assert host.status_display.health == 85
    assert host.status_display.gold == 4
    assert host.status_display.reputation == 2
    assert host.status_display.objectives == ["Escape"]
    assert host.notifications == [("-15 HP | +3 Gold | +2 Rep", "warning")]


@pytest.mark.asyncio
async def test_branch_screen_mount_populates_scene_list_and_cancel_dismisses() -> None:
    app = BranchScreenHarness()
    preview = app.screen_ref._build_scene_preview(app.screen_ref.scenes[0], 0, "Take torch")

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        list_view = app.screen.query_one("#branch-list", ListView)
        assert len(list_view.children) == 1
        await pilot.click("#cancel-branch")
        await pilot.pause(0.1)
        assert app.screen is not app.screen_ref

    assert "Turn 1" in preview
    assert "1 future path(s)" in preview
    assert "1 item(s) carried" in preview


@pytest.mark.asyncio
async def test_load_game_screen_mount_formats_save_names_and_selection_dismisses() -> None:
    app = LoadScreenHarness()

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        list_view = app.screen.query_one("#load-list", ListView)
        assert len(list_view.children) == 1
        item = list_view.children[0]
        assert _render_text(item.query_one(Label)) == "test adventure turn2"
        await pilot.click("#btn-load-cancel")
        await pilot.pause(0.1)
        assert app.screen is not app.screen_ref


@pytest.mark.asyncio
async def test_status_display_watchers_and_spinner_tick() -> None:
    app = StatusDisplayHarness()
    async with app.run_test() as pilot:
        display = app.query_one(StatusDisplay)
        display.health = 25
        display.gold = 9
        display.reputation = 3
        display.inventory = ["Torch", "Key"]
        display.objectives = ["Escape", "Survive", "Ignore"]
        display.generation_preset = "fast"
        display._update_stats_text()
        await pilot.pause(0.1)

        assert display.query_one("#health-bar", ProgressBar).progress == 25
        assert "25%" in _render_text(display.query_one("#stats-text", Label))
        assert "fast" in _render_text(display.query_one("#stats-text", Label))
        assert "Torch, Key" in _render_text(display.query_one("#inventory-label", Label))
        assert "Escape | Survive" in _render_text(display.query_one("#objectives-label", Label))
        assert display.has_class("health-low")

    spinner_app = SpinnerHarness()
    async with spinner_app.run_test() as pilot:
        await pilot.pause(0.1)
        spinner = spinner_app.query_one("#spinner", ThemeSpinner)
        first_frame = _render_text(spinner)
        assert first_frame in {"[a]", "[b]"}
        spinner.tick()
        advanced_frame = _render_text(spinner)
        assert advanced_frame in {"[a]", "[b]"}
        spinner.add_class("hidden")
        spinner.tick()
        assert _render_text(spinner) == advanced_frame


def test_branch_and_load_screens_handle_selection_events():
    branch = BranchScreen([{"narrative": "scene"}], ["Choose"])
    branch.dismiss = MagicMock()
    scene_item = branch._build_scene_preview({"narrative": "scene"}, 0, "Choose")
    assert "Turn 1" in scene_item
    branch.on_list_view_selected(SimpleNamespace(item=SimpleNamespace(scene_index=2)))
    branch.dismiss.assert_not_called()
    branch.on_list_view_selected(SimpleNamespace(item=branch.compose))
    branch.dismiss.assert_not_called()

    from cyoa.ui.components import SaveListItem, SceneListItem

    branch.on_list_view_selected(
        SimpleNamespace(item=SceneListItem(Label("x"), scene_index=2))
    )
    branch.dismiss.assert_called_once_with(2)

    load = LoadGameScreen(["save_one.json"])
    load.dismiss = MagicMock()
    load.on_list_view_selected(SimpleNamespace(item=SimpleNamespace(save_filename="ignored")))
    load.dismiss.assert_not_called()
    load.on_list_view_selected(
        SimpleNamespace(item=SaveListItem(Label("x"), save_filename="save_one.json"))
    )
    load.dismiss.assert_called_once_with("save_one.json")


def test_confirm_and_help_screens_dismiss_expected_values():
    from cyoa.ui.components import ConfirmScreen, HelpScreen

    confirm = ConfirmScreen("Proceed?")
    confirm.dismiss = MagicMock()
    confirm.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-confirm-yes")))
    confirm.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-confirm-no")))
    confirm.action_confirm()
    confirm.action_cancel()

    assert confirm.dismiss.call_args_list == [
        call(True),
        call(False),
        call(True),
        call(False),
    ]

    help_screen = HelpScreen()
    help_screen.dismiss = MagicMock()
    help_screen.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-help-close")))
    help_screen.action_close()
    assert help_screen.dismiss.call_args_list == [call(None), call(None)]


def test_theme_watch_mood_updates_container_spinner_and_theme(monkeypatch: pytest.MonkeyPatch):
    host = DummyThemeHost()
    monkeypatch.setattr(
        "cyoa.ui.mixins.theme.theme_loader.get_config_for_mood",
        lambda mood: {"spinner_frames": ["{", "}"], "accent_color": "#abcdef"} if mood == "heroic" else None,
    )

    host.watch_mood("default", "heroic")

    host.container.remove_class.assert_called_once_with("mood-default")
    host.container.add_class.assert_called_once_with("mood-heroic")
    assert host.spinner.frames == ["{", "}"]
    host.spinner.update.assert_called_once_with("{")
    assert "mood-heroic" in host.registered_theme_names
    assert host.theme == "mood-heroic"


def test_rendering_show_loading_and_mount_choice_buttons_cover_states():
    host = DummyRenderingChoiceHost()
    choices_container = DummyChoiceContainer()
    keep = DummyChoiceButton("choice-keep")
    drop = DummyChoiceButton("choice-drop")
    loading_widget = MagicMock()
    host.query_one = lambda selector, *_args: choices_container if selector == "#choices-container" else loading_widget
    host.is_runtime_active = lambda: True

    choices_container.mounted = [keep, drop]
    host.show_loading(selected_button_id="choice-keep")

    assert drop.removed is True
    assert keep.disabled is True
    assert keep.variant == "default"
    loading_widget.remove_class.assert_called_once_with("hidden")
    assert host._loading_suffix_shown is True

    error_container = DummyChoiceContainer()
    error_node = StoryNode(
        narrative=f"{constants.ERROR_NARRATIVE_PREFIX} backend timeout",
        choices=[Choice(text="Fallback option"), Choice(text="Retreat")],
    )
    host._mount_choice_buttons(error_node, error_container, is_error=True)
    assert [getattr(widget, "id", None) for widget in error_container.mounted][:1] == ["btn-retry"]

    ending_container = DummyChoiceContainer()
    host._mount_choice_buttons(
        StoryNode(narrative="The end.", choices=[], is_ending=True),
        ending_container,
        is_error=False,
    )
    assert [getattr(widget, "id", None) for widget in ending_container.mounted] == ["btn-new-adventure"]

    locked_container = DummyChoiceContainer()
    locked_choice = Choice(
        text="Open gate",
        requirements=ChoiceRequirement(items=["key"]),
    )
    host.engine.state.inventory = []
    host._mount_choice_buttons(
        StoryNode(
            narrative="A locked gate blocks the path.",
            choices=[locked_choice, Choice(text="Walk away")],
        ),
        locked_container,
        is_error=False,
    )
    mounted_button = locked_container.mounted[0]
    assert mounted_button.disabled is True
    assert "Locked:" in str(mounted_button.label)


def test_typewriter_settings_actions_persist_preferences(monkeypatch: pytest.MonkeyPatch):
    host = DummyTypewriterSettingsHost()
    config: dict[str, object] = {}
    monkeypatch.setattr("cyoa.ui.mixins.typewriter.utils.load_config", lambda: config)
    monkeypatch.setattr("cyoa.ui.mixins.typewriter.utils.save_config", lambda payload: config.update(payload))

    host._typewriter_active_chunk = list("Hi")
    host.action_toggle_typewriter()
    host.action_cycle_typewriter_speed()

    assert host.notifications[0] == "Typewriter Narrator: Disabled"
    assert host.notifications[1].startswith("Typewriter Speed:")
    assert config["typewriter"] is False
    assert config["typewriter_speed"] == "slow"
    assert host._current_story == "Hi"


def test_app_notification_and_cache_helpers_cover_ui_shell(monkeypatch: pytest.MonkeyPatch):
    app = CYOAApp(model_path="dummy.gguf")
    notified: list[tuple[str, str, float]] = []
    timer = DummyAppTimer()

    monkeypatch.setattr(app, "notify", lambda message, *, severity, timeout: notified.append((message, severity, timeout)))
    monkeypatch.setattr(app, "set_timer", lambda *_args, **_kwargs: timer)

    app.queue_notification("alpha", severity="information", timeout=2)
    app.queue_notification("alpha", severity="information", timeout=2)
    app.queue_notification("beta", severity="warning", timeout=4)
    assert len(app._notification_buffer) == 2
    assert app._notification_timer is timer

    app._flush_buffered_notifications()
    assert notified == [("alpha | beta", "warning", 4)]
    assert app._notification_buffer == []

    app._notification_buffer = [
        BufferedNotification("one", "information", 1),
        BufferedNotification("two", "warning", 2),
        BufferedNotification("three", "error", 3),
        BufferedNotification("four", "information", 1),
    ]
    app._flush_buffered_notifications()
    assert notified[-1] == ("one | two | three | +1 more", "error", 3)

    app.cache_story_history("a", {"turn": 1})
    app.cache_story_history("b", {"turn": 2})
    app.cache_story_map("a", {"map": 1})
    app.cache_story_map("b", {"map": 2})
    assert app.get_cached_story_history("a") == {"turn": 1}
    assert app.get_cached_story_map("b") == {"map": 2}

    app.invalidate_scene_caches(keep_scene_id="b")
    assert app.get_cached_story_history("a") is None
    assert app.get_cached_story_history("b") == {"turn": 2}
    assert app.get_cached_story_map("a") is None
    assert app.get_cached_story_map("b") == {"map": 2}

    app.invalidate_scene_caches()
    assert app._story_history_cache == {}
    assert app._story_map_cache == {}


def test_app_marks_first_scene_and_tears_down_runtime(monkeypatch: pytest.MonkeyPatch):
    app = CYOAApp(model_path="dummy.gguf")
    startup_timer = DummyAppTimer()
    warmup_timer = DummyAppTimer()
    notification_timer = DummyAppTimer()

    app._startup_timer = cast(Any, startup_timer)
    app._post_render_warmup_timer = cast(Any, warmup_timer)
    app._notification_timer = cast(Any, notification_timer)
    cancel_background_workers = MagicMock()
    close_runtime_resources = MagicMock()
    unsubscribe_engine_events = MagicMock()
    monkeypatch.setattr(cast(Any, app), "_cancel_background_workers", cancel_background_workers)
    monkeypatch.setattr(cast(Any, app), "_close_runtime_resources", close_runtime_resources)
    monkeypatch.setattr(cast(Any, app), "_unsubscribe_engine_events", unsubscribe_engine_events)
    scheduled_timer = DummyAppTimer()
    monkeypatch.setattr(app, "set_timer", lambda *_args, **_kwargs: scheduled_timer)

    app.mark_first_scene_rendered()
    assert app._has_rendered_first_scene is True
    assert app._post_render_warmup_timer is scheduled_timer

    app.on_unmount()
    assert startup_timer.stopped is True
    assert scheduled_timer.stopped is True
    assert notification_timer.stopped is True
    cancel_background_workers.assert_called_once()
    close_runtime_resources.assert_called_once()
    unsubscribe_engine_events.assert_called_once()
