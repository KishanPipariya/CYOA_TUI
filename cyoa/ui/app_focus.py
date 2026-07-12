import asyncio
from collections.abc import Callable
from typing import Any

from textual.app import ScreenStackError
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button

from cyoa.ui.app_types import FocusTarget


class FocusModalMixin:
    """Focus capture and restoration helpers around modal screens."""

    def _focused_widget(self) -> Widget | None:
        try:
            focused = self.focused
        except ScreenStackError:
            return None
        return focused if isinstance(focused, Widget) else None

    def _capture_focus_target(self) -> FocusTarget | None:
        focused = self._focused_widget()
        if focused is None or not focused.is_attached:
            return None

        if isinstance(focused, Button):
            buttons = self._available_action_buttons()
            if focused in buttons:
                return FocusTarget("choice_index", buttons.index(focused))

        widget: Widget | None = focused
        while widget is not None:
            if widget.id:
                return FocusTarget("widget_id", widget.id)
            parent = widget.parent
            widget = parent if isinstance(parent, Widget) else None
        return None

    def _widget_can_receive_focus(self, widget: Widget) -> bool:
        if not widget.is_attached or not widget.visible or not widget.display:
            return False
        if bool(getattr(widget, "disabled", False)):
            return False

        current: Widget | None = widget
        while current is not None:
            if current.has_class("hidden") or current.has_class("panel-collapsed"):
                return False
            parent = current.parent
            current = parent if isinstance(parent, Widget) else None
        return True

    def _resolve_focus_target_widget(self, target: FocusTarget | None) -> Widget | None:
        if target is None:
            return None
        if target.kind == "choice_index":
            buttons = self._available_action_buttons()
            if not buttons:
                return None
            index = min(int(target.value), len(buttons) - 1)
            return buttons[index]
        try:
            widget = self.query_one(f"#{target.value}", Widget)
        except NoMatches:
            return None
        return widget if self._widget_can_receive_focus(widget) else None

    def _fallback_focus_widget(self, fallback: str = "choices") -> Widget | None:
        fallback_methods: dict[str, Callable[[], Widget | None]] = {
            "choices": lambda: (
                self._available_action_buttons()[0] if self._available_action_buttons() else None
            ),
            "story": lambda: self.query_one("#story-container", Widget),
            "status": lambda: self.query_one("#status-display", Widget),
            "journal": lambda: self.query_one("#journal-list", Widget),
            "story_map": lambda: self.query_one("#story-map-tree", Widget),
        }
        ordered = [fallback, "choices", "story", "status", "journal", "story_map"]
        for key in ordered:
            resolver = fallback_methods.get(key)
            if resolver is None:
                continue
            try:
                widget = resolver()
            except NoMatches:
                continue
            if widget is not None and self._widget_can_receive_focus(widget):
                return widget
        return None

    def _restore_focus_target(
        self,
        target: FocusTarget | None,
        *,
        fallback: str = "choices",
    ) -> None:
        def apply_focus() -> None:
            try:
                widget = self._resolve_focus_target_widget(target)
                if widget is None:
                    widget = self._fallback_focus_widget(fallback)
            except ScreenStackError:
                return
            if widget is not None and self._widget_can_receive_focus(widget):
                widget.focus()

        self.call_after_refresh(apply_focus)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self.set_timer(0.01, apply_focus)

    def _has_open_modal_screen(self) -> bool:
        try:
            screen_stack = self.screen_stack
        except ScreenStackError:
            return False
        return any(isinstance(screen, ModalScreen) for screen in screen_stack[1:])

    def _push_modal_screen(
        self,
        screen: ModalScreen[Any],
        callback: Callable[[Any], None] | None = None,
        *,
        fallback_focus: str = "choices",
    ) -> None:
        opened_over_modal = self._has_open_modal_screen()
        modal_focus_target = self._capture_focus_target()
        if self._modal_focus_return_target is None:
            self._modal_focus_return_target = modal_focus_target

        def on_dismiss(result: Any) -> None:
            try:
                if callback is not None:
                    callback(result)
            finally:
                if self._has_open_modal_screen():
                    if opened_over_modal:
                        self._restore_focus_target(modal_focus_target, fallback=fallback_focus)
                else:
                    target = self._modal_focus_return_target
                    self._modal_focus_return_target = None
                    self._restore_focus_target(target, fallback=fallback_focus)

        self.push_screen(screen, on_dismiss)
