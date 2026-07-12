from typing import Literal

from textual.notifications import SeverityLevel

from cyoa.ui.app_types import BufferedNotification, NotificationHistoryEntry
from cyoa.ui.components import StatusDisplay
from cyoa.ui.presenters import format_status_message


class NotificationStatusMixin:
    """Notification popup batching and status-history behavior for the app shell."""

    def _notification_prefix(self, severity: SeverityLevel) -> str:
        prefix = self._notification_title(severity)
        if self.cognitive_load_reduction_mode:
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
        prefix = self._notification_prefix(severity)
        cleaned = format_status_message(
            message,
            screen_reader_mode=self.screen_reader_mode,
            simplified_mode=self.cognitive_load_reduction_mode,
        ).strip()
        if not cleaned:
            return cleaned
        if self.notification_verbosity == "minimal":
            return cleaned
        if self.notification_verbosity == "detailed":
            detailed_prefix = f"{prefix} update"
            if cleaned.lower().startswith(f"{detailed_prefix.lower()}:"):
                return cleaned
            return f"{detailed_prefix}: {cleaned}"
        if not cleaned.lower().startswith(f"{prefix.lower()}:"):
            cleaned = f"{prefix}: {cleaned}"
        return cleaned

    def _refresh_latest_status_message(self) -> None:
        self._latest_status_message = self._prepare_status_message(
            self._latest_status_source_message,
            self._latest_status_severity,
        )
        try:
            self.query_one(StatusDisplay).latest_status = self._latest_status_message
        except Exception:
            return

    def _record_notification_history(self, message: str, severity: SeverityLevel) -> None:
        cleaned = self._prepare_status_message(message, severity)
        if not cleaned:
            return
        self._notification_history.append(
            NotificationHistoryEntry(message=message, severity=severity)
        )
        if len(self._notification_history) > self._notification_history_limit:
            self._notification_history = self._notification_history[
                -self._notification_history_limit :
            ]

    def get_notification_history_lines(self) -> list[str]:
        return [
            self._prepare_status_message(entry.message, entry.severity)
            for entry in self._notification_history
        ]

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
        if not message:
            return
        if update_latest:
            self._latest_status_source_message = message
            self._latest_status_severity = severity
            self._latest_status_message = message
            try:
                self.query_one(StatusDisplay).latest_status = message
            except Exception:
                pass
        super().notify(
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
        prefix = self._notification_title(severity)
        cleaned = self._prepare_status_message(message, severity)
        self._record_notification_history(message, severity)
        self._dispatch_notification(
            cleaned,
            title=title or prefix,
            severity=severity,
            timeout=timeout,
            markup=markup and not self.screen_reader_mode,
            update_latest=True,
        )

    def action_repeat_latest_status(self) -> None:
        if not self._latest_status_message:
            self.notify("No status messages yet.", severity="warning", timeout=2)
            return
        self._dispatch_notification(
            self._latest_status_message,
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
        if not message or not self.is_runtime_active():
            return
        if not batch:
            self.notify(message, severity=severity, timeout=timeout)
            return

        self._record_notification_history(message, severity)
        entry = BufferedNotification(message=message, severity=severity, timeout=timeout)
        if self._notification_buffer and self._notification_buffer[-1] == entry:
            return
        self._notification_buffer.append(entry)
        if self._notification_timer is None:
            self._notification_timer = self.set_timer(0.18, self._flush_buffered_notifications)

    def _flush_buffered_notifications(self) -> None:
        self._notification_timer = None
        if not self._notification_buffer or not self.is_runtime_active():
            self._notification_buffer.clear()
            return

        buffered = self._notification_buffer
        self._notification_buffer = []
        if len(buffered) == 1:
            item = buffered[0]
            self._dispatch_notification(
                self._prepare_status_message(item.message, item.severity),
                title=self._notification_title(item.severity),
                severity=item.severity,
                timeout=item.timeout,
                markup=not self.screen_reader_mode,
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
            markup=not self.screen_reader_mode,
            update_latest=True,
        )
