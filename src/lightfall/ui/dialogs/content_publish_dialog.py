"""Publish a local plan, plugin, agent or skill to a shared scope.

The bundle is built once, on a worker, when the dialog opens; what the dialog shows
(file count, sizes, sha prefix) is exactly what Publish uploads. Building, git and the
network all run through the injected ``run``; the callbacks only touch widgets, and only
while the dialog still exists.
"""
from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from lightfall.content import actions
from lightfall.content.labels import describe_source
from lightfall.content.publish import Bundle, LocalItem, _human, build_bundle, policy_problems
from lightfall.scopes.client import ContentPolicy, ScopesError, describe_error
from lightfall.ui.dialogs.base import LFDialog
from lightfall.ui.theme import scaled_px
from lightfall.utils.logging import logger

Runner = Callable[[Callable[[], Any], Callable[[Any], None], Callable[[Exception], None]], None]


def thread_runner(fn: Callable[[], Any], ok: Callable[[Any], None],
                  err: Callable[[Exception], None]) -> None:
    """Run ``fn`` on a ``QThreadFuture``; ``ok``/``err`` come back on the Qt thread."""
    from lightfall.utils.threads import QThreadFuture

    QThreadFuture(fn, callback_slot=ok, except_slot=err, name="content-publish",
                  log_exceptions=False).start()


def error_text(exc: Exception, scope: str = "") -> str:
    return describe_error(exc, scope=scope) if isinstance(exc, ScopesError) else str(exc)


_LISTED_FILES = 50


def _file_list(files: Sequence[str]) -> str:
    """The bundle's files, one per line, capped at 50 with "and N more"."""
    lines = ["Files in the bundle:", *files[:_LISTED_FILES]]
    if len(files) > _LISTED_FILES:
        lines.append(f"and {len(files) - _LISTED_FILES:,} more")
    return "\n".join(lines)


def retract_question(type_: str, name: str, scope: str) -> str:
    """The one confirmation text for Retract (panel and History dialog)."""
    return (f"Retract {type_} '{name}' from {describe_source(scope)}?\n"
            "Everyone there stops receiving it; history keeps it.")


def toast_success(title: str, text: str) -> None:
    try:
        from lightfall.ui.toast import ToastManager

        ToastManager.get_instance().success(title, text)
    except Exception:  # no main window (tests, early startup)
        logger.debug("No toast for {}", title)


class ContentPublishDialog(LFDialog):
    def __init__(self, item: LocalItem, scopes: Sequence[str], roles: Mapping[str, str | None],
                 policy: ContentPolicy, *, client: Any, run: Runner | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self._item = item
        self._roles = dict(roles)
        self._policy = policy
        self._client = client
        self._run: Runner = run or thread_runner
        self._bundle: Bundle | None = None
        self._busy = False
        self._alive = True
        self._tmp = Path(tempfile.mkdtemp(prefix="lightfall-publish-"))

        self.setWindowTitle(f"Publish {item.type} {item.name}")
        self.setModal(True)
        self.setMinimumWidth(480)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._setup_ui(list(scopes))
        self.finished.connect(self._on_finished)
        self._refresh()
        # Lambdas, not bound methods: Qt drops a signal to a deleted (WA_DeleteOnClose)
        # dialog's slot, and the build result must still reach the cleanup.
        self._run(self._build, lambda b: self._on_built(b),
                  lambda exc: self._on_build_failed(exc))

    # -- UI ----------------------------------------------------------------------------

    def _setup_ui(self, scopes: list[str]) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 24, 24, 24)
        header = QLabel(f"Publish {self._item.type} {self._item.name}")
        header.setStyleSheet(f"font-size: {scaled_px(16)}px; font-weight: bold;")
        layout.addWidget(header)

        form = QFormLayout()
        form.setSpacing(8)
        self._type_label = QLabel(f"{self._item.type}: {self._item.name}")
        form.addRow("Item:", self._type_label)
        self._facts_label = QLabel("Building bundle...")
        self._facts_label.setWordWrap(True)
        form.addRow("Bundle:", self._facts_label)

        self._scope_combo = QComboBox()
        for s in scopes:
            self._scope_combo.addItem(describe_source(s), s)
        self._scope_combo.currentIndexChanged.connect(self._refresh)
        form.addRow("Publish to:", self._scope_combo)

        self._message_edit = QLineEdit()
        self._message_edit.setPlaceholderText("What changed (required)")
        self._message_edit.textChanged.connect(self._refresh)
        form.addRow("Message:", self._message_edit)

        self._other_check = QCheckBox("Publishing someone else's work")
        self._other_check.toggled.connect(self._refresh)
        form.addRow("", self._other_check)
        self._author_edit = QLineEdit()
        self._author_edit.setPlaceholderText("Author")
        self._author_edit.setEnabled(False)
        self._author_edit.textChanged.connect(self._refresh)
        form.addRow("Author:", self._author_edit)
        layout.addLayout(form)

        self._problems_label = QLabel()
        self._problems_label.setWordWrap(True)
        self._problems_label.setStyleSheet(f"color: #b26a00; font-size: {scaled_px(12)}px;")
        self._problems_label.setVisible(False)
        layout.addWidget(self._problems_label)

        self._error_label = QLabel()
        self._error_label.setStyleSheet(f"color: #cc0000; font-size: {scaled_px(12)}px;")
        self._error_label.setWordWrap(True)
        self._error_label.setVisible(False)
        layout.addWidget(self._error_label)

        row = QHBoxLayout()
        row.addStretch()
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.close)
        row.addWidget(self._cancel_btn)
        self._publish_btn = QPushButton("Publish")
        self._publish_btn.setDefault(True)
        self._publish_btn.clicked.connect(self._on_publish)
        row.addWidget(self._publish_btn)
        layout.addLayout(row)

    # -- state -------------------------------------------------------------------------

    def _scope(self) -> str:
        return self._scope_combo.currentData() or ""

    def _waived(self) -> bool:
        return actions.is_admin(self._scope(), self._roles)

    def _problem_lines(self) -> list[str]:
        if self._bundle is None:
            return []
        lines = policy_problems(self._bundle, self._policy)
        if lines and self._waived():
            lines = [f"{line} (admin: limit waived)" for line in lines]
        return lines

    def _blocked(self) -> bool:
        if self._bundle is None:
            return False
        return bool(policy_problems(self._bundle, self._policy)) and not self._waived()

    def _refresh(self, *_: Any) -> None:
        self._author_edit.setEnabled(self._other_check.isChecked() and not self._busy)
        lines = self._problem_lines()
        self._problems_label.setText("\n".join(lines))
        self._problems_label.setVisible(bool(lines))
        needs_author = self._other_check.isChecked() and not self._author_edit.text().strip()
        self._publish_btn.setEnabled(
            self._bundle is not None and not self._busy and bool(self._scope())
            and bool(self._message_edit.text().strip()) and not needs_author
            and not self._blocked())

    def _show_error(self, text: str) -> None:
        self._error_label.setText(text)
        self._error_label.setVisible(True)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        # An upload can't be called back: no Cancel (or Escape/close) until it answers.
        for w in (self._scope_combo, self._message_edit, self._other_check, self._cancel_btn):
            w.setEnabled(not busy)
        self._refresh()

    def reject(self) -> None:  # Escape and Cancel
        if not self._busy:
            super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._busy:
            event.ignore()
            return
        super().closeEvent(event)

    # -- build -------------------------------------------------------------------------

    def _build(self) -> Bundle:
        # make_dir=False: if the dialog closed (and removed the folder) first, fail
        # rather than recreate a folder nobody will remove.
        return build_bundle(self._item, self._tmp, make_dir=False)

    def _on_built(self, bundle: Bundle) -> None:
        if not self._alive:
            shutil.rmtree(self._tmp, ignore_errors=True)
            return
        self._bundle = bundle
        self._facts_label.setText(
            f"{bundle.file_count:,} file(s), {_human(bundle.size)} compressed, "
            f"{_human(bundle.uncompressed_size)} uncompressed, sha256 {bundle.sha256[:12]}")
        self._facts_label.setToolTip(_file_list(bundle.files))
        self._refresh()

    def _on_build_failed(self, exc: Exception) -> None:
        if not self._alive:
            shutil.rmtree(self._tmp, ignore_errors=True)
            return
        self._facts_label.setText("Could not build the bundle.")
        self._show_error(error_text(exc))

    # -- publish -----------------------------------------------------------------------

    def _on_publish(self) -> None:
        bundle, scope = self._bundle, self._scope()
        if bundle is None or not scope:
            return
        message = self._message_edit.text().strip()
        author = self._author_edit.text().strip() if self._other_check.isChecked() else None
        self._error_label.setVisible(False)
        self._set_busy(True)

        def work() -> dict[str, Any]:
            return actions.publish(self._client, self._item, bundle, scope, message=message,
                                   author=author or None)

        self._run(work, lambda rec: self._on_published(scope, rec),
                  lambda exc: self._on_failed(scope, exc))

    def _on_published(self, scope: str, record: dict[str, Any]) -> None:
        self._busy = False
        rev = record.get("revision") if isinstance(record, dict) else None
        # Reported even when the dialog is already gone: the upload happened.
        toast_success("Published", f"Published {self._item.name} to {describe_source(scope)}"
                      + (f" (revision {rev})" if rev is not None else ""))
        if not self._alive:
            shutil.rmtree(self._tmp, ignore_errors=True)
            return
        self.accept()

    def _on_failed(self, scope: str, exc: Exception) -> None:
        self._busy = False
        if not self._alive:
            shutil.rmtree(self._tmp, ignore_errors=True)
            return
        self._set_busy(False)
        self._show_error(error_text(exc, scope))

    # -- lifecycle ---------------------------------------------------------------------

    def _on_finished(self, *_: Any) -> None:
        self._alive = False
        # An upload may still be reading the bundle; its callback removes the folder then.
        if not self._busy:
            shutil.rmtree(self._tmp, ignore_errors=True)
