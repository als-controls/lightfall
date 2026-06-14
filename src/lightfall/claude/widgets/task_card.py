"""Inline card for one Task tool subagent run.

Updated in place across TaskStartedMessage / TaskProgressMessage /
TaskNotificationMessage. The card lives in the chat flow at the spot
where the subagent was dispatched, so the chronological reading order
matches what actually happened.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from lightfall.ui.theme import scaled_pt

try:
    import qtawesome as qta
except ImportError:  # pragma: no cover - qtawesome is a hard dep elsewhere
    qta = None  # type: ignore[assignment]


class TaskCard(QFrame):
    """Live status card for a single Task subagent run."""

    STATUS_ICONS: dict[str, tuple[str, str]] = {
        "running": ("mdi.loading", "#5fa8d3"),
        "completed": ("mdi.check-circle", "#4caf50"),
        "failed": ("mdi.alert-circle", "#f44336"),
        "stopped": ("mdi.stop-circle", "#ff9800"),
    }

    def __init__(
        self,
        task_id: str,
        description: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.task_id = task_id
        self._expanded = False
        self._description = description
        self._summary = ""
        self._output_file = ""
        self._last_tool = ""
        self._usage: dict = {}
        self._status = "running"

        self._setup_ui()
        self._apply_theme_style()
        self._refresh()

    def _setup_ui(self) -> None:
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # --- Header row -----------------------------------------------------
        header_row = QHBoxLayout()
        header_row.setSpacing(6)

        self.toggle_btn = QPushButton("▶")
        self.toggle_btn.setFixedSize(20, 20)
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setStyleSheet("QPushButton { border: none; padding: 0; }")
        self.toggle_btn.clicked.connect(self._toggle)
        header_row.addWidget(self.toggle_btn)

        self.status_label = QLabel()
        self.status_label.setFixedSize(16, 16)
        header_row.addWidget(self.status_label)

        self.title_label = QLabel()
        self.title_label.setTextFormat(Qt.TextFormat.RichText)
        self.title_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        header_row.addWidget(self.title_label, 1)

        self.counter_label = QLabel()
        self.counter_label.setStyleSheet(f"color: gray; font-size: {scaled_pt(9)}pt;")
        header_row.addWidget(self.counter_label)

        layout.addLayout(header_row)

        # --- Expanded details ----------------------------------------------
        self.details_widget = QWidget()
        self.details_widget.setVisible(False)
        d = QVBoxLayout(self.details_widget)
        d.setContentsMargins(28, 4, 0, 0)
        d.setSpacing(2)

        self.detail_description = QLabel()
        self.detail_description.setWordWrap(True)
        self.detail_description.setTextFormat(Qt.TextFormat.PlainText)
        d.addWidget(self.detail_description)

        self.detail_last_tool = QLabel()
        self.detail_last_tool.setStyleSheet(f"color: gray; font-size: {scaled_pt(9)}pt;")
        d.addWidget(self.detail_last_tool)

        self.detail_summary = QLabel()
        self.detail_summary.setWordWrap(True)
        self.detail_summary.setTextFormat(Qt.TextFormat.PlainText)
        d.addWidget(self.detail_summary)

        self.output_link = QLabel()
        self.output_link.setOpenExternalLinks(True)
        d.addWidget(self.output_link)

        layout.addWidget(self.details_widget)

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self.details_widget.setVisible(self._expanded)
        self.toggle_btn.setText("▼" if self._expanded else "▶")

    # --- Public update API --------------------------------------------------

    def update_progress(
        self,
        description: str,
        usage: dict,
        last_tool: str,
    ) -> None:
        self._description = description
        if usage:
            self._usage = dict(usage)
        self._last_tool = last_tool
        self._refresh()

    def mark_finished(
        self,
        status: str,
        summary: str,
        output_file: str,
        usage: dict,
    ) -> None:
        self._status = status if status in self.STATUS_ICONS else "completed"
        self._summary = summary
        self._output_file = output_file
        if usage:
            self._usage = dict(usage)
        self._refresh()

    # --- Rendering ----------------------------------------------------------

    def _refresh(self) -> None:
        if qta is not None:
            icon_name, color = self.STATUS_ICONS.get(
                self._status, ("mdi.help", "gray")
            )
            if self._status == "running":
                spin_icon = qta.icon(
                    icon_name, color=color, animation=qta.Spin(self.status_label)
                )
                self.status_label.setPixmap(spin_icon.pixmap(16, 16))
            else:
                self.status_label.setPixmap(
                    qta.icon(icon_name, color=color).pixmap(16, 16)
                )

        truncated = self._truncate(self._description, 60)
        self.title_label.setText(f"<b>Task:</b> {self._escape(truncated)}")

        tokens = self._usage.get("total_tokens", 0)
        tools = self._usage.get("tool_uses", 0)
        if tokens or tools:
            self.counter_label.setText(f"{tokens:,} tokens · {tools} tools")
        else:
            self.counter_label.setText("")

        self.detail_description.setText(self._description)
        self.detail_last_tool.setText(
            f"Last tool: {self._escape(self._last_tool)}" if self._last_tool else ""
        )
        self.detail_summary.setText(self._summary)
        if self._output_file:
            escaped = self._escape(self._output_file)
            self.output_link.setText(
                f'<a href="file://{escaped}">\U0001f4c4 Open transcript</a>'
            )
        else:
            self.output_link.setText("")

    def _apply_theme_style(self) -> None:
        palette = self.palette()
        is_dark = palette.color(QPalette.ColorRole.Base).lightness() < 128
        if is_dark:
            bg = "rgba(40, 60, 80, 0.4)"
            border = "rgba(80, 120, 160, 0.5)"
        else:
            bg = "rgba(220, 230, 245, 0.55)"
            border = "rgba(120, 160, 200, 0.5)"
        self.setStyleSheet(
            f"""
            TaskCard {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 6px;
            }}
            """
        )

    @staticmethod
    def _escape(text: str) -> str:
        return (
            text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )

    @staticmethod
    def _truncate(text: str, n: int) -> str:
        return text if len(text) <= n else text[: n - 1] + "…"
