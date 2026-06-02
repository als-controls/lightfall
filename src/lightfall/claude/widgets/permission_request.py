"""Inline widget for tool permission approval requests."""

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class PermissionRequestWidget(QFrame):
    """
    Compact inline widget for tool permission approval.

    Displays a single-row permission request with tool info and buttons,
    with an expandable details section for full input information.

    Signals:
        allowed(str, bool): Emitted when user clicks Allow/Always Allow
                           (request_id, always_allow)
        denied(str, str): Emitted when user clicks Deny
                         (request_id, reason)
    """

    allowed = Signal(str, bool)  # (request_id, always_allow)
    denied = Signal(str, str)    # (request_id, reason)

    def __init__(
        self,
        request_id: str,
        tool_name: str,
        tool_input: dict,
        parent: QWidget | None = None
    ):
        super().__init__(parent)
        self.request_id = request_id
        self.tool_name = tool_name
        self.tool_input = tool_input
        self._is_resolved = False
        self._details_expanded = False

        self._setup_ui()
        self._apply_theme_style()

    def _setup_ui(self) -> None:
        """Setup the compact UI with expandable details."""
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 6, 8, 6)
        main_layout.setSpacing(4)

        # === Compact row: info + buttons ===
        compact_row = QHBoxLayout()
        compact_row.setSpacing(8)

        # Tool info: "🔧 click_widget(button_name)"
        tool_display = self._format_tool_name(self.tool_name)
        input_summary = self._format_input_summary(self.tool_input)

        if input_summary:
            info_text = f"\U0001F527 <b>{tool_display}</b>({input_summary})"
        else:
            info_text = f"\U0001F527 <b>{tool_display}</b>"

        self.info_label = QLabel(info_text)
        self.info_label.setTextFormat(Qt.TextFormat.RichText)
        compact_row.addWidget(self.info_label, 1)

        # Details toggle (only show if there's input to display)
        if self.tool_input:
            self.details_btn = QPushButton("\u25B6")  # ▶
            self.details_btn.setFixedSize(20, 20)
            self.details_btn.setToolTip("Show details")
            self.details_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.details_btn.setStyleSheet("QPushButton { border: none; padding: 0; }")
            self.details_btn.clicked.connect(self._toggle_details)
            compact_row.addWidget(self.details_btn)
        else:
            self.details_btn = None

        # Action buttons
        self.allow_btn = QPushButton("\u2713 Allow")
        self.allow_btn.setFixedHeight(24)
        self.allow_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.allow_btn.clicked.connect(self._on_allow)
        compact_row.addWidget(self.allow_btn)

        self.deny_btn = QPushButton("\u2717 Deny")
        self.deny_btn.setFixedHeight(24)
        self.deny_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.deny_btn.clicked.connect(self._on_deny)
        compact_row.addWidget(self.deny_btn)

        self.always_btn = QPushButton("\u221E Always")
        self.always_btn.setFixedHeight(24)
        self.always_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.always_btn.setToolTip("Allow this tool for the rest of the session")
        self.always_btn.clicked.connect(self._on_always_allow)
        compact_row.addWidget(self.always_btn)

        main_layout.addLayout(compact_row)

        # === Details section (hidden by default) ===
        self.details_widget = QWidget()
        self.details_widget.setVisible(False)
        details_layout = QVBoxLayout(self.details_widget)
        details_layout.setContentsMargins(0, 4, 0, 0)
        details_layout.setSpacing(4)

        # Full tool name
        full_name_label = QLabel(f"<b>Tool:</b> {self.tool_name}")
        full_name_label.setTextFormat(Qt.TextFormat.RichText)
        details_layout.addWidget(full_name_label)

        # Full input display
        if self.tool_input:
            input_label = QLabel("<b>Input:</b>")
            details_layout.addWidget(input_label)

            self.input_display = QTextEdit()
            self.input_display.setReadOnly(True)
            self.input_display.setPlainText(self._format_full_input(self.tool_input))
            self.input_display.setMaximumHeight(120)
            self.input_display.setStyleSheet(
                "QTextEdit { background-color: rgba(128, 128, 128, 0.1); "
                "border: 1px solid rgba(128, 128, 128, 0.3); border-radius: 4px; "
                "font-family: monospace; font-size: 9pt; }"
            )
            details_layout.addWidget(self.input_display)

        main_layout.addWidget(self.details_widget)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _toggle_details(self) -> None:
        """Toggle the details section visibility."""
        self._details_expanded = not self._details_expanded
        self.details_widget.setVisible(self._details_expanded)

        if self.details_btn:
            if self._details_expanded:
                self.details_btn.setText("\u25BC")  # ▼
                self.details_btn.setToolTip("Hide details")
            else:
                self.details_btn.setText("\u25B6")  # ▶
                self.details_btn.setToolTip("Show details")

    def _format_tool_name(self, name: str) -> str:
        """Format tool name for display (strip MCP prefixes)."""
        if name.startswith("mcp__"):
            parts = name.split("__")
            if len(parts) >= 3:
                return parts[-1]
        return name

    def _format_input_summary(self, tool_input: dict) -> str:
        """Format tool input as a brief summary."""
        if not tool_input:
            return ""

        # For single-key inputs, show the value directly
        if len(tool_input) == 1:
            value = list(tool_input.values())[0]
            if isinstance(value, str):
                if len(value) > 30:
                    return f'"{value[:27]}..."'
                return f'"{value}"'
            return str(value)

        # For multiple keys, show count
        return f"{len(tool_input)} params"

    def _format_full_input(self, tool_input: dict) -> str:
        """Format tool input as full JSON for details view."""
        if not tool_input:
            return "(no input)"
        try:
            return json.dumps(tool_input, indent=2, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(tool_input)

    def _apply_theme_style(self) -> None:
        """Apply theme-aware styling."""
        palette = self.palette()
        is_dark = palette.color(QPalette.ColorRole.Base).lightness() < 128

        if is_dark:
            bg = "rgba(60, 90, 120, 0.35)"
            border = "rgba(100, 150, 200, 0.5)"
        else:
            bg = "rgba(200, 220, 240, 0.5)"
            border = "rgba(100, 150, 200, 0.6)"

        self.setStyleSheet(f"""
            PermissionRequestWidget {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 6px;
            }}
            QPushButton {{
                padding: 2px 8px;
                border-radius: 4px;
            }}
        """)

    def _on_allow(self) -> None:
        if self._is_resolved:
            return
        self._is_resolved = True
        self.allowed.emit(self.request_id, False)
        self._show_resolved_state("allowed")

    def _on_deny(self) -> None:
        if self._is_resolved:
            return
        self._is_resolved = True
        self.denied.emit(self.request_id, "User denied permission")
        self._show_resolved_state("denied")

    def _on_always_allow(self) -> None:
        if self._is_resolved:
            return
        self._is_resolved = True
        self.allowed.emit(self.request_id, True)
        self._show_resolved_state("always")

    def _show_resolved_state(self, status: str) -> None:
        """Update to show resolved state."""
        self.allow_btn.setEnabled(False)
        self.deny_btn.setEnabled(False)
        self.always_btn.setEnabled(False)
        if self.details_btn:
            self.details_btn.setEnabled(False)

        palette = self.palette()
        is_dark = palette.color(QPalette.ColorRole.Base).lightness() < 128

        if status == "denied":
            bg = "rgba(120, 60, 60, 0.3)" if is_dark else "rgba(255, 200, 200, 0.5)"
            border = "rgba(200, 100, 100, 0.5)" if is_dark else "rgba(200, 100, 100, 0.6)"
            self.info_label.setText(f"\u2717 <s>{self.info_label.text()[2:]}</s>")
        else:
            bg = "rgba(60, 120, 60, 0.3)" if is_dark else "rgba(200, 255, 200, 0.5)"
            border = "rgba(100, 200, 100, 0.5)" if is_dark else "rgba(100, 200, 100, 0.6)"
            symbol = "\u221E" if status == "always" else "\u2713"
            self.info_label.setText(f"{symbol} {self.info_label.text()[2:]}")

        self.setStyleSheet(f"""
            PermissionRequestWidget {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 6px;
            }}
        """)

        # Collapse details on resolution
        if self._details_expanded:
            self._toggle_details()

    def keyPressEvent(self, event) -> None:
        if self._is_resolved:
            super().keyPressEvent(event)
            return

        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._on_allow()
        elif event.key() == Qt.Key.Key_Escape:
            self._on_deny()
        elif event.key() == Qt.Key.Key_D and self.details_btn:
            self._toggle_details()
        else:
            super().keyPressEvent(event)
