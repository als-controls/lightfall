"""Bug report dialog for Lightfall.

Allows users to submit bug reports to Sentry/GlitchTip with optional
error context from recent errors.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from lightfall.ui.dialogs.base import LFDialog
from lightfall.utils.logging import logger
from lightfall.utils.threads import QThreadFuture

if TYPE_CHECKING:
    from lightfall.utils.error_collector import ErrorRecord


class BugPriority(Enum):
    """Priority levels for bug reports.

    Maps to Sentry severity levels for filtering and alerting.
    """

    LOW = "low"  # -> Sentry "info"
    NORMAL = "normal"  # -> Sentry "warning"
    HIGH = "high"  # -> Sentry "error"
    CRITICAL = "critical"  # -> Sentry "fatal"

    @property
    def sentry_level(self) -> str:
        """Get the corresponding Sentry severity level.

        Returns:
            Sentry level string.
        """
        mapping = {
            BugPriority.LOW: "info",
            BugPriority.NORMAL: "warning",
            BugPriority.HIGH: "error",
            BugPriority.CRITICAL: "fatal",
        }
        return mapping[self]


class BugReportDialog(LFDialog):
    """Dialog for submitting bug reports to Sentry/GlitchTip.

    Features:
    - Optional selection of a recent error for context
    - Error preview when selected
    - User description (required)
    - Priority selection
    - Background submission

    Signals:
        report_submitted: Emitted when report is successfully submitted.
            Argument is the Sentry event ID.
    """

    report_submitted = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the bug report dialog.

        Args:
            parent: Parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("Report a Bug")
        self.setModal(True)
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)

        self._selected_error: ErrorRecord | None = None
        self._errors: list[ErrorRecord] = []
        self._submitting = False
        self._submit_thread: QThreadFuture | None = None

        self._setup_ui()
        self._load_recent_errors()

    def _setup_ui(self) -> None:
        """Setup the dialog UI."""
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # Recent error selection
        error_label = QLabel("Recent error (optional):")
        layout.addWidget(error_label)

        self._error_combo = QComboBox()
        self._error_combo.addItem("(None - report without error context)", None)
        self._error_combo.currentIndexChanged.connect(self._on_error_selected)
        layout.addWidget(self._error_combo)

        # Error preview (shown when error selected)
        self._preview_group = QGroupBox("Error Details")
        self._preview_group.setVisible(False)
        preview_layout = QVBoxLayout(self._preview_group)

        self._preview_text = QTextEdit()
        self._preview_text.setReadOnly(True)
        self._preview_text.setMaximumHeight(120)
        self._preview_text.setStyleSheet("font-family: monospace; font-size: 11px;")
        preview_layout.addWidget(self._preview_text)

        layout.addWidget(self._preview_group)

        # Description
        desc_label = QLabel("Description (required):")
        layout.addWidget(desc_label)

        self._description_text = QTextEdit()
        self._description_text.setPlaceholderText(
            "Please describe what you were doing when the issue occurred, "
            "and what you expected to happen..."
        )
        self._description_text.setMinimumHeight(100)
        self._description_text.textChanged.connect(self._update_submit_enabled)
        layout.addWidget(self._description_text)

        # Priority selection
        priority_group = QGroupBox("Priority")
        priority_layout = QHBoxLayout(priority_group)

        self._priority_group = QButtonGroup(self)
        self._priority_buttons: dict[BugPriority, QRadioButton] = {}

        for priority in BugPriority:
            radio = QRadioButton(priority.name.capitalize())
            self._priority_group.addButton(radio)
            self._priority_buttons[priority] = radio
            priority_layout.addWidget(radio)

            # Default to Normal
            if priority == BugPriority.NORMAL:
                radio.setChecked(True)

        layout.addWidget(priority_group)

        # Add stretch to push buttons to bottom
        layout.addStretch()

        # Button box
        self._button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
        )

        # Use ActionRole to prevent auto-close behavior of AcceptRole
        self._submit_button = self._button_box.addButton(
            "Submit", QDialogButtonBox.ButtonRole.ActionRole
        )
        self._submit_button.setEnabled(False)  # Disabled until description entered

        self._button_box.rejected.connect(self.reject)
        self._submit_button.clicked.connect(self._on_submit)

        layout.addWidget(self._button_box)

    def _load_recent_errors(self) -> None:
        """Load recent errors into the combo box."""
        from lightfall.utils.error_collector import get_error_collector

        collector = get_error_collector()
        self._errors = collector.get_recent_errors(max_count=20)

        for error in self._errors:
            # Format: "HH:MM:SS - short message"
            display_text = f"{error.display_time} - {error.short_message}"
            self._error_combo.addItem(display_text, error)

    def _on_error_selected(self, index: int) -> None:
        """Handle error selection change.

        Args:
            index: The selected combo box index.
        """
        self._selected_error = self._error_combo.itemData(index)

        if self._selected_error is not None:
            # Show preview
            self._preview_group.setVisible(True)
            preview_text = self._format_error_preview(self._selected_error)
            self._preview_text.setPlainText(preview_text)
        else:
            # Hide preview
            self._preview_group.setVisible(False)
            self._preview_text.clear()

    def _format_error_preview(self, error: ErrorRecord) -> str:
        """Format an error record for preview display.

        Args:
            error: The error record to format.

        Returns:
            Formatted preview text.
        """
        lines = [
            f"Time: {error.display_time}",
            f"Level: {error.level}",
            f"Location: {error.location}",
            "",
            f"Message: {error.message}",
        ]

        if error.exception_info:
            lines.extend(["", "Exception:", error.exception_info[:500]])
            if len(error.exception_info) > 500:
                lines.append("... (truncated)")

        return "\n".join(lines)

    def _update_submit_enabled(self) -> None:
        """Update submit button enabled state based on description."""
        has_description = bool(self._description_text.toPlainText().strip())
        self._submit_button.setEnabled(has_description and not self._submitting)

    def _get_selected_priority(self) -> BugPriority:
        """Get the selected priority.

        Returns:
            The selected BugPriority enum value.
        """
        for priority, radio in self._priority_buttons.items():
            if radio.isChecked():
                return priority
        return BugPriority.NORMAL

    def _on_submit(self) -> None:
        """Handle submit button click."""
        description = self._description_text.toPlainText().strip()
        if not description:
            return

        if self._submitting:
            return  # Prevent double-submission

        self._submitting = True
        self._submit_button.setEnabled(False)
        self._submit_button.setText("Submitting...")

        # Get values for submission
        priority = self._get_selected_priority()
        error = self._selected_error

        # Submit in background thread
        def do_submit() -> str | None:
            from lightfall.utils.sentry import submit_bug_report

            return submit_bug_report(
                description=description,
                error_record=error,
                priority=priority.value,
            )

        self._submit_thread = QThreadFuture(
            do_submit,
            callback_slot=self._on_submit_complete,
            except_slot=self._on_submit_error,
            name="bug_report_submit",
        )
        self._submit_thread.start()

    def _on_submit_complete(self, event_id: str | None) -> None:
        """Handle successful submission.

        Args:
            event_id: The Sentry event ID, or None if submission failed.
        """
        self._submitting = False

        if event_id:
            logger.info("Bug report submitted: {}", event_id)
            self.report_submitted.emit(event_id)

            # Show success toast
            from lightfall.ui.toast import ToastManager

            toast = ToastManager.get_instance()
            toast.success(
                "Bug Report Submitted",
                f"Thank you for your feedback! (Event: {event_id[:8]}...)",
            )

            self.accept()
        else:
            # Submission returned None (Sentry not initialized)
            logger.warning("Bug report submission failed: Sentry not initialized")
            from lightfall.ui.toast import ToastManager

            toast = ToastManager.get_instance()
            toast.error(
                "Submission Failed",
                "Error reporting is not configured. Please contact support directly.",
            )

            self._submit_button.setText("Submit")
            self._update_submit_enabled()

    def _on_submit_error(self, error: Exception) -> None:
        """Handle submission error.

        Args:
            error: The exception that occurred.
        """
        self._submitting = False
        self._submit_button.setText("Submit")
        self._update_submit_enabled()

        logger.error("Bug report submission failed: {}", error)

        from lightfall.ui.toast import ToastManager

        toast = ToastManager.get_instance()
        toast.error(
            "Submission Failed",
            f"Could not submit bug report: {error}",
        )


def report_bug(parent: QWidget | None = None) -> bool:
    """Show the bug report dialog.

    Convenience function to show the dialog and return whether a report
    was submitted.

    Args:
        parent: Parent widget for the dialog.

    Returns:
        True if a report was submitted, False if cancelled.
    """
    dialog = BugReportDialog(parent)
    result = dialog.exec()
    return result == LFDialog.DialogCode.Accepted
