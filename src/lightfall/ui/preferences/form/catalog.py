"""Full-width table editors: a filter box above a plain, themed table.

``TableEditor`` is the shared base (filter, read-only text cells, height that
fits the shown rows so the page scrolls instead of the table). ``CatalogEditor``
edits an item-merged ``{id: bool}`` key: one row per discovered item with a
"Not set / On / Off" combo for the shown scope and a muted "Inherited" column
saying what broader scopes or the code default give the item.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from qtpy.QtCore import QEvent, Qt
from qtpy.QtGui import QBrush
from qtpy.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHeaderView,
    QLineEdit,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lightfall.ui.preferences.form.editors import (
    NO_CLEAR_MENU,
    ChoiceEditor,
    FieldEditor,
    muted_color,
)
from lightfall.ui.preferences.form.schema import CatalogItem, EditorSpec

HERE_HEADER = "Here"
INHERITED_HEADER = "Inherited"
DEFAULT_ORIGIN = "default"
PROTECTED_TEXT = "On (always)"
ON_OFF_OPTIONS: tuple[tuple[bool, str], ...] = ((True, "On"), (False, "Off"))


def on_off_text(value: Any) -> str:
    if value is True:
        return "On"
    if value is False:
        return "Off"
    return str(value)


class TableEditor(FieldEditor):
    """Base for full-width editors: filter line edit above a QTableWidget.

    Subclasses fill the table and say which text a row is filtered by
    (``_row_filter_text``); muted cells are re-coloured on theme changes.
    Columns fit their contents except ``stretch_column``, which takes the
    remaining width (and is the one cut short when the page is narrow).
    """

    full_width = True

    def __init__(
        self, headers: Sequence[str], filter_placeholder: str, stretch_column: int = 0, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.filter_edit = QLineEdit(self)
        self.filter_edit.setPlaceholderText(filter_placeholder)
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setProperty(NO_CLEAR_MENU, True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter_edit)
        self.table = QTableWidget(0, len(headers), self)
        self.table.setHorizontalHeaderLabels(list(headers))
        self.table.verticalHeader().hide()
        self.table.setWordWrap(False)  # long text elides (full text in the tooltip); rows stay one line
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # The height fits the rows, so neither scrollbar is shown: a horizontal
        # one would cover the last row. The stretch column elides instead.
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(stretch_column, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        self._muted_cells: list[QTableWidgetItem] = []

    # Building -----------------------------------------------------------
    def _text_cell(self, row: int, column: int, text: str, muted: bool = False) -> QTableWidgetItem:
        cell = self.table.item(row, column)
        if cell is None:
            cell = QTableWidgetItem()
            cell.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, column, cell)
        cell.setText(text)
        cell.setToolTip(text)
        if muted and cell not in self._muted_cells:
            self._muted_cells.append(cell)
            cell.setForeground(QBrush(muted_color(self.table)))
        return cell

    def _finish_rows(self) -> None:
        """Call after filling the table: size rows to their widgets and fit the height."""
        self.table.resizeRowsToContents()
        self._fit_height()

    def _fit_height(self) -> None:
        rows = sum(self.table.rowHeight(r) for r in range(self.table.rowCount()) if not self.table.isRowHidden(r))
        frame = 2 * self.table.frameWidth()
        self.table.setFixedHeight(self.table.horizontalHeader().sizeHint().height() + rows + frame)

    # Filtering ----------------------------------------------------------
    def _row_filter_text(self, row: int) -> str:
        cell = self.table.item(row, 0)
        return cell.text() if cell is not None else ""

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().casefold()
        for row in range(self.table.rowCount()):
            self.table.setRowHidden(row, bool(needle) and needle not in self._row_filter_text(row).casefold())
        self._fit_height()

    # Theme --------------------------------------------------------------
    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "_muted_cells"):
            brush = QBrush(muted_color(self.table))
            for cell in self._muted_cells:
                cell.setForeground(brush)
        super().changeEvent(event)


class CatalogEditor(TableEditor):
    """Table of ``spec.items()``; ``value()`` is the scope's own ``{id: bool}``, None when empty.

    Stored entries for ids the catalog does not list (an uninstalled plugin)
    are kept as they are. Protected items show a disabled, fixed "On" and are
    never written as Off.
    """

    def __init__(self, spec: EditorSpec, parent: QWidget | None = None) -> None:
        self._columns = tuple(spec.columns)
        # The last extra column (a description, say) gives way first; else the name.
        super().__init__(
            ("Name", *self._columns, HERE_HEADER, INHERITED_HEADER), "Filter by name", len(self._columns), parent
        )
        self._items: list[CatalogItem] = list(spec.items() if spec.items is not None else ())
        self._combos: dict[str, ChoiceEditor] = {}
        self._stored: dict[str, Any] = {}
        self._inherited: dict[str, tuple[Any, str]] = {}
        self._loading = False
        self._here_column = 1 + len(self._columns)
        self._inherited_column = self._here_column + 1
        self.table.setRowCount(len(self._items))
        for row, item in enumerate(self._items):
            self._text_cell(row, 0, item.label).setToolTip(f"{item.label} ({item.id})")
            for offset in range(len(self._columns)):
                text = item.columns[offset] if offset < len(item.columns) else ""
                self._text_cell(row, 1 + offset, text)
            choice = ChoiceEditor(EditorSpec("choice", options=ON_OFF_OPTIONS))
            if item.protected:
                choice.set_value(True)
                choice.set_read_only(True)
                choice.setToolTip("Always on")
            choice.changed.connect(self._on_item_changed)
            self.table.setCellWidget(row, self._here_column, choice)
            self._combos[item.id] = choice
        self._show_inherited()
        self._finish_rows()

    def _row_filter_text(self, row: int) -> str:
        item = self._items[row]
        return f"{item.label}\n{item.id}"

    def _on_item_changed(self) -> None:
        if not self._loading:
            self.changed.emit()

    def _show_inherited(self) -> None:
        for row, item in enumerate(self._items):
            if item.protected:  # broader layers cannot turn it off
                text = PROTECTED_TEXT
            else:
                value, origin = self._inherited.get(item.id, (item.default_enabled, DEFAULT_ORIGIN))
                text = f"{on_off_text(value)} ({origin})"
            self._text_cell(row, self._inherited_column, text, muted=True)

    # Catalog API --------------------------------------------------------
    def item_combo(self, item_id: str) -> QComboBox:
        """The "Here" combo of ``item_id`` (tests and screenshots)."""
        return self._combos[item_id].findChild(QComboBox)

    def set_inherited(self, inherited: dict[str, tuple[Any, str]]) -> None:
        """Show what broader scopes give each item: id -> (value, origin label)."""
        self._inherited = dict(inherited)
        self._show_inherited()

    # FieldEditor --------------------------------------------------------
    def value(self) -> dict[str, Any] | None:
        known = {item.id: item for item in self._items}
        out = {k: v for k, v in self._stored.items() if k not in known}
        for item in self._items:
            if item.protected:
                if self._stored.get(item.id) is True:
                    out[item.id] = True
                continue
            value = self._combos[item.id].value()
            if value is not None:
                out[item.id] = value
        return out or None

    def set_value(self, value: Any) -> None:
        self._stored = dict(value) if isinstance(value, dict) else {}
        self._loading = True
        try:
            for item in self._items:
                if not item.protected:
                    self._combos[item.id].set_value(self._stored.get(item.id))
        finally:
            self._loading = False

    def set_read_only(self, read_only: bool) -> None:
        for item in self._items:
            if not item.protected:
                self._combos[item.id].set_read_only(read_only)
