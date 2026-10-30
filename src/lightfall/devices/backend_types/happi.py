"""Happi backend type: a happi JSON database (or happi's default config)."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QPushButton, QWidget

from lightfall.devices.backend_types.base import BackendEditor, BackendType, BuildContext, hint
from lightfall.devices.backends.happi import HappiBackend


class _HappiEditor(BackendEditor):
    def __init__(self, config: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        form = QFormLayout(self)
        self._path = QLineEdit(str(config.get("path", "")))
        self._path.setPlaceholderText("Path to happi JSON db (empty: $HAPPI_CFG)")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        path_row = QHBoxLayout()
        path_row.addWidget(self._path)
        path_row.addWidget(browse)
        self._path.textChanged.connect(lambda *_: self.changed.emit())
        form.addRow("Database path:", path_row)
        form.addRow(
            hint(
                "Loads devices from a happi device database. "
                "Supports JSON file backends and happi's default configuration."
            )
        )

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select happi database", self._path.text(), "JSON (*.json);;All files (*)"
        )
        if path:
            self._path.setText(path)

    def get_config(self) -> dict[str, Any]:
        return {"path": self._path.text().strip()}


class HappiBackendType(BackendType):
    type_id = "happi"
    display_name = "Happi"

    def default_config(self) -> dict[str, Any]:
        return {"path": ""}

    def summary(self, config: dict[str, Any]) -> str:
        return config.get("path") or "happi default config ($HAPPI_CFG)"

    def create_editor(self, config: dict[str, Any], parent: QWidget | None = None) -> BackendEditor:
        return _HappiEditor(config, parent)

    def build(self, config: dict[str, Any], name: str, ctx: BuildContext) -> HappiBackend:
        return HappiBackend(
            path=config.get("path") or None,
            beamline=None,  # a console's happi file is its own: no beamline filter
            instantiate=ctx.instantiate_mode,
            connection_timeout=ctx.connection_timeout,
            name=name,
        )
