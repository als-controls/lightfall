"""Backend selector + Gemini (agy) controls for Settings → Assistant.

Status and model lists come from ``BackendInfo`` on a ``QThreadFuture`` (agy's
check runs ``agy models``, up to 30 s). Each check carries a generation number;
results for a superseded selection, or after the page is gone, are dropped.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable

import shiboken6
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
)

from lightfall.agents.backends.info import backend_info
from lightfall.agents.backends.select import AGY, CLAUDE_CODE
from lightfall.ui.preferences.manager import PreferencesManager
from lightfall.utils.logging import logger
from lightfall.utils.threads import QThreadFuture

_KINDS = ((CLAUDE_CODE, "Claude Code"), (AGY, "Gemini (agy)"))


class AgentBackendSection:
    def __init__(
        self,
        info_factory: Callable | None = None,
        spawn: Callable = subprocess.Popen,
        poll_interval_ms: int = 5000,
        poll_timeout_s: float = 300,
    ) -> None:
        # Looked up at call time (not bound as a default) so tests can patch backend_info.
        self._info_factory = info_factory or (
            lambda kind, agy_path=None: backend_info(kind, agy_path=agy_path)
        )
        self._spawn = spawn
        self._poll_interval_ms = poll_interval_ms
        self._poll_timeout_s = poll_timeout_s
        self.on_kind_changed: Callable[[str], None] | None = None
        self._kind_combo: QComboBox | None = None
        self._status: QLabel | None = None
        self._gemini_group: QGroupBox | None = None
        self._agy_path: QLineEdit | None = None
        self._project: QLineEdit | None = None
        self._location: QLineEdit | None = None
        self._model: QComboBox | None = None
        self._future: QThreadFuture | None = None
        self._generation = 0
        self._poll_timer: QTimer | None = None
        self._poll_deadline = 0.0

    # --- widgets -------------------------------------------------------------

    def create_backend_group(self) -> QGroupBox:
        group = QGroupBox("Backend")
        layout = QFormLayout(group)
        self._kind_combo = QComboBox()
        self._kind_combo.setObjectName("assistantBackend")
        for kind, label in _KINDS:
            self._kind_combo.addItem(label, kind)
        self._kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        layout.addRow("Assistant backend:", self._kind_combo)

        row = QHBoxLayout()
        self._status = QLabel("")
        self._status.setObjectName("assistantBackendStatus")
        self._status.setWordWrap(True)
        row.addWidget(self._status, 1)
        recheck = QToolButton()
        recheck.setText("⟳")
        recheck.setToolTip("Check again")
        recheck.clicked.connect(lambda: self.refresh_status(refresh=True))
        row.addWidget(recheck)
        layout.addRow("Status:", row)
        self._poll_timer = QTimer(group)  # dies with the page
        self._poll_timer.setInterval(self._poll_interval_ms)
        self._poll_timer.timeout.connect(self._on_poll)
        return group

    def create_gemini_group(self) -> QGroupBox:
        group = QGroupBox("Gemini (agy)")
        group.setObjectName("assistantGeminiGroup")
        layout = QFormLayout(group)

        path_row = QHBoxLayout()
        self._agy_path = QLineEdit()
        self._agy_path.setObjectName("agyPath")
        self._agy_path.setPlaceholderText("auto (PATH or %LOCALAPPDATA%\\agy\\bin)")
        path_row.addWidget(self._agy_path, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._on_browse)
        path_row.addWidget(browse)
        layout.addRow("agy executable:", path_row)

        self._project = QLineEdit()
        self._project.setObjectName("agyProject")
        layout.addRow("GCP project:", self._project)
        self._location = QLineEdit()
        self._location.setObjectName("agyLocation")
        layout.addRow("GCP location:", self._location)

        self._model = QComboBox()
        self._model.setObjectName("agyModel")
        self._model.setEditable(True)
        self._model.addItem("Default (agy)", "")
        layout.addRow("Model:", self._model)

        sign_in = QPushButton("Sign in…")
        sign_in.setObjectName("agySignIn")
        sign_in.setToolTip("Open agy's own Google sign-in in a console window")
        sign_in.clicked.connect(self._on_sign_in)
        layout.addRow("", sign_in)
        self._gemini_group = group
        return group

    # --- state ---------------------------------------------------------------

    def selected_kind(self) -> str:
        return self._kind_combo.currentData() if self._kind_combo else CLAUDE_CODE

    def _alive(self) -> bool:
        return self._status is not None and shiboken6.isValid(self._status)

    def _path_text(self) -> str | None:
        return (self._agy_path.text().strip() or None) if self._agy_path else None

    def _info(self, kind: str):
        return self._info_factory(kind, agy_path=self._path_text())

    def _model_id(self) -> str:
        if self._model is None:
            return ""
        idx = self._model.findText(self._model.currentText())
        if idx >= 0 and self._model.itemData(idx) is not None:
            return self._model.itemData(idx)
        return self._model.currentText().strip()

    def load(self) -> None:
        from lightfall.ui.preferences.claude_settings import ClaudeSettingsProvider as p

        if self._agy_path is not None:
            self._agy_path.setText(p.get_agy_path())
            self._project.setText(p.get_agy_project())
            self._location.setText(p.get_agy_location())
            self._set_model_items([], p.get_agy_model())
        if self._kind_combo is not None:
            self._kind_combo.blockSignals(True)
            self._kind_combo.setCurrentIndex(max(0, self._kind_combo.findData(p.get_agent_backend())))
            self._kind_combo.blockSignals(False)
        self._apply_kind()

    def save(self) -> None:
        prefs = PreferencesManager.get_instance()
        prefs.set("agent_backend", self.selected_kind())
        if self._agy_path is not None:
            prefs.set("agy_path", self._agy_path.text().strip())
            prefs.set("agy_gcp_project", self._project.text().strip())
            prefs.set("agy_gcp_location", self._location.text().strip())
            prefs.set("agy_model", self._model_id())

    def _set_model_items(self, models, current: str) -> None:
        self._model.blockSignals(True)
        self._model.clear()
        self._model.addItem("Default (agy)", "")
        for m in models:
            if m.id:
                self._model.addItem(m.label, m.id)
        idx = self._model.findData(current)
        if idx >= 0:
            self._model.setCurrentIndex(idx)
        else:
            self._model.setEditText(current)
        self._model.blockSignals(False)

    def _on_kind_changed(self, _index: int) -> None:
        self._apply_kind()

    def _apply_kind(self) -> None:
        kind = self.selected_kind()
        if self._gemini_group is not None:
            self._gemini_group.setVisible(kind == AGY)
        if self.on_kind_changed is not None:
            self.on_kind_changed(kind)
        self.refresh_status()

    # --- async status --------------------------------------------------------

    def refresh_status(self, refresh: bool = False) -> None:
        if not self._alive():
            return
        self._status.setText("Checking…")
        self._status.setStyleSheet("color: gray; font-style: italic;")
        self._generation += 1
        gen = self._generation
        info = self._info(self.selected_kind())

        def work():
            status = info.auth_status(refresh=refresh)
            models = None
            if status.ok and info.kind == AGY:
                try:
                    models = info.list_models()
                except Exception as exc:  # noqa: BLE001 - list is optional
                    logger.info("agy model list failed: {}", exc)
            return status, models

        self._future = QThreadFuture(
            work,
            callback_slot=lambda result, g=gen: self._on_status(result) if g == self._generation else None,
            except_slot=lambda exc, g=gen: self._on_status_error(exc) if g == self._generation else None,
            name="assistant-backend-check", log_exceptions=False,
        )
        self._future.start()

    def _on_status(self, result) -> None:
        if not self._alive():
            return
        status, models = result
        self._status.setText(("✓ " if status.ok else "") + status.detail)
        self._status.setStyleSheet(
            "color: green; font-style: italic;" if status.ok else "color: orange; font-style: italic;"
        )
        if models is not None and self._model is not None and shiboken6.isValid(self._model):
            self._set_model_items(models, self._model_id())
        if status.ok and self._poll_timer is not None and self._poll_timer.isActive():
            self._poll_timer.stop()

    def _on_status_error(self, exc: Exception) -> None:
        if not self._alive():
            return
        self._status.setText(f"Couldn't check: {exc}")
        self._status.setStyleSheet("color: orange; font-style: italic;")

    # --- sign in -------------------------------------------------------------

    def _on_sign_in(self) -> None:
        info = self._info(AGY)
        argv = info.sign_in_argv()
        if argv is None:
            self._status.setText("agy (Antigravity CLI) not found — install it or set its path")
            self._status.setStyleSheet("color: orange; font-style: italic;")
            return
        kwargs: dict = {"env": {**os.environ, **info.sign_in_env()}}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
        try:
            self._spawn(argv, **kwargs)
        except OSError as exc:
            self._status.setText(f"Couldn't start agy: {exc}")
            self._status.setStyleSheet("color: red; font-style: italic;")
            return
        self._status.setText("Complete the Google sign-in in the console window, then close it…")
        self._status.setStyleSheet("color: #0066cc; font-style: italic;")
        self._poll_deadline = time.monotonic() + self._poll_timeout_s
        if self._poll_timer is not None:
            self._poll_timer.start()

    def _on_poll(self) -> None:
        if not self._alive():
            return
        if time.monotonic() > self._poll_deadline:
            self._poll_timer.stop()
            self._status.setText("Not signed in — try again")
            self._status.setStyleSheet("color: orange; font-style: italic;")
            return
        if self.selected_kind() == AGY:
            self.refresh_status(refresh=True)

    def _on_browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self._gemini_group, "Locate agy", self._agy_path.text(),
            "agy (agy.exe agy);;All files (*)",
        )
        if path:
            self._agy_path.setText(path)
            self.refresh_status(refresh=True)
