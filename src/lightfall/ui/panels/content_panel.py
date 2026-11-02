"""Shared content panel: every plan, plugin, agent and skill with where it comes from.

Actions on the selected row: Publish (mine), History (synced, retracted, or mine: pick a
shared scope of the chain), Retract (synced), Copy to mine, Open folder. "Show retracted"
adds the items retracted at one shared scope of the chain (fetched with the injected
client on ``run``), so one nobody has locally can still be restored from its History.
Network work goes through the dialogs or the injected ``run``; the lists
come from ``actions.content_rows()``, which only reads registries and small index files.
Every collaborator (rows, scope state, client, runner, refresh signals) can be injected
so tests never reach the network or the real registries.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar

from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)
from shiboken6 import isValid

from lightfall.content import actions
from lightfall.content.actions import ContentRow
from lightfall.content.copy import copy_to_mine
from lightfall.content.labels import describe_source
from lightfall.content.publish import LocalItem, local_items
from lightfall.content.store import _is_shared_scope
from lightfall.scopes.client import ContentPolicy
from lightfall.ui.dialogs.content_history_dialog import ContentHistoryDialog
from lightfall.ui.dialogs.content_publish_dialog import (
    ContentPublishDialog,
    Runner,
    error_text,
    retract_question,
    thread_runner,
    toast_success,
)
from lightfall.ui.panels.base import BasePanel, PanelMetadata
from lightfall.utils.logging import logger

_COLUMNS = ("Type", "Name", "Source", "Revision", "Shadows / note")
_TYPES = (("plan", "Plans"), ("plugin", "Plugins"), ("agent", "Agents"), ("skill", "Skills"))
_COPYABLE_UNSHARED = {"agent", "skill"}  # core/package agents and skills have files to copy

ScopeState = tuple[Sequence[str], Mapping[str, str | None], ContentPolicy]


def _default_client() -> Any:
    from lightfall.scopes.client import ScopesClient

    return ScopesClient()


class ContentPanel(BasePanel):
    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="shared_content",
        name="Shared content",
        description="Plans, plugins, agents and skills: where each comes from; publish, "
                    "retract, view history or copy shared items",
        icon="mdi.share-variant-outline",
        category="development",
        keywords=["publish", "scope", "plans", "plugins", "agents", "skills", "shared"],
        singleton=True,
        default_area="left",
        proactive_init=False,
    )

    def __init__(self, parent: QWidget | None = None, *,
                 rows: Callable[[], list[ContentRow]] | None = None,
                 scope_state: Callable[[], ScopeState] | None = None,
                 local_items: Callable[[], list[LocalItem]] | None = None,
                 client_factory: Callable[[], Any] | None = None,
                 run: Runner | None = None,
                 refresh_signals: Sequence[Any] | None = None) -> None:
        # Assigned before super().__init__ because BasePanel calls _setup_ui().
        self._rows_provider = rows or actions.content_rows
        self._scope_state = scope_state or actions.current_scope_state
        self._local_items = local_items or _local_items
        self._client_factory = client_factory or _default_client
        self._run: Runner = run or thread_runner
        self._all: list[ContentRow] = []
        self._retracted: list[ContentRow] = []  # "Show retracted" rows of the picked scope
        self._retracted_gen = 0  # answers to older fetches are dropped
        self._shown: list[ContentRow] = []
        self._dialogs: list[Any] = []
        super().__init__(parent)
        signals = refresh_signals if refresh_signals is not None else _live_signals()
        for sig in signals:
            sig.connect(self._on_changed)
        self.refresh()

    # ------------------------------------------------------------------ setup

    def _setup_ui(self) -> None:
        bar = QHBoxLayout()
        self.type_filter = QComboBox()
        self.type_filter.addItem("All types", None)
        for key, label in _TYPES:
            self.type_filter.addItem(label, key)
        self.type_filter.currentIndexChanged.connect(self._fill)
        bar.addWidget(self.type_filter)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._fill)
        bar.addWidget(self.search, 1)
        self.retracted_check = QCheckBox("Show retracted")
        self.retracted_check.setToolTip("Also list the items retracted at a shared scope")
        self.retracted_check.toggled.connect(self._on_retracted_toggled)
        bar.addWidget(self.retracted_check)
        self.retracted_scope = QComboBox()
        self.retracted_scope.setToolTip("Scope whose retracted items are listed")
        self.retracted_scope.setVisible(False)
        self.retracted_scope.currentIndexChanged.connect(self._on_retracted_scope)
        bar.addWidget(self.retracted_scope)
        self._layout.addLayout(bar)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(list(_COLUMNS))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self._layout.addWidget(self.table, 1)
        self.retracted_note = QLabel()
        self.retracted_note.setWordWrap(True)
        self.retracted_note.setVisible(False)
        self._layout.addWidget(self.retracted_note)

        buttons = QHBoxLayout()
        self.publish_btn = self._button("Publish…", self._on_publish, buttons)
        self.history_btn = self._button("History…", self._on_history, buttons)
        self.retract_btn = self._button("Retract", self._on_retract, buttons)
        self.copy_btn = self._button("Copy to mine", self._on_copy, buttons)
        self.open_btn = self._button("Open folder", self._on_open, buttons)
        buttons.addStretch()
        self._layout.addLayout(buttons)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self._layout.addWidget(self.status_label)
        self._update_buttons()

    def _button(self, text: str, slot: Callable[[], None], layout: QHBoxLayout) -> QPushButton:
        btn = QPushButton(text)
        btn.clicked.connect(lambda *_: slot())
        layout.addWidget(btn)
        return btn

    # ------------------------------------------------------------------ rows

    def refresh(self) -> None:
        try:
            self._all = list(self._rows_provider())
        except Exception as e:  # a failing loader must not break the panel
            logger.warning("Could not list shared content: {}", e)
            self._all = []
        self._sync_retracted_scopes()
        self._fill()
        if self.retracted_check.isChecked():
            self._load_retracted()

    def _on_changed(self, *_: Any) -> None:
        self.refresh()

    # ------------------------------------------------------------------ retracted

    def _sync_retracted_scopes(self) -> None:
        """Offer the chain's shared scopes in the picker, keeping the current choice."""
        chain, _, _ = self._scope_state()
        scopes = self._shared_scopes(chain)
        combo = self.retracted_scope
        if scopes != [combo.itemData(i) for i in range(combo.count())]:
            keep = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for s in scopes:
                combo.addItem(describe_source(s), s)
            combo.setCurrentIndex(scopes.index(keep) if keep in scopes else 0)
            combo.blockSignals(False)
        self.retracted_check.setEnabled(bool(scopes))
        if not scopes and self.retracted_check.isChecked():
            self.retracted_check.setChecked(False)  # scoped settings went off

    def _on_retracted_toggled(self, on: bool) -> None:
        self.retracted_scope.setVisible(on)
        if on:
            self._load_retracted()
            return
        self._retracted_gen += 1  # an answer still on its way is dropped
        self._retracted = []
        self._set_retracted_note("")
        self._fill()

    def _on_retracted_scope(self, *_: Any) -> None:
        if self.retracted_check.isChecked():
            self._load_retracted()

    def _set_retracted_note(self, text: str) -> None:
        self.retracted_note.setText(text)
        self.retracted_note.setVisible(bool(text))

    def _load_retracted(self) -> None:
        """Fetch the picked scope's retracted items on the worker, then show them."""
        scope = self.retracted_scope.currentData()
        self._retracted_gen += 1
        gen = self._retracted_gen
        if any(r.source != scope for r in self._retracted):
            self._retracted = []  # another scope's rows: don't show them meanwhile
            self._fill()
        if scope is None:
            self._set_retracted_note("")
            return
        label = describe_source(scope)
        client = self._client_factory()
        if not self._retracted:
            self._set_retracted_note(f"Looking for items retracted at {label}…")

        def work() -> list[ContentRow] | None:
            return actions.retracted_rows(client, scope)

        # The panel may be closed (deleted), or another fetch started, by then.
        def current() -> bool:
            return (isValid(self) and isValid(self.table) and isValid(self.retracted_note)
                    and gen == self._retracted_gen)

        def ok(rows: list[ContentRow] | None) -> None:
            if not current():
                return
            if rows is None:
                self._retracted = []
                self._set_retracted_note("This server doesn't list retracted items.")
            else:
                self._retracted = list(rows)
                self._set_retracted_note("" if rows else f"Nothing is retracted at {label}.")
            self._fill()

        def err(exc: Exception) -> None:
            text = error_text(exc, scope)
            if not current():
                logger.debug("Listing retracted items at {} failed: {}", scope, text)
                return
            self._retracted = []
            self._set_retracted_note(f"Could not list retracted items: {text}")
            self._fill()

        self._run(work, ok, err)

    # ------------------------------------------------------------------ table

    def _fill(self, *_: Any) -> None:
        selected = self._selected_key()
        type_ = self.type_filter.currentData()
        needle = self.search.text().strip().lower()
        self._shown = [
            r for r in self._all + self._retracted
            if (type_ is None or r.type == type_)
            and (not needle or needle in r.name.lower()
                 or needle in describe_source(r.source).lower())
        ]
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.setRowCount(len(self._shown))
        for i, r in enumerate(self._shown):
            note = ", ".join(describe_source(s) for s in r.shadows)
            if r.shadows:
                note = f"Replaces {note}"
            if r.error:
                note = f"Error: {r.error}" + (f" ({note})" if note else "")
            if r.retracted:
                note = "Retracted"
            cells = (r.type, r.name, describe_source(r.source),
                     "" if r.revision is None else str(r.revision), note)
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if r.error:
                    item.setToolTip(r.error)
                self.table.setItem(i, c, item)
        if selected is not None:
            for i, r in enumerate(self._shown):
                if (r.type, r.name, r.source, r.retracted) == selected:
                    self.table.selectRow(i)
                    break
        self.table.blockSignals(False)
        self._update_buttons()

    def _selected_key(self) -> tuple[str, str, str, bool] | None:
        row = self._selected()
        return (row.type, row.name, row.source, row.retracted) if row else None

    def _selected(self) -> ContentRow | None:
        model = self.table.selectionModel()
        idx = model.selectedRows() if model is not None else []
        if not idx or idx[0].row() >= len(self._shown):
            return None
        return self._shown[idx[0].row()]

    # ------------------------------------------------------------------ buttons

    def _update_buttons(self) -> None:
        row = self._selected()
        chain, roles, _ = self._scope_state()
        synced = row is not None and _is_shared_scope(row.source)
        is_local = row is not None and row.source == "local"
        publishable = actions.publishable_scopes(chain, roles)

        tip = ""
        can_publish = False
        if is_local:
            if not chain:
                tip = "Scoped settings are off"
            elif not publishable:
                tip = "No scope where you can publish"
            elif self._local_item(row) is None:
                tip = "This item's name can't be published"
            else:
                can_publish = True
        self.publish_btn.setEnabled(can_publish)
        self.publish_btn.setToolTip(tip)

        # Your own copy hides the shared one it replaces (and a retracted one is gone
        # from the list), so History is offered on Mine rows too: you pick the scope.
        can_history = synced or (is_local and bool(self._shared_scopes(chain)))
        self.history_btn.setEnabled(can_history)
        self.history_btn.setToolTip(
            "Scoped settings are off" if is_local and not can_history else "")
        retracted = row is not None and row.retracted
        can_retract = synced and not retracted and actions._role_at_least(
            roles.get(row.source), actions.PUBLISH_ROLE)
        self.retract_btn.setEnabled(can_retract)
        if retracted:
            retract_tip = "Already retracted; restore it from History"
        elif can_retract or not synced:
            retract_tip = ""
        else:
            retract_tip = f"You are not a maintainer of {describe_source(row.source)}"
        self.retract_btn.setToolTip(retract_tip)

        has_file = row is not None and row.path is not None and row.path.exists()
        copyable = has_file and (synced or (row.source in ("core", "package")
                                            and row.type in _COPYABLE_UNSHARED))
        self.copy_btn.setEnabled(copyable)
        self.open_btn.setEnabled(has_file)

    def _local_item(self, row: ContentRow) -> LocalItem | None:
        try:
            for item in self._local_items():
                if (item.type, item.name) == (row.type, row.name):
                    return item
        except Exception as e:
            logger.warning("Could not list local content: {}", e)
        return None

    def _keep(self, dialog: Any) -> None:
        self._dialogs.append(dialog)
        dialog.finished.connect(lambda *_: self._dialogs.remove(dialog))
        dialog.open()

    def _on_publish(self) -> None:
        row = self._selected()
        item = self._local_item(row) if row else None
        if item is None:
            return
        chain, roles, policy = self._scope_state()
        scopes = actions.publishable_scopes(chain, roles)
        if not scopes:
            return
        dlg = ContentPublishDialog(item, scopes, roles, policy, client=self._client_factory(),
                                   run=self._run, parent=self)
        dlg.finished.connect(lambda *_: self.refresh())
        self._keep(dlg)

    @staticmethod
    def _shared_scopes(chain: Sequence[str]) -> list[str]:
        """Shared scopes of ``chain``, most specific first."""
        return [s for s in reversed(tuple(chain)) if _is_shared_scope(s)]

    def _pick_scope(self, row: ContentRow, chain: Sequence[str]) -> str | None:
        """Ask which shared scope's history to show for a Mine row (None: cancelled)."""
        scopes = self._shared_scopes(chain)
        if len(scopes) <= 1:
            return scopes[0] if scopes else None
        # Preselect the scope this copy replaces ("Replaces …"), most specific first.
        current = next((i for i, s in enumerate(scopes) if s in row.shadows), 0)
        labels = [describe_source(s) for s in scopes]
        label, ok = QInputDialog.getItem(
            self, "History", f"History of {row.type} '{row.name}' at:", labels, current,
            False)
        if not ok or label not in labels:
            return None
        return scopes[labels.index(label)]

    def _on_history(self) -> None:
        row = self._selected()
        if row is None:
            return
        chain, roles, _ = self._scope_state()
        if not _is_shared_scope(row.source):
            scope = self._pick_scope(row, chain)
            if scope is None:
                return
            row = ContentRow(row.type, row.name, scope, None, None, (), None)
        dlg = ContentHistoryDialog(row, roles, client=self._client_factory(), run=self._run,
                                   parent=self)
        dlg.finished.connect(lambda *_: self.refresh())
        self._keep(dlg)

    def _on_retract(self) -> None:
        row = self._selected()
        if row is None:
            return
        where = describe_source(row.source)
        answer = QMessageBox.question(self, "Retract",
                                      retract_question(row.type, row.name, row.source))
        if answer != QMessageBox.StandardButton.Yes:
            return
        client = self._client_factory()
        self.status_label.setText(f"Retracting {row.name}…")

        def work() -> Any:
            return actions.retract(client, row.source, row.type, row.name)

        # The panel may be closed (deleted) by the time the answer comes back.
        def ok(_: Any) -> None:
            toast_success("Retracted", f"{row.type} {row.name} retracted from {where}.")
            if isValid(self) and isValid(self.status_label):
                self.status_label.setText("")
                self.refresh()

        def err(exc: Exception) -> None:
            text = error_text(exc, row.source)
            if not (isValid(self) and isValid(self.status_label)):
                logger.warning("Retract of {} {} failed: {}", row.type, row.name, text)
                return
            self.status_label.setText("")
            QMessageBox.warning(self, "Retract failed", text)

        self._run(work, ok, err)

    def _on_copy(self) -> None:
        row = self._selected()
        if row is None:
            return
        try:
            try:
                dest = copy_to_mine(row, overwrite=False)
            except FileExistsError:
                answer = QMessageBox.question(
                    self, "Copy to mine", f"Replace your {row.type} '{row.name}'?")
                if answer != QMessageBox.StandardButton.Yes:
                    return
                dest = copy_to_mine(row, overwrite=True)
        except Exception as e:
            logger.warning("Copy to mine failed: {}", e)
            QMessageBox.warning(self, "Copy failed", str(e))
            return
        toast_success("Copied", f"{row.type} {row.name} copied to {dest}.")
        self.refresh()

    def _on_open(self) -> None:
        row = self._selected()
        if row is None or row.path is None:
            return
        folder = row.path if row.path.is_dir() else row.path.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(folder))))


def _local_items() -> list[LocalItem]:
    return local_items()


def _live_signals() -> list[Any]:
    """Change signals of services that already exist (none is created to be listened to)."""
    found: list[Any] = []

    def add(getter: Callable[[], QObject | None], name: str) -> None:
        try:
            obj = getter()
            if obj is not None:
                found.append(getattr(obj, name))
        except Exception as e:
            logger.debug("No {} to follow: {}", name, e)

    def sync() -> Any:
        from lightfall.content.service import ContentSync
        from lightfall.core.services import ServiceRegistry

        return ServiceRegistry.get_instance().get(ContentSync, None)

    def plans() -> Any:
        from lightfall.acquire.plans.user_plans import UserPlanService

        return UserPlanService._instance

    def agents() -> Any:
        from lightfall.agents.registry import AgentSpecRegistry

        return AgentSpecRegistry._instance

    def prefs() -> Any:
        from lightfall.ui.preferences.manager import PreferencesManager

        return PreferencesManager._instance

    add(sync, "synced")
    add(plans, "plans_refreshed")
    add(agents, "changed")
    add(prefs, "scopes_changed")
    return found
