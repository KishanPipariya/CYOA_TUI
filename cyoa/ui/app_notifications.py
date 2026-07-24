from typing import Any, Literal, cast

from textual.notifications import SeverityLevel

from cyoa.ui.app_types import BufferedNotification, NotificationHistoryEntry
from cyoa.ui.components import StatusDisplay
from cyoa.ui.presenters import format_status_message


class NotificationStatusMixin:
    """Notification popup batching and status-history behavior for the app shell."""

    def _notification_prefix(self, severity: SeverityLevel) -> str:
        prefix = self._notification_title(severity)
        app = cast(Any, self)
        if app.cognitive_load_reduction_mode:
            prefix = {
                "information": "Update",
                "warning": "Attention",
                "error": "Problem",
            }.get(severity, "Update")
        return prefix

    @staticmethod
    def _notification_title(severity: SeverityLevel) -> str:
        titles = {
            "information": "Information",
            "warning": "Warning",
            "error": "Error",
        }
        return titles.get(severity, "Notice")

    def _prepare_status_message(self, message: str, severity: SeverityLevel) -> str:
        app = cast(Any, self)
        prefix = self._notification_prefix(severity)
        cleaned = format_status_message(
            message,
            screen_reader_mode=app.screen_reader_mode,
            simplified_mode=app.cognitive_load_reduction_mode,
        ).strip()
        if not cleaned:
            return cleaned
        if app.notification_verbosity == "minimal":
            return cleaned
        if app.notification_verbosity == "detailed":
            detailed_prefix = f"{prefix} update"
            if cleaned.lower().startswith(f"{detailed_prefix.lower()}:"):
                return cleaned
            return f"{detailed_prefix}: {cleaned}"
        if not cleaned.lower().startswith(f"{prefix.lower()}:"):
            cleaned = f"{prefix}: {cleaned}"
        return cleaned

    def _refresh_latest_status_message(self) -> None:
        app = cast(Any, self)
        app._latest_status_message = self._prepare_status_message(
            app._latest_status_source_message,
            app._latest_status_severity,
        )
        try:
            app.query_one(StatusDisplay).latest_status = app._latest_status_message
        except Exception:
            return

    def _record_notification_history(self, message: str, severity: SeverityLevel) -> None:
        app = cast(Any, self)
        cleaned = self._prepare_status_message(message, severity)
        if not cleaned:
            return
        history = cast(list[NotificationHistoryEntry], app._notification_history)
        history.append(NotificationHistoryEntry(message=message, severity=severity))
        if len(history) > app._notification_history_limit:
            app._notification_history = history[-app._notification_history_limit :]

    def get_notification_history_lines(self) -> list[str]:
        history = cast(list[NotificationHistoryEntry], cast(Any, self)._notification_history)
        return [self._prepare_status_message(entry.message, entry.severity) for entry in history]

    def _dispatch_notification(
        self,
        message: str,
        *,
        title: str,
        severity: SeverityLevel,
        timeout: float | None,
        markup: bool,
        update_latest: bool,
    ) -> None:
        app = cast(Any, self)
        if not message:
            return
        if update_latest:
            app._latest_status_source_message = message
            app._latest_status_severity = severity
            app._latest_status_message = message
            try:
                app.query_one(StatusDisplay).latest_status = message
            except Exception:
                pass
        cast(Any, super()).notify(
            message,
            title=title,
            severity=severity,
            timeout=timeout,
            markup=markup,
        )

    def notify(
        self,
        message: str,
        *,
        title: str = "",
        severity: SeverityLevel = "information",
        timeout: float | None = None,
        markup: bool = True,
    ) -> None:
        app = cast(Any, self)
        prefix = self._notification_title(severity)
        cleaned = self._prepare_status_message(message, severity)
        self._record_notification_history(message, severity)
        self._dispatch_notification(
            cleaned,
            title=title or prefix,
            severity=severity,
            timeout=timeout,
            markup=markup and not app.screen_reader_mode,
            update_latest=True,
        )

    def action_repeat_latest_status(self) -> None:
        app = cast(Any, self)
        if not app._latest_status_message:
            self.notify("No status messages yet.", severity="warning", timeout=2)
            return
        self._dispatch_notification(
            app._latest_status_message,
            title="Latest Status",
            severity="information",
            timeout=6,
            markup=False,
            update_latest=False,
        )

    def queue_notification(
        self,
        message: str,
        *,
        severity: Literal["information", "warning", "error"] = "information",
        timeout: float = 3,
        batch: bool = True,
    ) -> None:
        """Coalesce bursty notifications into a single popup."""
        app = cast(Any, self)
        if not message or not app.is_runtime_active():
            return
        if not batch:
            self.notify(message, severity=severity, timeout=timeout)
            return

        self._record_notification_history(message, severity)
        entry = BufferedNotification(message=message, severity=severity, timeout=timeout)
        buffer = cast(list[BufferedNotification], app._notification_buffer)
        if buffer and buffer[-1] == entry:
            return
        buffer.append(entry)
        if app._notification_timer is None:
            app._notification_timer = app.set_timer(0.18, self._flush_buffered_notifications)

    def _flush_buffered_notifications(self) -> None:
        app = cast(Any, self)
        app._notification_timer = None
        buffer = cast(list[BufferedNotification], app._notification_buffer)
        if not buffer or not app.is_runtime_active():
            buffer.clear()
            return

        buffered = buffer
        app._notification_buffer = []
        if len(buffered) == 1:
            item = buffered[0]
            self._dispatch_notification(
                self._prepare_status_message(item.message, item.severity),
                title=self._notification_title(item.severity),
                severity=item.severity,
                timeout=item.timeout,
                markup=not app.screen_reader_mode,
                update_latest=True,
            )
            return

        severity_order = {"error": 3, "warning": 2, "information": 1}
        strongest = max(buffered, key=lambda item: severity_order.get(item.severity, 0))
        messages: list[str] = []
        for item in buffered:
            if item.message not in messages:
                messages.append(item.message)
        if len(messages) > 3:
            summary = " | ".join(messages[:3]) + f" | +{len(messages) - 3} more"
        else:
            summary = " | ".join(messages)
        self._dispatch_notification(
            self._prepare_status_message(summary, strongest.severity),
            title=self._notification_title(strongest.severity),
            severity=strongest.severity,
            timeout=max(item.timeout for item in buffered),
            markup=not app.screen_reader_mode,
            update_latest=True,
        )
