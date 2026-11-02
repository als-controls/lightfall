"""Full-width table editors: a filter box above a plain, themed table.

``TableEditor`` is the shared base (filter, read-only text cells, height that
fits the shown rows so the page scrolls instead of the table). ``CatalogEditor``
edits an item-merged ``{id: bool}`` key: one row per discovered item with a
"Not set / On / Off" combo for the shown scope and a muted "Inherited" column
saying what broader scopes or the code default give the item.
``MappingEditor`` is the same table for ``{id: value}`` keys: its "Here" cell
is a value editor built from the spec's ``value``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from qtpy.QtCore import QEvent, QObject, Qt
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
    create_editor,
    muted_color,
)
from lightfall.ui.preferences.form.schema import CatalogItem, EditorSpec, SettingField

HERE_HEADER = "Here"
INHERITED_HEADER = "Inherited"
DEFAULT_ORIGIN = "default"
PROTECTED_TEXT = "On (always)"
VALUE_COLUMN_WIDTH = 150  # a mapping row with a line-edit value editor
LINE_EDIT_KINDS = frozenset({"integer", "number", "text", "url", "secret"})
# Beyond the text's advance, a fitted cell needs the theme's item padding (4 px a side) and
# the view's own text margins; the header's size hint does not count the stylesheet padding.
CELL_TEXT_ALLOWANCE = 20
MIN_TEXT_COLUMN = 48  # how narrow an eliding text column may get when the table is short of room
_FIT_TEXT = "text"  # pinned column sized to its widest text (plus the allowance)
_FIT_HINT = "hint"  # pinned column sized to its cell widgets' size hint
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

    Column widths are laid out here, not by the header, so the total never exceeds the
    table (``_layout_columns``, on every resize and content change). Pinned columns (the
    "Here" editor and the "Inherited" text, listed in ``_pinned``) keep their fitted
    width; the other text columns fit their contents, ``stretch_column`` takes any spare
    width, and when room is short they elide (full text in the tooltips): the stretch
    column first, then the others from right to left. Only if even that is not enough do
    the pinned columns give way, right to left.
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
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setMinimumSectionSize(1)
        self._stretch_column = stretch_column
        self._pinned: dict[int, int | str] = {}
        self.table.viewport().installEventFilter(self)
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

    def _item_cells(self, row: int, item: CatalogItem, n_columns: int) -> None:
        """Name and extra text columns of ``item``; muted, with the reason, when it is inactive."""
        muted = bool(item.inactive_reason)
        tip = f"{item.label} ({item.id})"
        if muted:
            tip += "\n" + item.inactive_reason
        self._text_cell(row, 0, item.label, muted=muted).setToolTip(tip)
        for offset in range(n_columns):
            text = item.columns[offset] if offset < len(item.columns) else ""
            self._text_cell(row, 1 + offset, text, muted=muted)

    # Column layout ------------------------------------------------------
    def _text_width(self, column: int) -> int:
        metrics = self.table.fontMetrics()
        texts = [self.table.item(r, column).text() for r in range(self.table.rowCount()) if self.table.item(r, column)]
        return max((metrics.horizontalAdvance(t) for t in texts), default=0) + CELL_TEXT_ALLOWANCE

    def _wanted_width(self, column: int) -> int:
        """The width ``column`` needs to show everything (the header label included)."""
        header_hint = self.table.horizontalHeader().sectionSizeHint(column)
        rule = self._pinned.get(column)
        if isinstance(rule, int):
            return rule
        if rule == _FIT_TEXT:
            return max(self._text_width(column), header_hint)
        return max(self.table.sizeHintForColumn(column), header_hint)

    def _layout_columns(self) -> None:
        count = self.table.columnCount()
        room = self.table.viewport().width()
        widths = {c: self._wanted_width(c) for c in range(count)}
        flexible = [c for c in range(count) if c not in self._pinned]
        spare = room - sum(widths.values())
        if spare >= 0:
            if self._stretch_column in widths:
                widths[self._stretch_column] += spare
        else:
            # Short of room: elide the stretch column, then the other text columns right to
            # left, then (last resort) the pinned columns right to left.
            order = [c for c in flexible if c == self._stretch_column]
            order += [c for c in reversed(flexible) if c != self._stretch_column]
            order += sorted(self._pinned, reverse=True)
            overflow = -spare
            for column in order:
                cut = min(overflow, max(0, widths[column] - MIN_TEXT_COLUMN))
                widths[column] -= cut
                overflow -= cut
                if overflow <= 0:
                    break
        for column, width in widths.items():
            if self.table.columnWidth(column) != width:
                self.table.setColumnWidth(column, width)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Resize and obj is self.table.viewport():
            self._layout_columns()
        return super().eventFilter(obj, event)

    def _finish_rows(self) -> None:
        """Call after filling the table: size rows to their widgets, fit the height, lay out columns."""
        self.table.resizeRowsToContents()
        self._fit_height()
        self._layout_columns()

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
        self._pinned = {self._here_column: _FIT_HINT, self._inherited_column: _FIT_TEXT}
        self.table.setRowCount(len(self._items))
        for row, item in enumerate(self._items):
            self._item_cells(row, item, len(self._columns))
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
        self._layout_columns()

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


class MappingEditor(TableEditor):
    """Table of ``spec.items()``; each "Here" cell edits that item's own value.

    ``value()`` is the scope's ``{id: value}`` (None when empty). Items without
    a value are absent; ids the catalog does not list are kept as they are. A
    row whose editor holds invalid input is left out of ``value()`` (never its
    stale value) and makes ``is_valid()`` False.
    """

    def __init__(self, spec: EditorSpec, unit: str = "", parent: QWidget | None = None) -> None:
        if spec.value is None:
            raise ValueError("a mapping needs a value editor spec")
        self._value_spec = spec.value
        self._unit = unit
        self._columns = tuple(spec.columns)
        here = f"{HERE_HEADER} ({unit})" if unit else HERE_HEADER
        super().__init__(("Name", *self._columns, here, INHERITED_HEADER), "Filter by name", len(self._columns), parent)
        self._items: list[CatalogItem] = list(spec.items() if spec.items is not None else ())
        self._editors: dict[str, FieldEditor] = {}
        self._stored: dict[str, Any] = {}
        self._inherited: dict[str, tuple[Any, str]] = {}
        self._loading = False
        self._here_column = 1 + len(self._columns)
        self._inherited_column = self._here_column + 1
        self._pinned = {self._here_column: _FIT_HINT, self._inherited_column: _FIT_TEXT}
        self.table.setRowCount(len(self._items))
        field = SettingField("", spec.value)  # the unit is in the column header, not after each editor
        for row, item in enumerate(self._items):
            self._item_cells(row, item, len(self._columns))
            editor = create_editor(spec.value, field)
            editor.changed.connect(self._on_item_changed)
            self.table.setCellWidget(row, self._here_column, editor)
            self._editors[item.id] = editor
        if spec.value.kind in LINE_EDIT_KINDS:
            # A line edit would otherwise size its column to its (wide) hint.
            self._pinned[self._here_column] = VALUE_COLUMN_WIDTH
        self._show_inherited()
        self._finish_rows()

    def _row_filter_text(self, row: int) -> str:
        item = self._items[row]
        return f"{item.label}\n{item.id}"

    def _on_item_changed(self) -> None:
        if not self._loading:
            self.changed.emit()

    def _format(self, value: Any) -> str:
        """A value as the user knows it: a choice by its label, a number with the unit."""
        if value is None:
            return "Not set"
        spec = self._value_spec
        if spec.kind == "toggle":
            return on_off_text(value)
        if spec.kind == "choice":
            options = spec.options() if callable(spec.options) else spec.options
            return next((label for val, label in options if val == value), str(value))
        text = str(value)
        return f"{text} {self._unit}" if self._unit else text

    def _show_inherited(self) -> None:
        for row, item in enumerate(self._items):
            value, origin = self._inherited.get(item.id, (item.default, DEFAULT_ORIGIN))
            self._text_cell(row, self._inherited_column, f"{self._format(value)} ({origin})", muted=True)
        self._layout_columns()

    # Mapping API --------------------------------------------------------
    def item_editor(self, item_id: str) -> FieldEditor:
        """The "Here" value editor of ``item_id`` (tests and screenshots)."""
        return self._editors[item_id]

    def item_combo(self, item_id: str) -> QComboBox:
        """The combo of a choice-valued row."""
        return self._editors[item_id].findChild(QComboBox)

    def set_inherited(self, inherited: dict[str, tuple[Any, str]]) -> None:
        """Show what broader scopes give each item: id -> (value, origin label)."""
        self._inherited = dict(inherited)
        self._show_inherited()

    # FieldEditor --------------------------------------------------------
    def value(self) -> dict[str, Any] | None:
        known = {item.id for item in self._items}
        out = {k: v for k, v in self._stored.items() if k not in known}
        for item in self._items:
            editor = self._editors[item.id]
            if not editor.is_valid():
                continue
            value = editor.value()
            if value is not None:
                out[item.id] = value
        return out or None

    def set_value(self, value: Any) -> None:
        self._stored = dict(value) if isinstance(value, dict) else {}
        self._loading = True
        try:
            for item in self._items:
                self._editors[item.id].set_value(self._stored.get(item.id))
        finally:
            self._loading = False

    def is_valid(self) -> bool:
        return all(editor.is_valid() for editor in self._editors.values())

    def set_read_only(self, read_only: bool) -> None:
        for editor in self._editors.values():
            editor.set_read_only(read_only)
