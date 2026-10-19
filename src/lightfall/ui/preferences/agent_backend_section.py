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

from lightfall.agents.backends.info import AuthStatus, ModelOption, backend_info, effort_options_for
from lightfall.agents.backends.select import AGY, CLAUDE_CODE
from lightfall.ui.preferences.manager import PreferencesManager
from lightfall.utils.logging import logger
from lightfall.utils.threads import QThreadFuture

BACKEND_KINDS = ((CLAUDE_CODE, "Claude Code"), (AGY, "Gemini (agy)"))


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
        self._effort: QComboBox | None = None
        self._models: list[ModelOption] | None = None  # None until agy lists its models
        self._future: QThreadFuture | None = None
        self._generation = 0
        self._poll_timer: QTimer | None = None
        self._poll_deadline = 0.0
        self._last_status: AuthStatus | None = None
        self._checking = False

    # --- widgets -------------------------------------------------------------

    def create_backend_group(self, *, show_selector: bool = True) -> QGroupBox:
        """``show_selector=False``: the caller shows its own selector (the setup wizard's
        segmented control) and calls ``set_kind``."""
        self.stop()  # a new page replaces the old one: its poll must not drive this one
        group = QGroupBox("Backend")
        layout = QFormLayout(group)
        self._kind_combo = QComboBox(group)
        self._kind_combo.setObjectName("assistantBackend")
        for kind, label in BACKEND_KINDS:
            self._kind_combo.addItem(label, kind)
        self._kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        if show_selector:
            layout.addRow("Assistant backend:", self._kind_combo)
        else:
            self._kind_combo.hide()

        row = QHBoxLayout()
        self._status = QLabel("")
        self._status.setObjectName("assistantBackendStatus")
        self._status.setWordWrap(True)
        row.addWidget(self._status, 1)
        recheck = QToolButton()
        recheck.setText("⟳")
        recheck.setToolTip("Check again")
        recheck.clicked.connect(self._on_recheck)
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

        self._effort = QComboBox()
        self._effort.setObjectName("agyEffort")
        self._effort.setToolTip("Reasoning effort; the levels depend on the model. "
                                "A change applies on the next turn and keeps the conversation.")
        self._effort.addItem("Default", "")
        layout.addRow("Effort:", self._effort)
        self._model.currentIndexChanged.connect(lambda _i: self._refresh_effort_items())
        self._model.editTextChanged.connect(lambda _t: self._refresh_effort_items())

        sign_in = QPushButton("Sign in…")
        sign_in.setObjectName("agySignIn")
        sign_in.setToolTip("Open agy's own Google sign-in in a console window")
        sign_in.clicked.connect(self._on_sign_in)
        layout.addRow("", sign_in)
        self._gemini_group = group
        return group

    # --- state ---------------------------------------------------------------

    def stop(self) -> None:
        """Stop sign-in polling and drop any in-flight check's result.

        Called when the Preferences dialog finishes (the page stays alive,
        hidden, so ``_alive()`` alone would keep the poll running) and when a
        new page is built.
        """
        timer = self._poll_timer
        if timer is not None and shiboken6.isValid(timer):
            timer.stop()
        self._generation += 1
        self._checking = False

    def selected_kind(self) -> str:
        return self._kind_combo.currentData() if self._kind_combo else CLAUDE_CODE

    def set_kind(self, kind: str) -> None:
        """Select a backend (for an external selector); refreshes the status."""
        if self._kind_combo is None:
            return
        index = self._kind_combo.findData(kind)
        if index >= 0 and index != self._kind_combo.currentIndex():
            self._kind_combo.setCurrentIndex(index)

    @property
    def last_status(self) -> AuthStatus | None:
        """The most recent status check result for the selected backend, if any."""
        return self._last_status

    @property
    def checking(self) -> bool:
        """True while a (non-poll) status check is in flight and its result will be used."""
        return self._checking

    def _alive(self) -> bool:
        return self._status is not None and shiboken6.isValid(self._status)

    def _path_text(self) -> str | None:
        # "" (empty field) = auto-discover; None (no widget) = use the saved pref.
        return self._agy_path.text().strip() if self._agy_path is not None else None

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
            self._models = None
            self._set_model_items([], p.get_agy_model())
            self._refresh_effort_items(p.get_agy_effort())
        if self._kind_combo is not None:
            self._kind_combo.blockSignals(True)
            self._kind_combo.setCurrentIndex(max(0, self._kind_combo.findData(p.get_agent_backend())))
            self._kind_combo.blockSignals(False)
        self._apply_kind()

    def save(self) -> None:
        if self._kind_combo is None:
            return  # page never built: leave stored prefs alone
        prefs = PreferencesManager.get_instance()
        prefs.set("agent_backend", self.selected_kind())
        if self._agy_path is not None:
            prefs.set("agy_path", self._agy_path.text().strip())
            prefs.set("agy_gcp_project", self._project.text().strip())
            prefs.set("agy_gcp_location", self._location.text().strip())
            prefs.set("agy_model", self._model_id())
            prefs.set("agy_effort", self._effort.currentData() or "")

    def _set_model_items(self, models, current: str) -> None:
        if models:
            self._models = list(models)
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
        self._refresh_effort_items()

    def _refresh_effort_items(self, keep: str | None = None) -> None:
        """Fill the effort combo with the selected model's levels.

        ``keep`` (default: the current choice) stays selected if the model offers
        it, else the choice falls back to Default. Before agy has listed its
        models the levels are unknown, so ``keep`` is kept as is.
        """
        combo = self._effort
        if combo is None or not shiboken6.isValid(combo):
            return
        if keep is None:
            keep = combo.currentData() or ""
        if self._models is None:
            options = ["", keep] if keep else [""]
        else:
            options = effort_options_for(self._models, self._model_id())
        combo.blockSignals(True)
        combo.clear()
        for level in options:
            combo.addItem(level or "Default", level)
        combo.setCurrentIndex(max(0, combo.findData(keep if keep in options else "")))
        combo.blockSignals(False)

    def _on_kind_changed(self, _index: int) -> None:
        self._apply_kind()

    def _apply_kind(self) -> None:
        kind = self.selected_kind()
        if self._gemini_group is not None:
            self._gemini_group.setVisible(kind == AGY)
        if kind != AGY and self._poll_timer is not None and shiboken6.isValid(self._poll_timer):
            self._poll_timer.stop()  # sign-in polling is agy-only
        if self.on_kind_changed is not None:
            self.on_kind_changed(kind)
        self.refresh_status()

    # --- async status --------------------------------------------------------

    def _on_recheck(self) -> None:
        """"Check again": ignored while a shown check is still running (agy
        checks take up to 30 s; repeated clicks must not stack agy runs)."""
        if self._checking and self._future is not None and self._future.isRunning():
            return
        self.refresh_status(refresh=True)

    def refresh_status(self, refresh: bool = False, from_poll: bool = False) -> None:
        """Start an async status check.

        ``from_poll`` (sign-in polling): leave the sign-in instruction on the
        label; only a successful result replaces it.
        """
        if not self._alive():
            return
        if not from_poll:
            self._last_status = None
            self._checking = True
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
            callback_slot=lambda result, g=gen, p=from_poll: (
                self._on_status(result, from_poll=p) if g == self._generation else None),
            except_slot=lambda exc, g=gen, p=from_poll: (
                self._on_status_error(exc) if g == self._generation and not p else None),
            name="assistant-backend-check", log_exceptions=False,
        )
        self._future.start()

    def _on_status(self, result, from_poll: bool = False) -> None:
        if not self._alive():
            return
        status, models = result
        self._checking = False
        if from_poll and not status.ok:
            return  # keep "Complete the Google sign-in…" while polling
        self._last_status = status
        self._status.setText(("✓ " if status.ok else "") + status.detail)
        self._status.setStyleSheet(
            "color: green; font-style: italic;" if status.ok else "color: orange; font-style: italic;"
        )
        if models is not None and self._model is not None and shiboken6.isValid(self._model):
            self._set_model_items(models, self._model_id())
        if status.ok and self._poll_timer is not None and self._poll_timer.isActive():
            self._poll_timer.stop()

    def _on_status_error(self, exc: Exception) -> None:
        self._checking = False
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
        self._generation += 1  # supersede any in-flight check so it can't overwrite our message
        self._checking = False
        kwargs: dict = {"env": {**os.environ, **info.sign_in_env()}}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
        try:
            self._spawn(argv, **kwargs)
        except Exception as exc:  # noqa: BLE001 - never escape into the Qt slot
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
        if self._future is not None and self._future.isRunning():
            return  # let the in-flight check finish; don't supersede it
        if self.selected_kind() == AGY:
            self.refresh_status(refresh=True, from_poll=True)

    def _on_browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self._gemini_group, "Locate agy", self._agy_path.text(),
            "agy (agy.exe agy);;All files (*)",
        )
        if path:
            self._agy_path.setText(path)
            self.refresh_status(refresh=True)
