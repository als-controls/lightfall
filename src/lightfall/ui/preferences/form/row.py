"""One settings row: label and description on the left, editor on the right.

Markers (accent bar when set at the scope, dirty dot, lock and reason,
restart tag, conflict note) are painted from the widget palette so they
follow the theme.
"""

from __future__ import annotations

from qtpy.QtCore import QEvent, Qt, Signal
from qtpy.QtGui import QAction, QPainter, QPalette
from qtpy.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lightfall.ui.preferences.form.editors import (
    _TEXT_ROLES,
    FieldEditor,
    _error_color,
    _muted_color,
    _RoleColor,
)
from lightfall.utils.logging import logger

ACCENT_WIDTH = 3
DOT_SIZE = 8
ROW_MARGIN_V = 6
ROW_SPACING = 12
TEXT_SPACING = 2
LABEL_COLUMN_MIN = 220
EDITOR_COLUMN_MIN = 240
LOCK_ICON_SIZE = 14

CONFLICT_TEXT = "Changed elsewhere since you opened this"
RESTART_TEXT = "restart"
LOCK_GLYPH = "\U0001f512"


def set_muted(label: QLabel) -> None:
    _RoleColor(label, _TEXT_ROLES, _muted_color)


class _AccentBar(QWidget):
    """Thin bar on the row's left edge, filled with Highlight when active."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(ACCENT_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.active = False

    def set_active(self, active: bool) -> None:
        if active != self.active:
            self.active = active
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        if self.active:
            painter = QPainter(self)
            painter.fillRect(self.rect(), self.palette().color(QPalette.ColorRole.Highlight))


class _DirtyDot(QWidget):
    """Small Highlight-coloured dot shown while a field has unsaved edits."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(DOT_SIZE, DOT_SIZE)
        self.setToolTip("Unsaved change")

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.palette().color(QPalette.ColorRole.Highlight))
        painter.drawEllipse(self.rect().adjusted(1, 1, -1, -1))


def _lock_label(parent: QWidget) -> QLabel:
    label = QLabel(parent)
    try:
        import qtawesome as qta

        color = parent.palette().color(QPalette.ColorRole.WindowText)
        label.setPixmap(qta.icon("mdi6.lock", color=color).pixmap(LOCK_ICON_SIZE, LOCK_ICON_SIZE))
    except Exception as exc:  # qtawesome missing or font not loadable
        logger.debug("lock icon unavailable, using glyph: {}", exc)
        label.setText(LOCK_GLYPH)
    return label


class SettingRow(QWidget):
    """A field's row; the form owns the model and pushes state in."""

    clear_requested = Signal()

    def __init__(
        self,
        key: str,
        label: str,
        description: str,
        editor: FieldEditor,
        restart_required: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.key = key
        self.editor = editor
        self._read_only = False
        self._invalid = False

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, ROW_MARGIN_V, 0, ROW_MARGIN_V)
        outer.setSpacing(ROW_SPACING)
        self._accent = _AccentBar(self)
        outer.addWidget(self._accent)

        left = QVBoxLayout()
        left.setSpacing(TEXT_SPACING)
        title = QHBoxLayout()
        title.setSpacing(ROW_SPACING // 2)
        self._label = QLabel(label, self)
        title.addWidget(self._label)
        self._dot = _DirtyDot(self)
        self._dot.hide()
        title.addWidget(self._dot)
        self._restart = QLabel(RESTART_TEXT, self)
        self._restart.setToolTip("Takes effect after a restart")
        set_muted(self._restart)
        self._restart.setVisible(restart_required)
        title.addWidget(self._restart)
        title.addStretch(1)
        left.addLayout(title)

        self._description = QLabel(description, self)
        self._description.setWordWrap(True)
        set_muted(self._description)
        self._description.setVisible(bool(description))
        left.addWidget(self._description)

        lock_line = QHBoxLayout()
        lock_line.setSpacing(ROW_SPACING // 2)
        self._lock = _lock_label(self)
        lock_line.addWidget(self._lock, 0, Qt.AlignmentFlag.AlignTop)
        self._reason = QLabel(self)
        self._reason.setWordWrap(True)
        set_muted(self._reason)
        lock_line.addWidget(self._reason, 1)
        left.addLayout(lock_line)
        self._lock.hide()
        self._reason.hide()

        self._conflict = QLabel(CONFLICT_TEXT, self)
        self._conflict.setWordWrap(True)
        _RoleColor(self._conflict, _TEXT_ROLES, _error_color)
        self._conflict.hide()
        left.addWidget(self._conflict)

        left_box = QWidget(self)
        left_box.setLayout(left)
        left_box.setMinimumWidth(LABEL_COLUMN_MIN)
        outer.addWidget(left_box, 1)

        editor.setParent(self)
        editor.setMinimumWidth(EDITOR_COLUMN_MIN)
        outer.addWidget(editor, 0, Qt.AlignmentFlag.AlignTop)

        self._menu_button = QToolButton(self)
        self._menu_button.setText("⋯")
        self._menu_button.setToolTip("More actions")
        self._menu_button.setAutoRaise(True)
        self._menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self._menu_button)
        self.clear_action = QAction("Clear", self)
        self.clear_action.setToolTip("Remove this setting at the selected scope")
        self.clear_action.triggered.connect(self.clear_requested)
        menu.addAction(self.clear_action)
        self._menu_button.setMenu(menu)
        policy = self._menu_button.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self._menu_button.setSizePolicy(policy)
        self._menu_button.hide()
        outer.addWidget(self._menu_button, 0, Qt.AlignmentFlag.AlignTop)

    # Hover reveals the row menu.
    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Enter:
            self._menu_button.show()
        elif event.type() == QEvent.Type.Leave and not self._menu_button.menu().isVisible():
            self._menu_button.hide()
        return super().event(event)

    def set_markers(self, is_set: bool, dirty: bool, conflict: bool) -> None:
        self._accent.set_active(is_set)
        self._dot.setVisible(dirty)
        self._conflict.setVisible(conflict)
        self.clear_action.setEnabled(not self._read_only and (is_set or self._invalid))

    def set_read_only(self, reason: str | None) -> None:
        """Make the row read-only with ``reason`` shown inline; None makes it editable."""
        self._read_only = reason is not None
        self.editor.set_read_only(self._read_only)
        self._reason.setText(reason or "")
        self._lock.setVisible(self._read_only)
        self._reason.setVisible(self._read_only)
        if self._read_only:
            self.clear_action.setEnabled(False)

    def set_invalid(self, invalid: bool) -> None:
        self._invalid = invalid

    # Queries (used by the form and tests).
    def is_read_only(self) -> bool:
        return self._read_only

    def is_marked_invalid(self) -> bool:
        return self._invalid

    def is_marked_set(self) -> bool:
        return self._accent.active

    def is_marked_dirty(self) -> bool:
        return not self._dot.isHidden()

    def has_conflict_note(self) -> bool:
        return not self._conflict.isHidden()

    def has_restart_tag(self) -> bool:
        return not self._restart.isHidden()

    def reason_text(self) -> str:
        return self._reason.text() if self._read_only else ""

    def label_text(self) -> str:
        return self._label.text()
