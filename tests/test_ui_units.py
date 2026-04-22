import asyncio
import os
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, call

import pytest
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.theme import Theme
from textual.widgets import Button, Label, ListView, ProgressBar

from cyoa.core import constants
from cyoa.core.models import Choice, ChoiceRequirement, StoryNode
from cyoa.core.runtime import EnginePhase, EngineTransition
from cyoa.ui.app import BufferedNotification, CYOAApp
from cyoa.ui.components import (
    BranchScreen,
    FirstRunSetupScreen,
    LoadGameScreen,
    ModelDownloadScreen,
    SettingsScreen,
    StartupChoiceScreen,
    StatusDisplay,
    ThemeSpinner,
)
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
        self._ui_theme: dict[str, str] = {}
        self.container = MagicMock()
        self.spinner = MagicMock()
        self.action_panel = MagicMock()
        self.status_display = MagicMock()
        self.side_panel = MagicMock()
        self.story_turn = MagicMock()
        self.archived_turn = MagicMock()
        self.player_choice = MagicMock()
        self.choice_card = MagicMock()
        self.locked_choice = MagicMock()

    def register_theme(self, theme: Theme) -> None:
        self.registered_theme_names.append(theme.name)

    def query_one(self, selector: str, *_args: object) -> object:
        if selector == "#main-container":
            return self.container
        if selector == "#action-panel":
            return self.action_panel
        if selector == "#status-display":
            return self.status_display
        if selector == "#loading":
            return self.spinner
        raise AssertionError(selector)

    def query(self, selector: str) -> list[object]:
        mapping = {
            ".side-panel-shell": [self.side_panel],
            ".story-turn.current-turn": [self.story_turn],
            ".story-turn.archived-turn": [self.archived_turn],
            ".player-choice": [self.player_choice],
            "#choices-container .choice-card-available": [self.choice_card],
            "#choices-container .choice-card-locked": [self.locked_choice],
        }
        if selector not in mapping:
            raise AssertionError(selector)
        return mapping[selector]

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

    def apply_ui_theme(self) -> None:
        return None


class DummyChoiceContainer:
    def __init__(self) -> None:
        self.mounted: list[object] = []
        self.removed = False
        self.classes: set[str] = set()

    def mount(self, widget: object) -> None:
        self.mounted.append(widget)

    def remove_children(self) -> None:
        self.removed = True
        self.mounted.clear()

    def query(self, widget_type: type[object]) -> list[object]:
        if getattr(widget_type, "__name__", "") == "Button":
            return list(self.mounted)
        return [widget for widget in self.mounted if isinstance(widget, widget_type)]

    def add_class(self, name: str) -> None:
        self.classes.add(name)

    def remove_class(self, name: str) -> None:
        self.classes.discard(name)


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

    def apply_ui_theme(self) -> None:
        return None


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

    def apply_ui_theme(self) -> None:
        return None


class DummyPersistenceApp:
    def __init__(self) -> None:
        self.workers = SimpleNamespace(cancel_group=MagicMock())


class DummyEventsHost(EventsMixin):
    def __init__(self) -> None:
        self.runtime_active = True
        self._last_stats_snapshot = {"health": 100, "gold": 1, "reputation": 0}
        self.status_display = SimpleNamespace(health=0, gold=0, reputation=0, objectives=[])
        self.loading = MagicMock()
        self.notifications: list[tuple[str, str]] = []
        self.sync_calls = 0

    def is_runtime_active(self) -> bool:
        return self.runtime_active

    def query_one(self, selector: object, *_args: object) -> object:
        if selector == "#loading":
            return self.loading
        return self.status_display

    def _sync_runtime_status(self) -> None:
        self.sync_calls += 1

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


class FirstRunScreenHarness(App[None]):
    def __init__(
        self,
        *,
        general_notes: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        self.screen_ref = FirstRunSetupScreen(
            general_notes=general_notes,
        )

    def compose(self) -> ComposeResult:
        yield Container()

    async def on_mount(self) -> None:
        self.push_screen(self.screen_ref)


class SettingsScreenHarness(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.screen_ref = SettingsScreen(
            provider="mock",
            model_path="",
            theme="dark_dungeon",
            dark=True,
            typewriter=True,
            typewriter_speed="normal",
            diagnostics_enabled=False,
            available_themes=["dark_dungeon", "space_explorer"],
        )

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


def test_events_phase_handler_updates_runtime_status_and_spinner():
    host = DummyEventsHost()

    host._handle_engine_phase_changed(
        EngineTransition(
            from_phase=EnginePhase.IDLE,
            to_phase=EnginePhase.GENERATING,
            reason="generate_next",
        )
    )
    host._handle_engine_phase_changed(
        EngineTransition(
            from_phase=EnginePhase.GENERATING,
            to_phase=EnginePhase.READY,
            reason="generation_completed",
        )
    )

    assert host.sync_calls == 2
    host.loading.remove_class.assert_called_once_with("hidden")
    host.loading.add_class.assert_called_once_with("hidden")


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
async def test_first_run_screen_exposes_mock_and_download_actions() -> None:
    app = FirstRunScreenHarness()

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        mock_button = app.screen.query_one("#btn-first-run-mock", Button)
        download_button = app.screen.query_one("#btn-first-run-download", Button)
        assert mock_button.disabled is False
        assert download_button.disabled is False


@pytest.mark.asyncio
async def test_first_run_screen_renders_general_notes() -> None:
    app = FirstRunScreenHarness(general_notes=("Resize the terminal if panels feel cramped.",))

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        labels = [label.render().plain for label in app.screen.query(Label)]
        assert any("Resize the terminal" in text for text in labels)


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
        display.directives = ["No combat", "Stay hidden", "Ignore"]
        display.generation_preset = "fast"
        display.runtime_profile = "balanced-runtime-profile"
        display.provider_label = "provider-with-a-very-long-name"
        display.engine_phase = "ready"
        display._update_stats_text()
        await pilot.pause(0.1)

        assert display.query_one("#health-bar", ProgressBar).progress == 25
        assert "25%" in _render_text(display.query_one("#health-value", Label))
        assert "Gold 9" in _render_text(display.query_one("#stats-text", Label))
        assert "fast" in _render_text(display.query_one("#runtime-text", Label))
        assert "ready" in _render_text(display.query_one("#runtime-text", Label))
        assert "provider-with-a-very-long-name" in _render_text(display.query_one("#runtime-text", Label))
        assert "Torch, Key" in _render_text(display.query_one("#inventory-label", Label))
        assert "Escape | Survive" in _render_text(display.query_one("#objectives-label", Label))
        assert "No combat | Stay hidden" in _render_text(display.query_one("#directives-label", Label))
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
    from cyoa.ui.components import ConfirmScreen

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


def test_startup_choice_screen_dismisses_expected_values():
    from cyoa.ui.components import HelpScreen

    startup = StartupChoiceScreen("Resume or start over?")
    startup.dismiss = MagicMock()
    startup.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-startup-resume")))
    startup.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-startup-new")))
    startup.action_resume()
    startup.action_new_game()

    assert startup.dismiss.call_args_list == [
        call("resume"),
        call("new"),
        call("resume"),
        call("new"),
    ]

    help_screen = HelpScreen()
    help_screen.dismiss = MagicMock()
    help_screen.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-help-close")))
    help_screen.action_close()
    assert help_screen.dismiss.call_args_list == [call(None), call(None)]


def test_first_run_setup_screen_dismisses_expected_values():
    first_run = FirstRunSetupScreen()
    first_run.dismiss = MagicMock()
    first_run.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-first-run-mock")))
    first_run.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-first-run-download")))
    first_run.action_quick_demo()
    first_run.action_download_model()

    assert first_run.dismiss.call_args_list == [
        call("mock"),
        call("download"),
        call("mock"),
        call("download"),
    ]


def test_settings_screen_dismisses_saved_payload():
    settings = SettingsScreen(
        provider="mock",
        model_path="",
        theme="dark_dungeon",
        dark=True,
        typewriter=True,
        typewriter_speed="normal",
        diagnostics_enabled=False,
        available_themes=["dark_dungeon", "space_explorer"],
    )
    settings.dismiss = MagicMock()
    settings._refresh_state = MagicMock()
    settings.query_one = lambda selector, *_args: SimpleNamespace(value="/tmp/demo.gguf") if selector == "#settings-model-path" else None

    settings.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-settings-provider-llama")))
    settings.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-settings-theme-next")))
    settings.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-settings-typewriter-off")))
    settings.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-settings-speed-fast")))
    settings.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-settings-diagnostics-on")))
    settings.action_save()

    saved_payload = settings.dismiss.call_args_list[-1].args[0]
    assert saved_payload == {
        "provider": "llama_cpp",
        "model_path": "/tmp/demo.gguf",
        "theme": "space_explorer",
        "dark": True,
        "typewriter": False,
        "typewriter_speed": "fast",
        "diagnostics_enabled": True,
    }


def test_settings_screen_support_actions_dismiss_expected_payloads():
    settings = SettingsScreen(
        provider="mock",
        model_path="",
        theme="dark_dungeon",
        dark=True,
        typewriter=True,
        typewriter_speed="normal",
        diagnostics_enabled=False,
        available_themes=["dark_dungeon"],
    )
    settings.dismiss = MagicMock()

    settings.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-settings-test-backend")))
    settings.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-settings-reveal-saves")))
    settings.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-settings-reset")))

    assert settings.dismiss.call_args_list == [
        call({"action": "test_backend"}),
        call({"action": "reveal_saves"}),
        call({"action": "reset_settings"}),
    ]


def test_cyoa_app_first_run_selection_updates_runtime_and_config(monkeypatch: pytest.MonkeyPatch):
    app = CYOAApp(model_path="")
    saved: dict[str, object] = {}
    app._sync_runtime_status = MagicMock()
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_PRESET", raising=False)
    monkeypatch.delenv("LLM_MODEL_PATH", raising=False)

    monkeypatch.setattr(
        "cyoa.ui.app.update_user_config",
        lambda **changes: saved.update(changes) or SimpleNamespace(**changes),
    )

    app._apply_first_run_selection("mock")

    assert os.environ["LLM_PROVIDER"] == "mock"
    assert os.environ["LLM_PRESET"] == "precise"
    assert app._runtime_diagnostics["runtime_preset"] == "mock-smoke"
    assert app._runtime_diagnostics["provider"] == "mock"
    assert app._runtime_diagnostics["model"] == "mock"
    assert saved["setup_completed"] is True
    assert saved["setup_choice"] == "mock"
    assert saved["runtime_preset"] == "mock-smoke"
    app._sync_runtime_status.assert_called_once_with()


def test_cyoa_app_downloaded_model_selection_updates_runtime_and_config(
    monkeypatch: pytest.MonkeyPatch,
):
    app = CYOAApp(model_path="")
    saved: dict[str, object] = {}
    app._sync_runtime_status = MagicMock()
    previous_provider = os.environ.get("LLM_PROVIDER")
    previous_model_path = os.environ.get("LLM_MODEL_PATH")
    previous_preset = os.environ.get("LLM_PRESET")
    monkeypatch.setattr(
        "cyoa.ui.app.update_user_config",
        lambda **changes: saved.update(changes) or SimpleNamespace(**changes),
    )

    try:
        result = SimpleNamespace(path="/tmp/models/demo.gguf")
        app._apply_downloaded_model_selection(result)

        assert os.environ["LLM_PROVIDER"] == "llama_cpp"
        assert os.environ["LLM_MODEL_PATH"] == "/tmp/models/demo.gguf"
        assert os.environ["LLM_PRESET"] == "balanced"
        assert app.model_path == "/tmp/models/demo.gguf"
        assert saved["setup_completed"] is True
        assert saved["setup_choice"] == "download"
        assert saved["runtime_preset"] == "local-fast"
        app._sync_runtime_status.assert_called_once_with()
    finally:
        if previous_provider is None:
            os.environ.pop("LLM_PROVIDER", None)
        else:
            os.environ["LLM_PROVIDER"] = previous_provider
        if previous_model_path is None:
            os.environ.pop("LLM_MODEL_PATH", None)
        else:
            os.environ["LLM_MODEL_PATH"] = previous_model_path
        if previous_preset is None:
            os.environ.pop("LLM_PRESET", None)
        else:
            os.environ["LLM_PRESET"] = previous_preset


def test_cyoa_app_apply_settings_updates_runtime_and_config(monkeypatch: pytest.MonkeyPatch):
    app = CYOAApp(model_path="/tmp/current.gguf")
    app._user_config = SimpleNamespace(
        provider="mock",
        model_path=None,
        theme="dark_dungeon",
        dark=True,
        typewriter=True,
        typewriter_speed="normal",
        diagnostics_enabled=False,
    )
    app._runtime_diagnostics["provider"] = "mock"
    app.notify = MagicMock()
    app.action_skip_typewriter = MagicMock()
    saved: dict[str, object] = {}
    previous_env = os.environ.get("CYOA_ENABLE_RAG")
    monkeypatch.setattr(
        "cyoa.ui.app.update_user_config",
        lambda **changes: saved.update(changes) or SimpleNamespace(**changes),
    )

    try:
        app._apply_settings(
            {
                "provider": "llama_cpp",
                "model_path": "/tmp/models/demo.gguf",
                "theme": "space_explorer",
                "dark": False,
                "typewriter": False,
                "typewriter_speed": "fast",
                "diagnostics_enabled": True,
            }
        )

        assert app.dark is False
        assert app.typewriter_enabled is False
        assert app.typewriter_speed == "fast"
        assert os.environ["CYOA_ENABLE_RAG"] == "1"
        assert saved["provider"] == "llama_cpp"
        assert saved["model_path"] == "/tmp/models/demo.gguf"
        assert saved["theme"] == "space_explorer"
        assert saved["diagnostics_enabled"] is True
        app.action_skip_typewriter.assert_called_once_with()
        app.notify.assert_called_once()
        assert "Restart to apply: theme, provider, model path." in app.notify.call_args.args[0]
    finally:
        if previous_env is None:
            os.environ.pop("CYOA_ENABLE_RAG", None)
        else:
            os.environ["CYOA_ENABLE_RAG"] = previous_env


def test_cyoa_app_handle_settings_action_routes_requests() -> None:
    app = CYOAApp(model_path="")
    app.run_worker = MagicMock(side_effect=lambda coro, **_kwargs: coro.close())
    app._reveal_save_folder = MagicMock()
    app.push_screen = MagicMock()

    app._handle_settings_action("test_backend")
    app._handle_settings_action("reveal_saves")
    app._handle_settings_action("reset_settings")

    app.run_worker.assert_called_once()
    app._reveal_save_folder.assert_called_once_with()
    app.push_screen.assert_called_once()


def test_cyoa_app_reset_settings_restores_safe_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    app = CYOAApp(model_path="/tmp/current.gguf")
    app.notify = MagicMock()
    app._user_config = SimpleNamespace(dark=False, typewriter=False, typewriter_speed="fast")
    monkeypatch.setenv("CYOA_ENABLE_RAG", "1")
    monkeypatch.setenv("LLM_MODEL_PATH", "/tmp/current.gguf")
    monkeypatch.setattr(
        "cyoa.ui.app.reset_user_config",
        lambda preserve_setup=True: SimpleNamespace(
            dark=True,
            typewriter=True,
            typewriter_speed="normal",
        ),
    )

    app._reset_settings_to_safe_defaults()

    assert app.dark is True
    assert app.typewriter_enabled is True
    assert app.typewriter_speed == "normal"
    assert "CYOA_ENABLE_RAG" not in os.environ
    assert "LLM_MODEL_PATH" not in os.environ
    app.notify.assert_called_once()


@pytest.mark.asyncio
async def test_cyoa_app_backend_test_reports_missing_model_path() -> None:
    app = CYOAApp(model_path="")
    app.notify = MagicMock()
    app._user_config = SimpleNamespace(provider="llama_cpp", model_path=None)

    await app._run_backend_connection_test()

    assert "no GGUF path is saved" in app.notify.call_args.args[0]


@pytest.mark.asyncio
async def test_cyoa_app_backend_test_reports_success_for_mock() -> None:
    app = CYOAApp(model_path="")
    app.notify = MagicMock()
    app._user_config = SimpleNamespace(provider="mock", model_path=None)

    await app._run_backend_connection_test()

    assert app.notify.call_args.args[0] == "Quick Demo backend is ready."


class ModelDownloadHarness(App[None]):
    def __init__(self, *, blocked_reason: str | None = None) -> None:
        super().__init__()
        self.begin_first_run_model_download = MagicMock()
        self.cancel_first_run_model_download = MagicMock()
        self.screen_ref = ModelDownloadScreen(
            SimpleNamespace(
                label="7B (Balanced - Q5_K_M)",
                filename="demo.gguf",
                repo_id="Qwen/demo",
            ),
            models_dir="/tmp/cyoa-models",
            preflight_notes=("Machine check passed.",),
            blocked_reason=blocked_reason,
        )

    def compose(self) -> ComposeResult:
        yield Container()

    async def on_mount(self) -> None:
        self.push_screen(self.screen_ref)


@pytest.mark.asyncio
async def test_model_download_screen_wires_start_cancel_and_progress() -> None:
    app = ModelDownloadHarness()

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await pilot.click("#btn-model-download-start")
        await pilot.pause(0.1)
        app.begin_first_run_model_download.assert_called_once()

        app.screen_ref.update_progress(
            SimpleNamespace(percent=40, stage="Downloading", detail="Pulling model weights.")
        )
        await pilot.pause(0.1)
        progress = app.screen.query_one("#model-download-progress", ProgressBar)
        detail = app.screen.query_one("#model-download-detail", Label)
        assert progress.progress == 40
        assert "Pulling model weights." in detail.render().plain

        await pilot.click("#btn-model-download-cancel")
        await pilot.pause(0.1)
        app.cancel_first_run_model_download.assert_called_once_with()


@pytest.mark.asyncio
async def test_model_download_screen_blocks_start_when_preflight_fails() -> None:
    app = ModelDownloadHarness(blocked_reason="Only 1.0 GB free disk space is available.")

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        start_button = app.screen.query_one("#btn-model-download-start", Button)
        detail = app.screen.query_one("#model-download-detail", Label)
        assert start_button.disabled is True
        assert "Only 1.0 GB free disk space is available." in detail.render().plain
        await pilot.click("#btn-model-download-start")
        await pilot.pause(0.1)
        app.begin_first_run_model_download.assert_not_called()


@pytest.mark.asyncio
async def test_settings_screen_cycles_and_saves_choices() -> None:
    app = SettingsScreenHarness()

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        app.screen_ref.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-settings-provider-llama")))
        app.screen_ref.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-settings-theme-next")))
        app.screen_ref.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-settings-dark-off")))
        app.screen_ref.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-settings-typewriter-off")))
        app.screen_ref.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-settings-speed-instant")))
        app.screen_ref.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn-settings-diagnostics-on")))
        await pilot.pause(0.1)

        provider_label = app.screen.query_one("#settings-provider-value", Label)
        theme_label = app.screen.query_one("#settings-theme-value", Label)
        assert "saved GGUF" in provider_label.render().plain
        assert "space_explorer" in theme_label.render().plain


def test_cyoa_app_requires_first_run_until_setup_completed() -> None:
    assert CYOAApp._requires_first_run_setup(SimpleNamespace(setup_completed=False)) is True
    assert CYOAApp._requires_first_run_setup(SimpleNamespace(setup_completed=True)) is False


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


def test_apply_ui_theme_styles_primary_surfaces_and_dynamic_widgets() -> None:
    host = DummyThemeHost()
    host._ui_theme = {
        "main_surface": "#101820",
        "action_dock_surface": "#111827",
        "status_surface": "#17212b",
        "side_panel_surface": "#131a22",
        "story_card_surface": "#18242d",
        "story_card_muted_surface": "#0f161d",
        "player_choice_surface": "#1b3140",
        "choice_surface": "#213646",
        "choice_locked_surface": "#15191d",
    }

    host.apply_ui_theme()

    host.container.set_styles.assert_called_once_with("background: #101820;")
    host.action_panel.set_styles.assert_called_once_with("background: #111827;")
    host.status_display.set_styles.assert_called_once_with("background: #17212b;")
    host.side_panel.set_styles.assert_called_once_with("background: #131a22;")
    host.story_turn.set_styles.assert_called_once_with("background: #18242d;")
    host.archived_turn.set_styles.assert_called_once_with("background: #0f161d;")
    host.player_choice.set_styles.assert_called_once_with("background: #1b3140;")
    host.choice_card.set_styles.assert_called_once_with("background: #213646;")
    host.locked_choice.set_styles.assert_called_once_with("background: #15191d;")


def test_rendering_show_loading_and_mount_choice_buttons_cover_states():
    host = DummyRenderingChoiceHost()
    choices_container = DummyChoiceContainer()
    story_container = MagicMock()
    keep = DummyChoiceButton("choice-keep")
    drop = DummyChoiceButton("choice-drop")
    loading_widget = MagicMock()
    host.query_one = (
        lambda selector, *_args: (
            choices_container
            if selector == "#choices-container"
            else story_container
            if selector == "#story-container"
            else loading_widget
        )
    )
    host.is_runtime_active = lambda: True

    choices_container.mounted = [keep, drop]
    host.show_loading(selected_button_id="choice-keep")

    assert drop.removed is True
    assert keep.disabled is True
    assert keep.variant == "default"
    story_container.add_class.assert_called_once_with("loading-state")
    assert "loading-state" not in choices_container.classes
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

    app._running = False
    app.queue_notification("ignored", severity="information", timeout=2)
    assert app._notification_buffer == []
    assert app._notification_timer is None

    app._running = True
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
