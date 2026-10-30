"""Preferences dialog for NCS.

Provides a plugin-driven dialog for viewing and editing user preferences.
Settings plugins are discovered from the PluginRegistry and rendered
dynamically. Form pages (``plugin.is_form_page()``) are generated from their
schema and edited at the scope chosen in the header; hand-built (legacy) pages
keep their own widget and save logic.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from lightfall.scopes.keys import client_registry
from lightfall.ui.preferences.form.editors import _muted_color
from lightfall.ui.preferences.form.header import ScopeHeader
from lightfall.ui.preferences.form.model import FormModel
from lightfall.ui.preferences.form.schema import SettingField, field_label
from lightfall.ui.preferences.form.view import SettingsForm
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.plugins.settings_plugin import SettingsPlugin


def _is_form_page(plugin: Any) -> bool:
    check = getattr(plugin, "is_form_page", None)
    return bool(check()) if callable(check) else False


class _FormPage(QWidget):
    """A form page's stack content: the form, or a note when nothing applies at the scope."""

    def __init__(self, form: SettingsForm, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.form = form
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(form)
        self._empty = QWidget(self)
        empty_layout = QVBoxLayout(self._empty)
        empty_layout.addStretch(1)
        self._message = QLabel(self._empty)
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setWordWrap(True)
        empty_layout.addWidget(self._message)
        self._buttons = QHBoxLayout()
        self._buttons.addStretch(1)
        self._buttons.addStretch(1)
        empty_layout.addLayout(self._buttons)
        empty_layout.addStretch(1)
        layout.addWidget(self._empty)
        self._empty.hide()

    def show_form(self) -> None:
        self._empty.hide()
        self.form.show()

    def show_empty(self, label: str, offers: list[tuple[str, Callable[[], None]]]) -> None:
        """Show the 'nothing here' note with one button per scope where the page has fields."""
        self.form.hide()
        self._message.setText(f"Nothing on this page can be set at {label}.")
        while self._buttons.count() > 2:  # keep the two centring stretches
            item = self._buttons.takeAt(1)
            if item.widget() is not None:
                item.widget().deleteLater()
        for text, slot in offers:
            button = QPushButton(text, self._empty)
            button.clicked.connect(slot)
            self._buttons.insertWidget(self._buttons.count() - 1, button)
        self._empty.show()


class PreferencesDialog(QDialog):
    """Plugin-driven dialog for editing user preferences.

    Discovers all registered SettingsPlugin instances and renders them
    as pages in a sidebar-navigated dialog. A scope header above the pages
    selects where form pages read and write; one ``FormModel`` holds the
    pending edits of every form page at every scope.

    Example:
        >>> dialog = PreferencesDialog(parent)
        >>> if dialog.exec() == QDialog.DialogCode.Accepted:
        ...     # Preferences were applied

    The dialog supports:
    - Live preview via apply_preview()
    - Validation before save via validate() / validate_values()
    - Revert on cancel via revert_preview()
    """

    def __init__(
        self, parent: QWidget | None = None, initial_page: str | None = None
    ) -> None:
        """Initialize the preferences dialog.

        Args:
            parent: Parent widget.
            initial_page: Optional plugin name to navigate to initially.
        """
        super().__init__(parent)
        from lightfall.ui.preferences.manager import PreferencesManager

        self._prefs = PreferencesManager.get_instance()
        self._model = FormModel(read_layer=lambda key, scope: self._prefs.layer_value(key, scope))
        self._scope: str = self._prefs.default_scope()
        self._plugins: dict[str, SettingsPlugin] = {}
        self._plugin_widgets: dict[str, QWidget] = {}
        self._page_names: list[str] = []  # sidebar / stack order
        self._form_pages: dict[str, _FormPage] = {}
        self._key_owner: dict[str, str] = {}  # form key -> plugin name
        self._fields: dict[str, SettingField] = {}
        self._subscriptions: list[tuple[str, Callable[[Any], None]]] = []
        self._initial_page = initial_page

        self._setup_ui()
        self._load_plugins()
        self._subscribe()
        scopes_changed = getattr(self._prefs, "scopes_changed", None)
        if scopes_changed is not None:
            scopes_changed.connect(self._on_scopes_changed)

        # Navigate to initial page if specified
        if initial_page:
            self.select_page(initial_page)

    def _setup_ui(self) -> None:
        """Setup the dialog UI with sidebar navigation."""
        self.setWindowTitle("Preferences")
        self.setMinimumSize(600, 400)

        layout = QHBoxLayout(self)
        layout.setSpacing(16)

        # Left sidebar for navigation
        self._sidebar = QListWidget()
        self._sidebar.setSelectionBehavior(QListWidget.SelectionBehavior.SelectRows)
        self._sidebar.setMaximumWidth(150)
        self._sidebar.setMinimumWidth(120)
        self._sidebar.currentRowChanged.connect(self._on_page_changed)
        layout.addWidget(self._sidebar)

        # Right content area
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Scope header above the pages
        self._header = ScopeHeader(self)
        self._header.set_choices(self._prefs.scope_choices(), self._scope)
        self._header.scope_selected.connect(self.select_scope)
        right_layout.addWidget(self._header)

        # Stack for plugin widgets
        self._stack = QStackedWidget()
        right_layout.addWidget(self._stack, 1)

        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        apply_button = button_box.button(QDialogButtonBox.StandardButton.Apply)
        if apply_button:
            apply_button.clicked.connect(self._apply)

        right_layout.addWidget(button_box)
        layout.addLayout(right_layout, 1)

    def _load_plugins(self) -> None:
        """Load all settings plugins and create their pages."""
        from lightfall.core.services import ServiceRegistry
        from lightfall.plugins.registry import PluginRegistry

        try:
            services = ServiceRegistry.get_instance()
            registry = services.get(PluginRegistry)
        except Exception:
            # PluginRegistry may not be initialized yet
            logger.warning("PluginRegistry not available, preferences dialog will be empty")
            return

        if registry is None:
            logger.warning("PluginRegistry not registered, preferences dialog will be empty")
            return

        plugin_infos = registry.get_ready_by_type("settings")

        if not plugin_infos:
            logger.debug("No settings plugins found")
            return

        # Sort by category, then priority
        plugin_infos.sort(key=lambda p: (p.instance.category, p.instance.priority))

        for info in plugin_infos:
            plugin = info.instance
            try:
                if _is_form_page(plugin):
                    widget = self._build_form_page(plugin)
                else:
                    logger.warning(
                        "Settings page '{}' is hand-built (deprecated); convert it to sections()",
                        plugin.name,
                    )
                    widget = plugin.create_widget(self)
                    plugin.load_settings()
                self._plugins[plugin.name] = plugin
                self._plugin_widgets[plugin.name] = widget

                # Add to UI. Wrap each plugin widget in its own vertical
                # scroll area (one per stack page). Putting the QStackedWidget
                # itself inside a single scroll area would size its contents to
                # the tallest page; per-page scroll areas scroll independently.
                scroll = QScrollArea()
                scroll.setWidgetResizable(True)
                scroll.setFrameShape(QScrollArea.Shape.NoFrame)
                scroll.setHorizontalScrollBarPolicy(
                    Qt.ScrollBarPolicy.ScrollBarAlwaysOff
                )
                scroll.setVerticalScrollBarPolicy(
                    Qt.ScrollBarPolicy.ScrollBarAsNeeded
                )
                scroll.setWidget(widget)

                item = QListWidgetItem(plugin.display_name)
                if plugin.icon:
                    item.setIcon(plugin.icon)
                self._page_names.append(plugin.name)
                self._sidebar.addItem(item)
                self._stack.addWidget(scroll)

                logger.debug("Loaded settings plugin: {}", plugin.name)
            except Exception as e:
                logger.error("Failed to load settings plugin '{}': {}", plugin.name, e)

        self._refresh_pages()

        # Select first item
        if self._sidebar.count() > 0:
            self._sidebar.setCurrentRow(0)

    def _build_form_page(self, plugin: SettingsPlugin) -> _FormPage:
        form = SettingsForm(plugin, self._model, self._prefs)
        form.edited.connect(self._on_form_edited)
        form.show_scope(self._scope)
        page = _FormPage(form)
        self._form_pages[plugin.name] = page
        for section in plugin.sections():
            for field in section.items:
                self._key_owner[field.key] = plugin.name
                self._fields[field.key] = field
        return page

    # Live updates -----------------------------------------------------------
    def _subscribe(self) -> None:
        subscribe = getattr(self._prefs, "subscribe", None)
        if subscribe is None:
            return
        for key, name in self._key_owner.items():
            form = self._form_pages[name].form

            def slot(_value: Any, form: SettingsForm = form) -> None:
                form.refresh_from_store()

            subscribe(key, slot)
            self._subscriptions.append((key, slot))

    def _unsubscribe(self) -> None:
        unsubscribe = getattr(self._prefs, "unsubscribe", None)
        for key, slot in self._subscriptions:
            if unsubscribe is not None:
                unsubscribe(key, slot)
        self._subscriptions.clear()

    def _on_scopes_changed(self, _info: Any = None) -> None:
        choices = self._prefs.scope_choices()
        scopes = {c.scope for c in choices}
        selected = self._scope if self._scope in scopes else self._prefs.default_scope()
        self._header.set_choices(choices, selected)
        self._header.set_dirty_scopes(self._model.dirty_scopes())
        self._scope = selected
        # Rebuild even at the same scope: which fields show and which are editable
        # may have changed. Dirty edits live in the model and survive.
        for page in self._form_pages.values():
            page.form.show_scope(selected)
        self._refresh_pages()

    # Scope selection --------------------------------------------------------
    def current_scope(self) -> str:
        return self._scope

    def select_scope(self, scope: str) -> None:
        """Show every form page at ``scope``; refused while the current scope has invalid input."""
        if scope == self._scope:
            self._header.set_selected(scope)
            return
        invalid = self._invalid_labels()
        if invalid:
            QMessageBox.warning(
                self,
                "Invalid values",
                "Fix these values before switching scope:\n\n" + "\n".join(invalid),
            )
            self._header.set_selected(self._scope)
            return
        self._scope = scope
        self._header.set_selected(scope)
        for page in self._form_pages.values():
            page.form.show_scope(scope)
        self._refresh_pages()

    def _scope_label(self, scope: str) -> str:
        choice = next((c for c in self._prefs.scope_choices() if c.scope == scope), None)
        return choice.label if choice is not None else scope

    def _refresh_pages(self) -> None:
        """Dim form pages with nothing at the scope and show their 'nothing here' note."""
        choices = self._prefs.scope_choices()
        dim = _muted_color(self)
        label = self._scope_label(self._scope)
        for row, name in enumerate(self._page_names):
            page = self._form_pages.get(name)
            if page is None:
                continue
            item = self._sidebar.item(row)
            if page.form.has_fields(self._scope):
                item.setData(Qt.ItemDataRole.ForegroundRole, None)
                page.show_form()
                continue
            item.setForeground(dim)
            offers = [
                (c.label, lambda _checked=False, s=c.scope: self.select_scope(s))
                for c in choices
                if c.scope != self._scope and page.form.has_fields(c.scope)
            ]
            page.show_empty(label, offers)

    def _on_form_edited(self) -> None:
        self._header.set_dirty_scopes(self._model.dirty_scopes())

    def _invalid_labels(self) -> list[str]:
        labels: list[str] = []
        for name, page in self._form_pages.items():
            for key in page.form.invalid_fields(self._scope):
                labels.append(f"{self._plugins[name].display_name}: {self._label(key)}")
        return labels

    def _label(self, key: str) -> str:
        return field_label(self._fields[key], client_registry().get(key))

    # Pages --------------------------------------------------------------------
    def _on_page_changed(self, row: int) -> None:
        """Handle sidebar selection change.

        Args:
            row: The selected row index.
        """
        self._stack.setCurrentIndex(row)
        if 0 <= row < len(self._page_names):
            plugin = self._plugins[self._page_names[row]]
            aware = _is_form_page(plugin) and getattr(plugin, "scope_aware", True)
            self._header.set_page_scope_aware(bool(aware))

    def select_page(self, plugin_name: str) -> bool:
        """Select a settings page by plugin name.

        Args:
            plugin_name: The name of the plugin to navigate to.

        Returns:
            True if the page was found and selected.
        """
        if plugin_name in self._page_names:
            self._sidebar.setCurrentRow(self._page_names.index(plugin_name))
            return True
        return False

    # Saving -----------------------------------------------------------------
    def _validation_errors(self) -> list[str]:
        errors = [f"Invalid value: {label}" for label in self._invalid_labels()]
        if errors:
            return errors
        for plugin in self._plugins.values():
            if _is_form_page(plugin):
                errors.extend(self._form_errors(plugin))
                continue
            try:
                for err in plugin.validate() or []:
                    errors.append(f"{plugin.display_name}: {err}")
            except Exception as e:
                errors.append(f"{plugin.display_name}: Validation error - {e}")
        return errors

    def _form_errors(self, plugin: SettingsPlugin) -> list[str]:
        """Per-field and cross-field errors of a form page at every scope it has edits."""
        errors: list[str] = []
        keys = [k for k, owner in self._key_owner.items() if owner == plugin.name]
        for scope in sorted(self._model.dirty_scopes()):
            dirty = [k for k in keys if self._model.is_dirty(scope, k)]
            if not dirty:
                continue
            values = self._model.values(scope)
            label = self._scope_label(scope)
            try:
                for key in dirty:
                    field = self._fields[key]
                    value = self._model.pending(scope, key)
                    if field.validate is None or value is None:
                        continue  # clearing a value is always allowed
                    message = field.validate(value, values)
                    if message:
                        errors.append(f"{plugin.display_name} ({label}): {self._label(key)}: {message}")
                for err in plugin.validate_values(values) or []:
                    errors.append(f"{plugin.display_name} ({label}): {err}")
            except Exception as e:
                errors.append(f"{plugin.display_name} ({label}): Validation error - {e}")
        return errors

    def _unwritable_edits(self) -> list[str]:
        """Labels (with scope) of unsaved edits that can no longer be written."""
        return [
            f"{self._label(key)} ({self._scope_label(scope)})"
            for scope, key in self._model.dirty_cells()
            if not self._prefs.writable_at(key, scope)
        ]

    def _write_ops(self) -> tuple[list[tuple[str, str, Any]], bool]:
        """Write every dirty, writable edit; stop at the first failure.

        Returns the (scope, key, value) writes that landed and whether all did.
        """
        written: list[tuple[str, str, Any]] = []
        for op in self._model.ops(self._prefs.writable_at):
            try:
                if op.value is None:
                    self._prefs.remove(op.key, scope=op.scope)
                else:
                    self._prefs.set(op.key, op.value, scope=op.scope)
            except Exception as e:
                logger.error("Failed to save '{}' at {}: {}", op.key, op.scope, e)
                QMessageBox.critical(
                    self,
                    "Error",
                    f"Failed to save {op.key} at {op.scope}: {e}\n\nThe remaining changes were not saved.",
                )
                return written, False
            self._model.commit(op.scope, op.key)
            written.append((op.scope, op.key, op.value))
        return written, True

    def _after_save(self, written: list[tuple[str, str, Any]]) -> None:
        changed: dict[tuple[str, str], dict[str, Any]] = defaultdict(dict)
        for scope, key, value in written:
            changed[(self._key_owner[key], scope)][key] = value
        for (name, scope), values in changed.items():
            plugin = self._plugins[name]
            try:
                plugin.after_save(scope, values)
            except Exception as e:
                logger.error("after_save failed for '{}' at {}: {}", name, scope, e)

    def _needs_restart(self, written: list[tuple[str, str, Any]]) -> bool:
        return any(
            self._fields[key].restart_required or client_registry().get(key).restart_required
            for _scope, key, _value in written
        )

    def _prompt_restart(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Restart required")
        box.setText("Some of the changed settings take effect after Lightfall restarts.")
        restart = box.addButton("Restart now", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is restart:
            from lightfall.utils.restart import request_restart

            request_restart()

    def _apply(self) -> bool:
        """Apply settings without closing.

        Returns:
            True if settings were applied successfully.
        """
        errors = self._validation_errors()
        if errors:
            QMessageBox.warning(
                self,
                "Validation Error",
                "Please fix the following errors:\n\n" + "\n".join(errors),
            )
            return False

        blocked = self._unwritable_edits()
        if blocked:
            QMessageBox.warning(
                self,
                "Can't save",
                "You can no longer change these where you edited them, so nothing was saved:\n\n"
                + "\n".join(blocked)
                + "\n\nCancel to discard your changes.",
            )
            return False

        written, ok = self._write_ops()
        self._after_save(written)
        self._header.set_dirty_scopes(self._model.dirty_scopes())
        for page in self._form_pages.values():
            page.form.refresh_from_store()
        if not ok:
            return False

        # Save hand-built (legacy) pages
        for plugin in self._plugins.values():
            if _is_form_page(plugin):
                continue
            try:
                plugin.save_settings()
                logger.debug("Saved settings for plugin: {}", plugin.name)
            except Exception as e:
                logger.error("Failed to save settings for '{}': {}", plugin.name, e)
                QMessageBox.critical(
                    self,
                    "Error",
                    f"Failed to save {plugin.display_name} settings: {e}",
                )
                return False

        if self._needs_restart(written):
            self._prompt_restart()
        return True

    def accept(self) -> None:
        """Validate, save preferences, and close dialog."""
        if self._apply():
            super().accept()

    def reject(self) -> None:
        """Discard pending edits, revert any preview changes and close dialog."""
        self._model.discard()
        for plugin in self._plugins.values():
            try:
                revert = getattr(plugin, "revert_preview", None)
                if revert is not None:
                    revert()
            except Exception as e:
                logger.error("Error reverting preview for '{}': {}", plugin.name, e)
        super().reject()

    def done(self, result: int) -> None:
        """Drop live-update subscriptions whichever way the dialog closes."""
        self._unsubscribe()
        scopes_changed = getattr(self._prefs, "scopes_changed", None)
        if scopes_changed is not None:
            try:
                scopes_changed.disconnect(self._on_scopes_changed)
            except (RuntimeError, TypeError):
                pass
        super().done(result)
