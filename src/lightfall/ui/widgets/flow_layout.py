"""A wrapping flow layout for Qt widgets.

``FlowLayout`` arranges its child items left-to-right, wrapping to a new line
when the available width is exhausted. As the containing widget grows wider,
more items fit per line, so a column of items becomes a grid of columns. This
is the standard Qt "flow layout" pattern (see the Qt ``flowlayout`` example),
ported to PySide6 and exposed for reuse across Lightfall widgets.

Typical use: a panel of fixed-size "field" widgets (label + control) that
should pack into as many columns as the panel width allows.
"""

from __future__ import annotations

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QSizePolicy, QWidget


class FlowLayout(QLayout):
    """A layout that lays items out horizontally and wraps as needed.

    Parameters
    ----------
    parent:
        Optional parent widget.
    margin:
        Uniform content margin in pixels applied on all four sides.
    h_spacing:
        Horizontal spacing between items. ``-1`` uses the widget's default
        layout spacing.
    v_spacing:
        Vertical spacing between rows. ``-1`` uses the widget's default
        layout spacing.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        margin: int = 0,
        h_spacing: int = -1,
        v_spacing: int = -1,
    ) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self.setContentsMargins(QMargins(margin, margin, margin, margin))

    def __del__(self) -> None:  # pragma: no cover - defensive cleanup
        while self._items:
            self._items.pop()

    # -- QLayout overrides -------------------------------------------------

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 (Qt naming)
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def horizontalSpacing(self) -> int:  # noqa: N802
        return self._h_spacing

    def verticalSpacing(self) -> int:  # noqa: N802
        return self._v_spacing

    def expandingDirections(self) -> Qt.Orientations:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(
            margins.left() + margins.right(),
            margins.top() + margins.bottom(),
        )
        return size

    # -- internal ----------------------------------------------------------

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        x = effective.x()
        y = effective.y()
        line_height = 0

        for item in self._items:
            widget = item.widget()
            space_x = self.horizontalSpacing()
            space_y = self.verticalSpacing()
            if widget is not None:
                if space_x < 0:
                    space_x = widget.style().layoutSpacing(
                        QSizePolicy.PushButton,
                        QSizePolicy.PushButton,
                        Qt.Horizontal,
                    )
                if space_y < 0:
                    space_y = widget.style().layoutSpacing(
                        QSizePolicy.PushButton,
                        QSizePolicy.PushButton,
                        Qt.Vertical,
                    )

            item_size = item.sizeHint()
            next_x = x + item_size.width() + space_x
            if next_x - space_x > effective.right() and line_height > 0:
                # Wrap to the next line.
                x = effective.x()
                y = y + line_height + space_y
                next_x = x + item_size.width() + space_x
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item_size))

            x = next_x
            line_height = max(line_height, item_size.height())

        return y + line_height - rect.y() + margins.bottom()
