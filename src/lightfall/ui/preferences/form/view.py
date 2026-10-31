"""SettingsForm: one generated settings page, rendered at one scope."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from qtpy.QtCore import Signal
from qtpy.QtGui import QFont
from qtpy.QtWidgets import QLabel, QVBoxLayout, QWidget

from lightfall.scopes.keys import client_registry
from lightfall.ui.preferences.form.editors import create_editor
from lightfall.ui.preferences.form.model import FormModel
from lightfall.ui.preferences.form.row import SettingRow, set_muted
from lightfall.ui.preferences.form.schema import SettingField, field_label, fields_at

if TYPE_CHECKING:
    from lightfall.plugins.settings_plugin import SettingsPlugin

SECTION_SPACING = 16
GENERIC_READ_ONLY = "You can't change this here"


class _SectionBlock:
    """A rendered section: its header widgets and its rows."""

    def __init__(self, header: list[QWidget], rows: list[SettingRow]) -> None:
        self.header = header
        self.rows = rows


class SettingsForm(QWidget):
    """Renders a form page's ``sections()`` for the scope chosen in the dialog.

    ``prefs`` is duck-typed: only ``allowed_at``, ``writable_at``, ``lock_at``
    and ``scope_choices`` are used. Values come from and go to ``model``.
    """

    edited = Signal()

    def __init__(self, plugin: SettingsPlugin, model: FormModel, prefs: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._plugin = plugin
        self._model = model
        self._prefs = prefs
        self._scope: str | None = None
        self._fields: dict[str, SettingField] = {}
        self._rows: dict[str, SettingRow] = {}
        self._blocks: list[_SectionBlock] = []
        self._content: QWidget | None = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

    # Public API -----------------------------------------------------------
    @property
    def scope(self) -> str | None:
        return self._scope

    def has_fields(self, scope: str) -> bool:
        return bool(fields_at(self._plugin.sections(), scope, self._prefs.allowed_at))

    def show_scope(self, scope: str) -> None:
        """Rebuild the rows for ``scope`` from fresh store values and current rules.

        Each shown field is reloaded first, so values that changed while another
        scope was shown appear; dirty edits keep their value (and get the conflict
        flag if the stored value moved).
        """
        self._scope = scope
        for field in fields_at(self._plugin.sections(), scope, self._prefs.allowed_at):
            self._model.reload(scope, field.key)
        self._rows.clear()
        self._fields.clear()
        self._blocks.clear()
        if self._content is not None:
            self._layout.removeWidget(self._content)
            self._content.hide()
            self._content.setParent(None)  # detach now; deletion is deferred
            self._content.deleteLater()
        self._content = QWidget(self)
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        read_only_reason = self._scope_read_only_reason(scope)
        for section in self._plugin.sections():
            fields = fields_at([section], scope, self._prefs.allowed_at)
            if not fields:
                continue
            header = self._section_header(section.title, section.description)
            if self._blocks:
                content_layout.addSpacing(SECTION_SPACING)
            for widget in header:
                content_layout.addWidget(widget)
            rows = [self._build_row(f, scope, read_only_reason) for f in fields]
            for row in rows:
                content_layout.addWidget(row)
            self._blocks.append(_SectionBlock(header, rows))
        content_layout.addStretch(1)
        self._layout.addWidget(self._content)
        self._apply_predicates()

    def invalid_fields(self, scope: str) -> list[str]:
        """Keys at ``scope`` whose editor currently holds unparsable or out-of-range input."""
        if scope != self._scope:
            return []
        return [key for key, row in self._rows.items() if row.is_marked_invalid()]

    def refresh_from_store(self) -> None:
        """Reload shown keys; clean fields take the new value, dirty ones keep the edit."""
        scope = self._scope
        if scope is None:
            return
        for key, row in self._rows.items():
            self._model.reload(scope, key)
            if not self._model.is_dirty(scope, key):
                row.editor.set_value(self._model.pending(scope, key))
                row.set_invalid(False)
            self._refresh_markers(key)
        self._apply_predicates()

    # Building -------------------------------------------------------------
    def _section_header(self, title: str, description: str) -> list[QWidget]:
        label = QLabel(title, self._content)
        label.setObjectName("sectionTitle")
        font = QFont(label.font())
        font.setBold(True)
        label.setFont(font)
        widgets: list[QWidget] = [label]
        if description:
            desc = QLabel(description, self._content)
            desc.setWordWrap(True)
            set_muted(desc)
            widgets.append(desc)
        return widgets

    def _build_row(self, field: SettingField, scope: str, scope_reason: str) -> SettingRow:
        spec = client_registry().get(field.key)
        editor = create_editor(field.editor, field)
        row = SettingRow(
            field.key,
            field_label(field, spec),
            field.description,
            editor,
            restart_required=field.restart_required or spec.restart_required,
            parent=self._content,
        )
        editor.set_value(self._model.pending(scope, field.key))
        reason = self._read_only_reason(field.key, scope, scope_reason)
        # The header already states the scope's reason; repeat only the lock glyph.
        row.set_read_only(reason, inline=reason != scope_reason)
        key = field.key
        editor.changed.connect(lambda: self._on_editor_changed(key))
        row.clear_requested.connect(lambda: self._on_clear(key))
        self._fields[key] = field
        self._rows[key] = row
        self._refresh_markers(key)
        return row

    def _scope_read_only_reason(self, scope: str) -> str:
        choice = next((c for c in self._prefs.scope_choices() if c.scope == scope), None)
        return choice.read_only_reason if choice is not None else ""

    def _read_only_reason(self, key: str, scope: str, scope_reason: str) -> str | None:
        """Inline reason the row can't be edited, or None if it is editable."""
        lock = self._prefs.lock_at(key, scope)
        if lock is not None:
            text = f"Locked at {lock.locked_at}"
            if lock.locked_by:
                text += f" by {lock.locked_by}"
            if lock.reason:
                text += f": {lock.reason}"
            return text
        if not self._prefs.writable_at(key, scope):
            return scope_reason or GENERIC_READ_ONLY
        return None

    # Reacting -------------------------------------------------------------
    def _on_editor_changed(self, key: str) -> None:
        row = self._rows.get(key)
        if row is None or row.is_read_only() or self._scope is None:
            return
        valid = row.editor.is_valid()
        row.set_invalid(not valid)
        if valid:
            self._model.edit(self._scope, key, row.editor.value())
        self._after_edit(key)

    def _on_clear(self, key: str) -> None:
        row = self._rows.get(key)
        if row is None or row.is_read_only() or self._scope is None:
            return
        self._model.edit(self._scope, key, None)
        row.editor.set_value(None)
        row.set_invalid(False)
        self._after_edit(key)

    def _after_edit(self, key: str) -> None:
        self._refresh_markers(key)
        self._apply_predicates()
        self.edited.emit()

    def _refresh_markers(self, key: str) -> None:
        scope = self._scope
        row = self._rows[key]
        row.set_markers(
            is_set=self._model.pending(scope, key) is not None,
            dirty=self._model.is_dirty(scope, key),
            conflict=self._model.conflict(scope, key),
        )

    def _apply_predicates(self) -> None:
        """Re-evaluate visible_when / enabled_when against the pending values."""
        if self._scope is None:
            return
        values = self._model.values(self._scope)
        for block in self._blocks:
            any_visible = False
            for row in block.rows:
                field = self._fields[row.key]
                visible = field.visible_when(values) if field.visible_when else True
                enabled = field.enabled_when(values) if field.enabled_when else True
                row.setVisible(bool(visible))
                row.editor.setEnabled(bool(enabled))
                any_visible = any_visible or bool(visible)
            for widget in block.header:
                widget.setVisible(any_visible)
