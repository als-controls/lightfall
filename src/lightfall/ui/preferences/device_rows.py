"""Settings -> Devices: the device backend rows of one scope.

``DeviceRowsEditor`` edits the union key ``device_backends`` at the shown scope.
It lists the scope's own rows and, greyed, the rows broader scopes give it; only the
own layer is edited (an inherited row is overridden, never removed). Selecting a row
shows its name and its type's settings form below the table.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from qtpy.QtCore import Qt
from qtpy.QtGui import QBrush
from qtpy.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lightfall.devices.backend_types.base import BackendEditor
from lightfall.devices.backend_types.registry import BackendTypeRegistry
from lightfall.devices.backend_types.rows import (
    EXCLUSIVE_RESTORE_KEY,
    BackendRow,
    ShownRow,
    enter_exclusive,
    leave_exclusive,
    normalized,
    restore_ids_from_value,
    restore_value,
    set_enabled,
    shown_rows,
    unique_name,
    with_row,
    without_row,
)
from lightfall.devices.backend_types.session import exclusive_row, is_exclusive
from lightfall.ui.preferences.form.catalog import _FIT_HINT, TableEditor
from lightfall.ui.preferences.form.editors import muted_color
from lightfall.ui.preferences.form.row import set_muted
from lightfall.ui.preferences.form.schema import SettingField

COLUMNS = ("", "Name", "Type", "From", "Details")
_CHECK, _NAME, _TYPE, _FROM, _DETAILS = range(len(COLUMNS))
HERE = "Here"
DETAIL_TITLE = "Backend settings"


def _device_backend_plugin_names() -> list[str]:
    """Names of the loaded device-backend plugins (kept off in an exclusive session)."""
    try:
        from lightfall.core import LFApplication
        from lightfall.plugins.registry import PluginRegistry

        registry = LFApplication.get_instance().services.get(PluginRegistry)
        return [info.name for info in registry.get_by_type("device_backend")]
    except Exception:
        return []


def _registry() -> BackendTypeRegistry:
    return BackendTypeRegistry.get_instance()


class DeviceRowsEditor(TableEditor):
    """Own and inherited device backend rows; ``value()`` is the own layer, normalized."""

    full_width = True

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(COLUMNS, "Filter by name or type", _DETAILS, parent)
        self._pinned = {_CHECK: _FIT_HINT}
        self._own: dict[str, dict[str, Any]] | None = None
        self._inherited: dict[str, tuple[dict[str, Any], str]] = {}
        self._restore_ids: list[str] = []
        self._shown: list[ShownRow] = []
        self._editors: dict[str, QWidget] = {}  # row id -> BackendEditor (or the unavailable label)
        # Rows whose settings form was edited: only their form's config is read back (a form
        # may fill in defaults, which must not turn an untouched row into an edit).
        self._edited_ids: set[str] = set()
        self._selected_id: str | None = None
        self._read_only = False
        self._loading = False
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        layout = self.layout()
        layout.addLayout(self._build_buttons())
        self.exclusive_hint = QLabel(self)
        self.exclusive_hint.setWordWrap(True)
        set_muted(self.exclusive_hint)
        self.exclusive_hint.hide()
        layout.addWidget(self.exclusive_hint)
        layout.addWidget(self._build_detail())
        self._refresh()

    # Building -----------------------------------------------------------
    def _build_buttons(self) -> QHBoxLayout:
        buttons = QHBoxLayout()
        self.add_button = QToolButton(self)
        self.add_button.setText("Add")
        self.add_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.add_button)
        for backend_type in _registry().all():
            action = menu.addAction(backend_type.display_name)
            action.triggered.connect(lambda _=False, t=backend_type.type_id: self._add_row(t))
        self.add_button.setMenu(menu)
        self.duplicate_button = QPushButton("Duplicate", self)
        self.duplicate_button.clicked.connect(self._duplicate_selected)
        self.remove_button = QPushButton("Remove", self)
        self.remove_button.clicked.connect(self._remove_selected)
        for button in (self.add_button, self.duplicate_button, self.remove_button):
            buttons.addWidget(button)
        buttons.addStretch()
        return buttons

    def _build_detail(self) -> QGroupBox:
        self.detail_group = QGroupBox(DETAIL_TITLE, self)
        box = QVBoxLayout(self.detail_group)
        self.override_button = QPushButton("Override here", self.detail_group)
        self.override_button.setToolTip("Copy this row into this scope so it can be edited here.")
        self.override_button.clicked.connect(self._override_selected)
        self.override_button.hide()
        box.addWidget(self.override_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.pane = QWidget(self.detail_group)
        pane_box = QVBoxLayout(self.pane)
        pane_box.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.name_edit = QLineEdit(self.pane)
        self.name_edit.textChanged.connect(self._on_name_edited)
        form.addRow("Name:", self.name_edit)
        pane_box.addLayout(form)
        self.stack = QStackedWidget(self.pane)
        pane_box.addWidget(self.stack)
        box.addWidget(self.pane)
        return self.detail_group

    # Rows ---------------------------------------------------------------
    def _type_label(self, row: BackendRow) -> str:
        backend_type = _registry().get(row.type)
        return backend_type.display_name if backend_type else f"(unavailable: {row.type})"

    def _summary(self, row: BackendRow) -> str:
        backend_type = _registry().get(row.type)
        return backend_type.summary(row.config) if backend_type else ""

    @staticmethod
    def _from_text(shown: ShownRow) -> str:
        if shown.origin is None:
            return HERE
        return f"{shown.origin}, changed here" if shown.own else shown.origin

    def _find(self, row_id: str | None) -> tuple[int, ShownRow] | None:
        for index, shown in enumerate(self._shown):
            if shown.row.id == row_id:
                return index, shown
        return None

    def _selected(self) -> ShownRow | None:
        found = self._find(self._selected_id)
        return found[1] if found else None

    def _cell(self, row: int, column: int, text: str, muted: bool) -> None:
        cell = self._text_cell(row, column, text)
        cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        if muted:
            self._muted_cells.append(cell)
            cell.setForeground(QBrush(muted_color(self.table)))
        else:
            cell.setData(Qt.ItemDataRole.ForegroundRole, None)

    def _refresh(self, detail: bool = True) -> None:
        """Re-read the shown rows into the table (cells are reused, never deleted mid-signal)."""
        self._shown = shown_rows(self._own, self._inherited)
        exclusive = exclusive_row([s.row for s in self._shown], _registry())
        self._loading = True
        try:
            self._muted_cells = []
            self.table.setRowCount(len(self._shown))
            for index, shown in enumerate(self._shown):
                check = self.table.item(index, _CHECK)
                if check is None:
                    check = QTableWidgetItem()
                    self.table.setItem(index, _CHECK, check)
                if exclusive is not None and shown.row is not exclusive:
                    flags = Qt.ItemFlag.ItemIsSelectable  # while an exclusive row is on, nothing else can be
                elif self._read_only or self._locked_exclusive(shown):
                    flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                else:
                    flags = Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                check.setFlags(flags)
                check.setCheckState(Qt.CheckState.Checked if shown.row.enabled else Qt.CheckState.Unchecked)
                muted = not shown.own
                self._cell(index, _NAME, shown.row.name, muted)
                self._cell(index, _TYPE, self._type_label(shown.row), muted)
                self._cell(index, _FROM, self._from_text(shown), muted)
                self._cell(index, _DETAILS, self._summary(shown.row), muted)
            found = self._find(self._selected_id)
            if found is None and self._shown:
                self._selected_id, found = self._shown[0].row.id, (0, self._shown[0])
            if found is None:
                self._selected_id = None
                self.table.clearSelection()
            else:
                self.table.selectRow(found[0])
        finally:
            self._loading = False
        self._apply_filter(self.filter_edit.text())
        self._update_hint(exclusive)
        self._update_buttons()
        if detail:
            self._show_detail()
        self._finish_rows()

    @staticmethod
    def _locked_exclusive(shown: ShownRow) -> bool:
        """An exclusive row a broader scope gives (or this scope overrides): switched there only."""
        return shown.origin is not None and is_exclusive(shown.row, _registry())

    def _update_hint(self, exclusive: BackendRow | None) -> None:
        if exclusive is None:
            self.exclusive_hint.hide()
            return
        backend_type = _registry().get(exclusive.type)
        label = backend_type.session_label(exclusive.config) if backend_type else exclusive.name
        text = f"Other device sources are off while {label} is on."
        plugins = _device_backend_plugin_names()
        if plugins:
            text += f" Also off: {', '.join(plugins)}."
        found = self._find(exclusive.id)
        if found is not None and found[1].origin is not None:
            text += f" It is switched on at {found[1].origin}; switch it off there."
        self.exclusive_hint.setText(text)
        self.exclusive_hint.show()

    def _update_buttons(self) -> None:
        shown = self._selected()
        editable = not self._read_only
        self.add_button.setEnabled(editable)
        self.duplicate_button.setEnabled(editable and shown is not None)
        override = shown is not None and shown.own and shown.origin is not None
        self.remove_button.setText("Use inherited" if override else "Remove")
        self.remove_button.setEnabled(editable and shown is not None and shown.own)

    def _show_detail(self) -> None:
        shown = self._selected()
        self.name_edit.blockSignals(True)  # loading a row into the field is not an edit
        try:
            if shown is None:
                self.detail_group.setTitle(DETAIL_TITLE)
                self.detail_group.setEnabled(False)
                self.override_button.hide()
                self.name_edit.clear()
                return
            self.detail_group.setEnabled(True)
            self.detail_group.setTitle(f"{shown.row.name} ({self._type_label(shown.row)})")
            self.override_button.setVisible(not shown.own)
            self.override_button.setEnabled(not self._read_only)
            self.pane.setEnabled(shown.own and not self._read_only)
            self.name_edit.setText(shown.row.name)
        finally:
            self.name_edit.blockSignals(False)
        self.stack.setCurrentWidget(self._editor_for(shown.row))

    def _editor_for(self, row: BackendRow) -> QWidget:
        editor = self._editors.get(row.id)
        if editor is not None:
            return editor
        backend_type = _registry().get(row.type)
        if backend_type is None:
            editor = QLabel(
                f"Backend type '{row.type}' is not available (is its plugin installed?). "
                "This row is kept unchanged.",
                self.stack,
            )
            editor.setWordWrap(True)
        else:
            editor = backend_type.create_editor(row.config, self.stack)
            editor.changed.connect(lambda rid=row.id: self._on_config_edited(rid))
        self._editors[row.id] = editor
        self.stack.addWidget(editor)
        return editor

    def _drop_editor(self, row_id: str) -> None:
        self._edited_ids.discard(row_id)
        editor = self._editors.pop(row_id, None)
        if editor is not None:
            self.stack.removeWidget(editor)
            editor.deleteLater()

    def _drop_editors(self) -> None:
        for row_id in list(self._editors):
            self._drop_editor(row_id)

    def _synced_row(self, shown: ShownRow) -> BackendRow:
        """``shown.row`` with the config its settings form holds, once that form was edited."""
        row = BackendRow.from_dict(shown.row.to_dict())
        editor = self._editors.get(row.id)
        if row.id in self._edited_ids and isinstance(editor, BackendEditor):
            row.config = editor.get_config()
        return row

    def _sync(self) -> None:
        """Fold the open forms' config and the name field into the own layer."""
        for shown in self._shown:
            if not shown.own:
                continue
            row = self._synced_row(shown)
            if row.id == self._selected_id:
                row.name = self.name_edit.text().strip()
            if row.to_dict() != shown.row.to_dict():
                self._own = with_row(self._own, row)
        self._shown = shown_rows(self._own, self._inherited)

    def _commit(self, own: dict[str, dict[str, Any]] | None, detail: bool = True) -> None:
        self._own = own
        self._refresh(detail)
        self.changed.emit()

    # Slots --------------------------------------------------------------
    def _on_selection_changed(self) -> None:
        if self._loading:
            return
        rows = self.table.selectionModel().selectedRows()
        index = rows[0].row() if rows else -1
        if 0 <= index < len(self._shown):
            self._selected_id = self._shown[index].row.id
        self._update_buttons()
        self._show_detail()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != _CHECK or not 0 <= item.row() < len(self._shown):
            return
        shown = self._shown[item.row()]
        row = shown.row
        checked = item.checkState() == Qt.CheckState.Checked
        if self._read_only or checked == row.enabled or self._locked_exclusive(shown):
            self._refresh()
            return
        backend_type = _registry().get(row.type)
        if backend_type is None or not backend_type.exclusive:
            self._commit(set_enabled(self._own, self._inherited, row.id, checked))
            return
        if checked:
            others = [s for s in self._shown if s.row.id != row.id and s.row.enabled]
            if others and not self._confirm_exclusive(backend_type.display_name, len(others)):
                self._refresh()  # puts the box back to unticked
                return
            own, self._restore_ids = enter_exclusive(self._own, self._inherited, row.id, _registry())
        else:
            own = leave_exclusive(self._own, self._inherited, row.id, self._restore_ids)
            self._restore_ids = []
        self._commit(own)

    def _confirm_exclusive(self, display_name: str, others: int) -> bool:
        sources = "device source" if others == 1 else "device sources"
        answer = QMessageBox.question(
            self,
            "Device backends",
            f"{display_name} mode disables the other {others} {sources}. Continue?",
        )
        return answer == QMessageBox.StandardButton.Yes

    def _on_config_edited(self, row_id: str) -> None:
        found = self._find(row_id)
        if self._loading or self._read_only or found is None or not found[1].own:
            return
        self._edited_ids.add(row_id)
        self._commit(with_row(self._own, self._synced_row(found[1])), detail=False)

    def _on_name_edited(self, text: str) -> None:
        shown = self._selected()
        if shown is None or not shown.own or self._read_only:
            return
        row = BackendRow.from_dict(shown.row.to_dict())
        row.name = text.strip()
        # The field keeps what is typed (no reload); only the table and title follow.
        self._commit(with_row(self._own, row), detail=False)
        self.detail_group.setTitle(f"{row.name} ({self._type_label(row)})")

    def _override_selected(self) -> None:
        shown = self._selected()
        if shown is None or shown.own or self._read_only:
            return
        self._commit(with_row(self._own, self._synced_row(shown)))

    def _all_names(self) -> list[str]:
        return [s.row.name for s in self._shown]

    def _add_row(self, type_id: str) -> None:
        backend_type = _registry().get(type_id)
        if backend_type is None or self._read_only:
            return
        row = BackendRow(
            type=type_id,
            name=unique_name(type_id, self._all_names()),
            # An exclusive row is switched on with its own checkbox, which asks first.
            enabled=not backend_type.exclusive,
            config=backend_type.default_config(),
        )
        self._selected_id = row.id
        self._commit(with_row(self._own, row))

    def _duplicate_selected(self) -> None:
        shown = self._selected()
        if shown is None or self._read_only:
            return
        source = self._synced_row(shown)
        row = BackendRow(
            type=source.type,
            name=unique_name(source.type, self._all_names()),
            enabled=source.enabled and not is_exclusive(source, _registry()),
            config=deepcopy(source.config),
        )
        self._selected_id = row.id
        self._commit(with_row(self._own, row))

    def _remove_selected(self) -> None:
        found = self._find(self._selected_id)
        if found is None or not found[1].own or self._read_only:
            return
        index, shown = found
        was_on = shown.row.enabled and is_exclusive(shown.row, _registry())
        own = without_row(self._own, shown.row.id, self._inherited)
        after = next((s for s in shown_rows(own, self._inherited) if s.row.id == shown.row.id), None)
        if was_on and not (after is not None and after.row.enabled):
            # Same as unticking it: the rows it switched off come back.
            own = leave_exclusive(own, self._inherited, shown.row.id, self._restore_ids)
            self._restore_ids = []
        self._drop_editor(shown.row.id)
        if after is None:
            remaining = [s.row.id for s in shown_rows(own, self._inherited)]
            self._selected_id = remaining[min(index, len(remaining) - 1)] if remaining else None
        self._commit(own)

    # Test hooks ---------------------------------------------------------
    def row_ids(self) -> list[str]:
        """Shown row ids, in table order."""
        return [s.row.id for s in self._shown]

    def select(self, row_id: str) -> None:
        found = self._find(row_id)
        if found is not None:
            self.table.selectRow(found[0])

    def set_checked(self, row_id: str, on: bool) -> None:
        """Set a row's checkbox, through the same ``itemChanged`` path a click takes."""
        found = self._find(row_id)
        if found is not None:
            state = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
            self.table.item(found[0], _CHECK).setCheckState(state)

    def cell_text(self, row_id: str, column: int | str) -> str:
        found = self._find(row_id)
        index = COLUMNS.index(column) if isinstance(column, str) else column
        cell = self.table.item(found[0], index) if found is not None else None
        return cell.text() if cell is not None else ""

    # Filtering ----------------------------------------------------------
    def _row_filter_text(self, row: int) -> str:
        if not 0 <= row < len(self._shown):
            return ""
        shown = self._shown[row]
        return f"{shown.row.name}\n{self._type_label(shown.row)}"

    # Inherited and companion values -------------------------------------
    def set_inherited(self, inherited: dict[str, tuple[Any, str]]) -> None:
        """Rows broader scopes give this one: row id -> (row dict, origin label)."""
        self._inherited = {str(rid): (item, origin) for rid, (item, origin) in (inherited or {}).items()}
        self._drop_editors()
        self._refresh()

    def companion_values(self) -> dict[str, Any]:
        return {EXCLUSIVE_RESTORE_KEY: restore_value(self._restore_ids) or None}

    def set_companion_values(self, values: dict[str, Any]) -> None:
        self._restore_ids = restore_ids_from_value(values.get(EXCLUSIVE_RESTORE_KEY))

    # FieldEditor --------------------------------------------------------
    def value(self) -> dict[str, dict[str, Any]] | None:
        self._sync()
        return normalized(self._own, self._inherited)

    def set_value(self, value: Any) -> None:
        if isinstance(value, dict) and value:
            self._own = {str(rid): (dict(item) if isinstance(item, dict) else item) for rid, item in value.items()}
        else:
            self._own = None
        self._drop_editors()
        self._refresh()

    def set_read_only(self, read_only: bool) -> None:
        self._read_only = read_only
        self._refresh()


def device_rows_editor(field: SettingField) -> DeviceRowsEditor:
    """``Custom`` factory for the ``device_backends`` field."""
    return DeviceRowsEditor()
