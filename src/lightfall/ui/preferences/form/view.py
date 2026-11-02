"""SettingsForm: one generated settings page, rendered at one scope."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from qtpy.QtCore import Signal
from qtpy.QtWidgets import QGroupBox, QLabel, QVBoxLayout, QWidget

from lightfall.scopes.keys import client_registry
from lightfall.scopes.store import MISSING
from lightfall.ui.preferences.form.editors import create_editor
from lightfall.ui.preferences.form.items import ActionItem, InfoItem
from lightfall.ui.preferences.form.model import FormModel
from lightfall.ui.preferences.form.row import SettingRow, set_muted
from lightfall.ui.preferences.form.schema import (
    Action,
    Embed,
    Info,
    Section,
    SettingField,
    field_label,
    fields_at,
    item_shown_at,
    section_fields,
)

if TYPE_CHECKING:
    from lightfall.plugins.settings_plugin import SettingsPlugin

SECTION_SPACING = 16
GENERIC_READ_ONLY = "You can't change this here"


class _SectionBlock:
    """A rendered section: its group box, the rows and the other items inside it."""

    def __init__(self, box: QGroupBox) -> None:
        self.box = box
        self.rows: list[SettingRow] = []
        self.actions: list[ActionItem] = []
        self.infos: list[InfoItem] = []
        self.has_items = False  # any Action / Info / Embed shown at this scope


class SettingsForm(QWidget):
    """Renders a form page's ``sections()`` for the scope chosen in the dialog.

    ``prefs`` is duck-typed: only ``allowed_at``, ``writable_at``, ``lock_at``
    and ``scope_choices`` are used (``needs_approval``, ``awaiting_approval`` and,
    for catalog fields, ``inherited_items`` too when present). Values come from and go to ``model``.
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
        self._needs_approval: dict[str, bool] = {}
        self._approval_for_all = False
        self._blocks: list[_SectionBlock] = []
        self._content: QWidget | None = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

    # Public API -----------------------------------------------------------
    @property
    def scope(self) -> str | None:
        return self._scope

    def has_fields(self, scope: str) -> bool:
        """Whether any field may be set at ``scope`` (Actions, Info and Embeds don't count)."""
        return bool(fields_at(self._plugin.sections(), scope, self._prefs.allowed_at))

    def show_scope(self, scope: str) -> None:
        """Rebuild the rows for ``scope`` from fresh store values and current rules.

        Each shown field is reloaded first, so values that changed while another
        scope was shown appear; dirty edits keep their value (and get the conflict
        flag if the stored value moved).
        """
        self._scope = scope
        for field in fields_at(self._plugin.sections(), scope, self._prefs.allowed_at):
            for key in (field.key, *field.companions):
                self._model.reload(scope, key)
        self._rows.clear()
        self._fields.clear()
        self._needs_approval.clear()
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
            shown = self._items_at(section, scope)
            if not shown:
                continue
            box = self._section_box(section.title, section.description)
            if self._blocks:
                content_layout.addSpacing(SECTION_SPACING)
            content_layout.addWidget(box)
            block = _SectionBlock(box)
            for item in shown:
                self._add_item(block, item, scope, read_only_reason)
            self._blocks.append(block)
        content_layout.addStretch(1)
        self._layout.addWidget(self._content)
        self._place_approval_notes()
        self._apply_predicates()

    def approval_for_all(self) -> bool:
        """Every editable field at the shown scope needs approval: the scope header says so once."""
        return self._approval_for_all

    def invalid_fields(self, scope: str) -> list[str]:
        """Keys at ``scope`` whose editor holds unparsable or out-of-range input.

        Rows hidden or disabled by their predicates don't count: the user can't
        see or fix them, and their (unchanged) model value is what gets saved.
        """
        if scope != self._scope:
            return []
        return [
            key
            for key, row in self._rows.items()
            if row.is_marked_invalid() and row.isVisibleTo(self) and row.editor.isEnabled()
        ]

    def refresh_from_store(self) -> None:
        """Reload shown keys; clean fields take the new value, dirty ones keep the edit."""
        scope = self._scope
        if scope is None:
            return
        for key, row in self._rows.items():
            keys = self._own_keys(key)
            for k in keys:
                self._model.reload(scope, k)
            if not any(self._model.is_dirty(scope, k) for k in keys):
                row.editor.set_value(self._model.pending(scope, key))
                self._show_companions(key, row.editor, scope)
                row.set_invalid(False)
            self._show_inherited(key, row.editor, scope)
            self._refresh_markers(key)
        self._apply_predicates()

    # Building -------------------------------------------------------------
    def _items_at(self, section: Section, scope: str) -> list:
        """Items of ``section`` that belong at ``scope``, in order."""
        return [
            item
            for item in section.items
            if (self._prefs.allowed_at(item.key, scope) if isinstance(item, SettingField) else item_shown_at(item, scope))
        ]

    def _add_item(self, block: _SectionBlock, item: object, scope: str, read_only_reason: str) -> None:
        layout = block.box.layout()
        if isinstance(item, SettingField):
            row = self._build_row(item, scope, read_only_reason, block.box)
            layout.addWidget(row)
            block.rows.append(row)
            return
        block.has_items = True
        if isinstance(item, Action):
            action = ActionItem(item, lambda: self._action_values(scope), block.box)
            layout.addWidget(action)
            block.actions.append(action)
        elif isinstance(item, Info):
            info = InfoItem(item, block.box.title(), block.box)
            layout.addWidget(info)
            block.infos.append(info)
        elif isinstance(item, Embed):
            layout.addWidget(item.factory(block.box))
        else:  # pragma: no cover - a page bug
            raise TypeError(f"Unknown section item: {item!r}")

    def _action_values(self, scope: str):
        """Values an action's worker sees: a copy of every field key of this page."""
        keys = {f.key for s in self._plugin.sections() for f in section_fields(s)}
        return self._model.frozen_values(scope, keys, self._prefs.allowed_at)

    def _section_box(self, title: str, description: str) -> QGroupBox:
        """A group box titled ``title``; the theme's QGroupBox rules style it."""
        box = QGroupBox(title, self._content)
        layout = QVBoxLayout(box)
        layout.setSpacing(0)
        if description:
            desc = QLabel(description, box)
            desc.setWordWrap(True)
            set_muted(desc)
            layout.addWidget(desc)
        return box

    def _build_row(self, field: SettingField, scope: str, scope_reason: str, parent: QWidget) -> SettingRow:
        spec = client_registry().get(field.key)
        editor = create_editor(field.editor, field)
        row = SettingRow(
            field.key,
            field_label(field, spec),
            field.description,
            editor,
            restart_required=field.restart_required or spec.restart_required,
            parent=parent,
        )
        self._fields[field.key] = field
        editor.set_value(self._model.pending(scope, field.key))
        self._show_companions(field.key, editor, scope)
        self._show_inherited(field.key, editor, scope)
        reason = self._read_only_reason(field.key, scope, scope_reason)
        # The header already states the scope's reason; repeat only the lock glyph.
        row.set_read_only(reason, inline=reason != scope_reason)
        needs_approval = getattr(self._prefs, "needs_approval", None)
        self._needs_approval[field.key] = bool(needs_approval and needs_approval(field.key, scope))
        key = field.key
        editor.changed.connect(lambda: self._on_editor_changed(key))
        row.clear_requested.connect(lambda: self._on_clear(key))
        self._rows[key] = row
        self._refresh_markers(key)
        return row

    def _own_keys(self, key: str) -> tuple[str, ...]:
        """The field's key and the companion keys its editor owns."""
        return (key, *self._fields[key].companions)

    def _show_companions(self, key: str, editor: Any, scope: str) -> None:
        """Give an editor that owns companion keys their pending values at ``scope``."""
        companions = self._fields[key].companions
        set_companion_values = getattr(editor, "set_companion_values", None)
        if companions and set_companion_values is not None:
            set_companion_values({k: self._model.pending(scope, k) for k in companions})

    def _show_inherited(self, key: str, editor: Any, scope: str) -> None:
        """Give an item-merged editor what broader scopes set (the one exception to "own layer only")."""
        set_inherited = getattr(editor, "set_inherited", None)
        if set_inherited is None:
            return
        inherited_items = getattr(self._prefs, "inherited_items", None)
        set_inherited(inherited_items(key, scope) if inherited_items else {})

    def _place_approval_notes(self) -> None:
        """Note approval on the rows that need it, or nowhere when every editable row does."""
        editable = [key for key, row in self._rows.items() if not row.is_read_only()]
        self._approval_for_all = bool(editable) and all(self._needs_approval[key] for key in editable)
        for key, row in self._rows.items():
            row.set_needs_approval(self._needs_approval[key] and not self._approval_for_all)

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
            companion_values = getattr(row.editor, "companion_values", None)
            if self._fields[key].companions and companion_values is not None:
                for k, value in companion_values().items():
                    self._model.edit(self._scope, k, value)
        self._after_edit(key)

    def _on_clear(self, key: str) -> None:
        row = self._rows.get(key)
        if row is None or row.is_read_only() or self._scope is None:
            return
        for k in self._own_keys(key):
            self._model.edit(self._scope, k, None)
        row.editor.set_value(None)
        self._show_companions(key, row.editor, self._scope)
        row.set_invalid(False)
        self._after_edit(key)

    def _after_edit(self, key: str) -> None:
        self._refresh_markers(key)
        self._apply_predicates()
        self.edited.emit()

    def _refresh_markers(self, key: str) -> None:
        scope = self._scope
        row = self._rows[key]
        keys = self._own_keys(key)
        row.set_markers(
            is_set=self._model.pending(scope, key) is not None,
            dirty=any(self._model.is_dirty(scope, k) for k in keys),
            conflict=any(self._model.conflict(scope, k) for k in keys),
        )
        awaiting = getattr(self._prefs, "awaiting_approval", None)
        row.set_awaiting_approval(bool(awaiting) and awaiting(key, scope) is not MISSING)

    def _apply_predicates(self) -> None:
        """Re-evaluate visible_when / enabled_when against the pending values."""
        if self._scope is None:
            return
        values = self._model.values(self._scope)
        for block in self._blocks:
            any_visible = block.has_items
            for info in block.infos:
                info.update_text(values)
            for action in block.actions:
                when = action.action.enabled_when
                action.set_predicate_enabled(bool(when(values)) if when else True)
            for row in block.rows:
                field = self._fields[row.key]
                visible = field.visible_when(values) if field.visible_when else True
                enabled = field.enabled_when(values) if field.enabled_when else True
                row.setVisible(bool(visible))
                row.editor.setEnabled(bool(enabled))
                any_visible = any_visible or bool(visible)
            block.box.setVisible(any_visible)
