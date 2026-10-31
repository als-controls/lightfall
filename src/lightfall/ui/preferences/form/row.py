"""One settings row: label and description on the left, editor on the right.

Markers (accent bar when set at the scope, dirty dot, lock and reason,
restart tag, conflict note) are painted from the widget palette so they
follow the theme. The row has no buttons of its own: right-clicking it
offers "Clear (not set)".
"""

from __future__ import annotations

from qtpy.QtCore import QEvent, QObject, QPoint, Qt, Signal
from qtpy.QtGui import QAction, QContextMenuEvent, QPainter, QPalette
from qtpy.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from lightfall.ui.preferences.form.editors import (
    TEXT_ROLES,
    FieldEditor,
    RoleColor,
    error_color,
    muted_color,
)
from lightfall.utils.logging import logger

ACCENT_WIDTH = 3
DOT_SIZE = 8
ROW_MARGIN_V = 6
ROW_SPACING = 12
TEXT_SPACING = 2
LABEL_COLUMN_MIN = 220
EDITOR_COLUMN_MIN = 240
LABEL_STRETCH = 4  # label column : editor column
EDITOR_STRETCH = 5
LOCK_ICON_SIZE = 14

CONFLICT_TEXT = "Changed elsewhere since you opened this"
APPROVAL_TEXT = "Saving here asks a facility admin to approve this setting"
RESTART_TEXT = "restart"
CLEAR_TEXT = "Clear (not set)"
LOCK_GLYPH = "\U0001f512"


def set_muted(label: QLabel) -> None:
    RoleColor(label, TEXT_ROLES, muted_color)


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
        self._is_set = False
        self._needs_approval = False

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
        self._label.setWordWrap(True)  # a long label wraps rather than push the editor column
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

        self._approval = QLabel(APPROVAL_TEXT, self)
        self._approval.setWordWrap(True)
        set_muted(self._approval)
        self._approval.hide()
        left.addWidget(self._approval)

        self._conflict = QLabel(CONFLICT_TEXT, self)
        self._conflict.setWordWrap(True)
        RoleColor(self._conflict, TEXT_ROLES, error_color)
        self._conflict.hide()
        left.addWidget(self._conflict)

        # Label and editor columns split the row by stretch alone (their size
        # hints are ignored), so every row of a form puts its editors at the
        # same x. Free-text editors fill their column; the rest keep their
        # natural width, left-aligned in it.
        left_box = QWidget(self)
        left_box.setLayout(left)
        left_box.setMinimumWidth(LABEL_COLUMN_MIN)
        left_box.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        outer.addWidget(left_box, LABEL_STRETCH)

        editor_box = QWidget(self)
        editor_box.setMinimumWidth(EDITOR_COLUMN_MIN)
        editor_box.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        editor_line = QHBoxLayout(editor_box)
        editor_line.setContentsMargins(0, 0, 0, 0)
        editor.setParent(editor_box)
        editor.setMinimumWidth(EDITOR_COLUMN_MIN)
        if editor.fills_row:
            editor_line.addWidget(editor)
        else:
            editor_line.addWidget(editor, 0, Qt.AlignmentFlag.AlignLeft)
            editor_line.addStretch(1)
        outer.addWidget(editor_box, EDITOR_STRETCH, Qt.AlignmentFlag.AlignTop)

        self.clear_action = QAction(CLEAR_TEXT, self)
        self.clear_action.setToolTip("Remove this setting at the selected scope")
        self.clear_action.triggered.connect(self.clear_requested)
        # Combos swallow context-menu events and line edits have their own
        # menu, so both are filtered: combos show the row menu, line edits
        # get Clear added to their standard one.
        for child in (*editor.findChildren(QComboBox), *editor.findChildren(QLineEdit)):
            child.installEventFilter(self)

    # Right-click offers "Clear (not set)".
    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802
        self._update_clear_action()
        menu = QMenu(self)
        menu.addAction(self.clear_action)
        self._popup(menu, event.globalPos())
        event.accept()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.ContextMenu:
            return super().eventFilter(obj, event)
        if isinstance(obj, QComboBox):
            self.contextMenuEvent(event)
            return True
        if isinstance(obj, QLineEdit):
            self._update_clear_action()
            menu = obj.createStandardContextMenu()
            menu.addSeparator()
            menu.addAction(self.clear_action)
            self._popup(menu, event.globalPos())
            return True
        return super().eventFilter(obj, event)

    @staticmethod
    def _popup(menu: QMenu, pos: QPoint) -> None:
        menu.exec(pos)
        menu.deleteLater()

    def set_markers(self, is_set: bool, dirty: bool, conflict: bool) -> None:
        self._accent.set_active(is_set)
        self._dot.setVisible(dirty)
        self._conflict.setVisible(conflict)
        self._is_set = is_set
        self._update_clear_action()

    def _update_clear_action(self) -> None:
        """Clear is offered only for an editable, enabled field that holds a value (or bad input).

        Called again when a menu opens: ``enabled_when`` can disable the editor
        without the row hearing about it.
        """
        editable = not self._read_only and self.editor.isEnabled()
        self.clear_action.setEnabled(editable and (self._is_set or self._invalid))

    def set_read_only(self, reason: str | None, inline: bool = True) -> None:
        """Make the row read-only for ``reason``; None makes it editable.

        The reason shows next to the lock, or only as the lock's tooltip when
        ``inline`` is False (the scope header already says it).
        """
        self._read_only = reason is not None
        self.editor.set_read_only(self._read_only)
        self._reason.setText(reason or "")
        self._lock.setToolTip(reason or "")
        self._lock.setVisible(self._read_only)
        self._reason.setVisible(self._read_only and inline)
        self._show_approval()
        self._update_clear_action()

    def set_needs_approval(self, needs: bool) -> None:
        """Show the muted "asks a facility admin" note (never on a read-only row)."""
        self._needs_approval = needs
        self._show_approval()

    def _show_approval(self) -> None:
        self._approval.setVisible(self._needs_approval and not self._read_only)

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

    def approval_note_shown(self) -> bool:
        return not self._approval.isHidden()

    def reason_text(self) -> str:
        return self._reason.text() if self._read_only else ""

    def reason_shown_inline(self) -> bool:
        return self._read_only and not self._reason.isHidden()

    def lock_tooltip(self) -> str:
        return self._lock.toolTip() if self._read_only else ""

    def label_text(self) -> str:
        return self._label.text()
